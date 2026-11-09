# T3 の回答（6 件）

## Answer 1

ソースは一通り確認できたので、流れをまとめます。説明は **v1.37.2 の実際のコード**に沿っています。

## 全体像

```
kubectl / コントローラ
   │ POST /api/v1/namespaces/{ns}/pods
   ▼
kube-apiserver ── 書き込み ──▶ etcd   (この時点では spec.nodeName は空)
   │ watch (Informer)
   ▼
kube-scheduler
  イベントハンドラ → スケジューリングキュー → ScheduleOne
    ├ スケジューリングサイクル(同期): PreFilter → Filter → Score → Assume → Reserve → Permit
    └ バインディングサイクル(goroutine): WaitOnPermit → PreBind → Bind → PostBind
   │ POST .../pods/{name}/binding
   ▼
kube-apiserver: BindingREST.Create → spec.nodeName を設定 → etcd
```

---

## 1. apiserver での Pod 作成

| 処理 | 場所 |
|---|---|
| HTTP ハンドラ(デコード、admission、保存) | `staging/src/k8s.io/apiserver/pkg/endpoints/handlers/create.go:53` `createHandler` |
| 汎用 REST ストア | `staging/src/k8s.io/apiserver/pkg/registry/generic/registry/store.go:514` `(*Store).Create` |
| Pod 固有のデフォルト設定・検証 | `pkg/registry/core/pod/strategy.go:87` `PrepareForCreate`、`:124` `Validate` |
| etcd への書き込み | `staging/src/k8s.io/apiserver/pkg/storage/etcd3/store.go:284` `(*store).Create` |

保存された Pod は `spec.nodeName` が空なので、スケジューラーの処理対象になります。

## 2. スケジューラーの起動と Pod の検知

- **起動**: `cmd/kube-scheduler/app/server.go:434` `Setup` でスケジューラーを作り、`:183` `Run` で起動します。その中で `pkg/scheduler/scheduler.go:538` `(*Scheduler).Run` が呼ばれます。
  - `SchedulingQueue.Run()` を開始します。
  - `go wait.UntilWithContext(ctx, sched.ScheduleOne, 0)` で、メインループを無限に回します。
- **イベントハンドラの登録**: `pkg/scheduler/eventhandlers.go:624` `addAllEventHandlers` で、Pod Informer の `AddFunc` に `sched.addPod` を登録しています(`:642`)。
- **`addPod`** (`eventhandlers.go:129`)
  - 未割り当てで、かつこのスケジューラーの担当(`schedulerName` が一致)なら → `addPodToSchedulingQueue` (`:238`) → `SchedulingQueue.Add`
  - 割り当て済みなら → `addAssignedPodToCache` (`:401`) → `Cache.AddPod`

## 3. スケジューリングキュー

`pkg/scheduler/backend/queue/scheduling_queue.go`

- `:873` `(*PriorityQueue).Add` → `addPod` → `moveToActiveQ` (`:811`)
  - ここで PreEnqueue プラグイン(`runPreEnqueuePlugins` `:707`)が評価されます。たとえば schedulingGates が残っている Pod は activeQ に入りません。
- `:1335` `Pop` → `activeQ.pop()`。`scheduler.go:473` で `sched.NextEntity = podQueue.Pop` と設定されています。
- 失敗した Pod は backoffQ や unschedulable プールに移り、クラスタイベントや `flushBackoffQCompleted` (`:1289`) をきっかけに再投入されます。

## 4. スケジューリングサイクル(同期で実行)

`pkg/scheduler/schedule_one.go`

1. **`ScheduleOne`** (`:67`): `NextEntity` で1件取り出します。単体の Pod なら `scheduleOnePod`、PodGroup なら `scheduleOnePodGroup` に振り分けます。
2. **`scheduleOnePod`** (`:93`): プロファイルに対応する Framework を選び(`frameworkForPod`)、`CycleState` を作って `schedulingCycle` を呼びます。
   - 失敗した場合は `FailureHandler`(`handleSchedulingFailure` `:1152`)でキューに戻します。
   - 成功した場合は `go sched.runBindingCycle(...)` で、**バインドを非同期に**実行します。
3. **`schedulingCycle`** (`:169`)
   - `Cache.UpdateSnapshot` (`backend/cache/cache.go:206`): このサイクルで使うノード情報のスナップショットを更新します。
   - `schedulingAlgorithm` (`:250`) → `SchedulePod`(実体は `schedulePod` `:572`)
   - `prepareForBindingCycle` (`:196`)
4. **`schedulePod`** (`:572`)
   - **`findNodesThatFitPod`** (`:630`)
     - `RunPreFilterPlugins`(`framework/runtime/framework.go:934`)
     - `NominatedNodeName`(プリエンプションの結果)やノードヒントがあれば、そのノードを先に評価します(`evaluateNominatedNode`)。
     - `findNodesThatPassFilters` (`:779`) → `RunFilterPluginsWithNominatedPods` (`framework.go:1275`) → `RunFilterPlugins` (`:1105`)。全ノードではなく、`numFeasibleNodesToFind` (`:866`) で決めた件数が見つかった時点で打ち切ります。
     - `findNodesThatPassExtenders` (`:894`)
   - 候補が 0 件なら `FitError` を返します。このとき `schedulingAlgorithm` 側で `RunPostFilterPlugins`(プリエンプション)が動きます。
   - 候補が 1 件ならスコアリングせずにそのノードに決めます。
   - 複数件なら **`prioritizeNodes`** (`:945`) で `RunPreScorePlugins` / `RunScorePlugins` (`framework.go:1354`, `:1405`) を実行し、スコアが最も高いノードを `SuggestedHost` にします。
5. **`prepareForBindingCycle`** (`:196`)
   - `assumeAndReserve` (`:315`)
     - **`assume`** (`:1060`): `Pod.Spec.NodeName = host` を設定し、`Cache.AssumePod` (`cache.go:447`) でキャッシュ上では「配置済み」とみなします。API の応答を待たずに次の Pod のスケジューリングに進めるのはこのためです。
     - `RunReservePluginsReserve` (`framework.go:2014`)
   - `RunPermitPlugins` (`framework.go:2097`): Wait が返った場合は waiting pod として登録します。

## 5. バインディングサイクル(goroutine で実行)

`schedule_one.go:399` `bindingCycle`

1. (feature gate が有効なら)`RunPreBindPreFlights` を実行し、`NominatedNodeName` を更新します。
2. `WaitOnPermit`: Permit で待機中の場合は、ここで許可を待ちます。
3. `SchedulingQueue.Done(uid)`
4. `RunPreBindPlugins` (`framework.go:1766`): ボリュームのバインドなどを行います。
5. **`sched.bind`** (`:1100`)
   - Extender にバインダーがあればそれを使います(`extendersBinding`)。
   - なければ `RunBindPlugins` (`framework.go:1930`) → **`DefaultBinder.Bind`** (`framework/plugins/defaultbinder/default_binder.go:52`)
     - `v1.Binding{Target: Node}` を作ります。
     - APICacher があればそれ経由で送り、なければ `util.BindPod` (`pkg/scheduler/util/utils.go:111`) で `pods/binding` サブリソースに POST します。
   - `finishBinding` で `Scheduled` イベントを記録します。
6. `RunPostBindPlugins` (`framework.go:1979`)

失敗した場合は `handleBindingCycleError` (`:507`) で Unreserve と `ForgetPod` を行い、キューに戻します。

## 6. apiserver でのバインド処理

`pkg/registry/core/pod/storage/storage.go`

- `:177` `BindingREST.Create` → `ValidatePodBinding` → `assignPod` (`:290`)
- `:213` **`setPodNodeAndMetadata`**: `GuaranteedUpdate` を使い、楽観ロックで Pod を更新します。
  - 削除中の Pod、`nodeName` が設定済みの Pod、`schedulingGates` が残っている Pod はエラーにします。
  - `pod.Spec.NodeName = machine` を設定します。
  - `PodScheduled=True` の condition を付け、`NominatedNodeName` をクリアします(feature gate が有効な場合)。

## 7. その後

更新された Pod は Informer の Update イベントとしてスケジューラーに届きます。`updatePod` (`eventhandlers.go:146`) → `addAssignedPodToCache`/`updateAssignedPodInCache` で、assume していたキャッシュエントリが確定します。同時に、該当ノードの kubelet が `spec.nodeName` を watch で検知し、コンテナを起動します。

---

**ポイント**: スケジューリングサイクルは1つずつ直列に実行されますが、`assume` によってキャッシュを先に更新しておくので、バインディングサイクル(API 呼び出し)は goroutine で並列に走ります。このため、API の往復を待たずに次の Pod のスケジューリングを始められます。

なお、このバージョンには PodGroup(`scheduleOnePodGroup`)や APICacher/APIDispatcher といった新しい経路もあります。上の説明は通常の単体 Pod の経路です。

## Answer 2

Pod が作られてからノードにバインドされるまでは、大きく **① API Server への Pod 作成 → ② スケジューラーのキュー投入 → ③ スケジューリングサイクル（ノード選択） → ④ バインディングサイクル（Binding API 呼び出し） → ⑤ API Server が `spec.nodeName` を書き込む** の 5 段階です。以下の関数名と行番号は、このリポジトリ（v1.37.2）のコードで確認したものです。

## 全体像

```
kubectl create pod
  └─ kube-apiserver: Pod を nodeName 空のまま etcd に保存
        │ (watch)
        ▼
kube-scheduler
  Informer → addPod → SchedulingQueue.Add
  ScheduleOne ループ → Pop → schedulingCycle (同期: Filter/Score/Reserve/Permit)
                           → go runBindingCycle (非同期: PreBind/Bind/PostBind)
                                └─ DefaultBinder.Bind → POST pods/{name}/binding
        ▼
kube-apiserver: BindingREST.Create → spec.nodeName を設定 (PodScheduled=True)
        │ (watch)
        ├─ scheduler: updatePod → キャッシュの assumed Pod を確定
        └─ kubelet: 自ノードの Pod として起動
```

---

## ① Pod の作成（kube-apiserver）

- **`pkg/registry/core/pod/strategy.go`**
  - `podStrategy.PrepareForCreate` (L87) は、ユーザーが作成時に設定できないフィールドを消します。
  - `podStrategy.Validate` (L124) で検証します。
- Pod は `spec.nodeName` が空、つまり「未割り当て」のまま etcd に保存されます。

## ② スケジューラーが Pod を受け取ってキューに入れる

- **`cmd/kube-scheduler/app/server.go`**: `Run` (L183) が Informer を起動し (L289)、`sched.Run(ctx)` を呼びます。
- **`pkg/scheduler/eventhandlers.go`**
  - `addAllEventHandlers` (L624) で、Pod Informer に `addPod` / `updatePod` / `deletePod` を登録します (L641-645)。
  - `addPod` (L129) は、Pod が自分の担当（`responsibleForPod`: schedulerName がプロファイルに一致）かつ未割り当て（`!assignedPod`）なら `addPodToSchedulingQueue` を呼びます。
  - `addPodToSchedulingQueue` (L238) が `SchedulingQueue.Add` を呼びます。
- **`pkg/scheduler/backend/queue/scheduling_queue.go`**
  - `PriorityQueue.Add` (L873) は、PreEnqueue プラグイン（SchedulingGates など）を通った Pod を activeQ に、通らなかった Pod を unschedulable に入れます。
  - `PriorityQueue.Pop` (L1335) は activeQ から優先度順に 1 件取り出します。
  - `Run` (L508) は、backoffQ や unschedulable にいる Pod を定期的に activeQ へ戻す goroutine を起動します。

## ③ スケジューリングサイクル（同期処理、1 Pod ずつ直列）

**`pkg/scheduler/scheduler.go`**
- `Scheduler.Run` (L538) は `wait.UntilWithContext(ctx, sched.ScheduleOne, 0)` で無限ループを回します。
- `NextEntity` には `podQueue.Pop` が設定されています (L473)。

**`pkg/scheduler/schedule_one.go`**

| 関数 | 行 | 役割 |
|---|---|---|
| `ScheduleOne` | L67 | `NextEntity` でキューから取り出し、Pod なら `scheduleOnePod`、PodGroup なら `scheduleOnePodGroup` へ振り分け |
| `scheduleOnePod` | L93 | プロファイル（Framework）を選び、`CycleState` を作って `schedulingCycle` を実行。成功すると `go sched.runBindingCycle(...)` |
| `schedulingCycle` | L169 | `Cache.UpdateSnapshot` でノード情報のスナップショットを更新し、`schedulingAlgorithm` → `prepareForBindingCycle` |
| `schedulingAlgorithm` | L250 | `SchedulePod` を呼ぶ。失敗が `FitError` なら **PostFilter（プリエンプション）** を実行 |
| `schedulePod` | L572 | ノード選択の本体（下記） |
| `findNodesThatFitPod` | L630 | `RunPreFilterPlugins` → 指名ノードがあれば `evaluateNominatedNode` → `findNodesThatPassFilters` → `findNodesThatPassExtenders` |
| `findNodesThatPassFilters` | L779 | ノードごとに並列で `RunFilterPluginsWithNominatedPods` を実行。`numFeasibleNodesToFind` で調べるノード数を打ち切る |
| `prioritizeNodes` | L945 | `RunPreScorePlugins` → `RunScorePlugins`（Extender のスコアも加算） |
| `prepareForBindingCycle` | L196 | `assumeAndReserve` → `RunPermitPlugins`（Wait なら `AddWaitingPod`） |
| `assumeAndReserve` | L315 | `assume` → `RunReservePluginsReserve`。失敗したら `unreserveAndForget` |
| `assume` | L1060 | Pod のコピーに `Spec.NodeName = host` を設定し、`Cache.AssumePod` を呼ぶ |

`schedulePod` の中では、条件に合うノードが 1 つだけならそれを使います (L596)。複数あれば `prioritizeNodes` でスコアを付け、`NewSortedScoredNodes(...).Pop()` で最高スコアのノードを選びます (L609-611)。

**ポイント（assume）**: API Server での確定を待たずに、スケジューラー内部のキャッシュ上で「このノードに置いた」ことにしてしまいます。これにより、バインド完了を待たずに次の Pod のスケジューリングへ進めます。

失敗した場合は `FailureHandler`、つまり `handleSchedulingFailure` (L1152) が呼ばれます。Pod は unschedulable または backoffQ に戻され、`PodScheduled=False` の condition と、必要なら `nominatedNodeName` が書き込まれます。

## ④ バインディングサイクル（非同期 goroutine）

**`pkg/scheduler/schedule_one.go`**

| 関数 | 行 | 役割 |
|---|---|---|
| `runBindingCycle` | L145 | `bindingCycle` を実行し、失敗したら `handleBindingCycleError` |
| `bindingCycle` | L399 | `WaitOnPermit` → `SchedulingQueue.Done` → `RunPreBindPlugins`（ボリュームのバインドなど） → `bind` → `RunPostBindPlugins` |
| `bind` | L1100 | Binder 機能を持つ Extender があればそれを使い、なければ `RunBindPlugins` |
| `finishBinding` | L1132 | 成功時に `Scheduled` イベント（"Successfully assigned ..."）を記録 |
| `handleBindingCycleError` | L507 | `unreserveAndForget` で assume を取り消し、キューに戻す |

- **`pkg/scheduler/framework/runtime/framework.go`**: `RunBindPlugins` (L1930)
- **`pkg/scheduler/framework/plugins/defaultbinder/default_binder.go`**: `DefaultBinder.Bind` (L52)
  - `v1.Binding{Target: {Kind: "Node", Name: nodeName}}` を作ります。
  - APICacher（非同期 API ディスパッチャー）があればそれ経由で、なければ `util.BindPod`（`pkg/scheduler/util/utils.go` L111）経由で `pods/{name}/binding` サブリソースに POST します。

## ⑤ API Server がバインドを確定

**`pkg/registry/core/pod/storage/storage.go`**
- `BindingREST.Create` (L177) は `ValidatePodBinding` で検証してから `assignPod` を呼びます。
- `assignPod` (L290) は `setPodNodeAndMetadata` を呼びます。
- `setPodNodeAndMetadata` (L213) は `GuaranteedUpdate` で etcd を楽観的ロック付きで更新します。
  - 削除中の Pod、`nodeName` が既に入っている Pod、schedulingGates が残っている Pod は拒否します。
  - 問題なければ `pod.Spec.NodeName = machine` を設定し、`PodScheduled=True` の condition を付けます。
  - feature gate が有効なら `NominatedNodeName` もクリアします。

## ⑥ その後（確定の反映）

- スケジューラー側では、Informer 経由で `updatePod` (eventhandlers.go L146) が呼ばれます。
  - 旧オブジェクトが未割り当てで新オブジェクトが割り当て済みのとき、`addAssignedPodToCache` で assumed 状態の Pod を確定します。
  - あわせて `deletePodFromSchedulingQueue(oldPod, true)` でキューから取り除きます。
- kubelet 側では、`syncLoopIteration` から `HandlePodAdditions`（`pkg/kubelet/kubelet.go` L2746 / L2883）が呼ばれ、コンテナの起動が始まります。

---

### 補足
- 処理はスケジューリングサイクル（同期・直列）とバインディングサイクル（非同期・並列）の 2 つに分かれています。両者をつないでいるのが assume（Reserve）です。
- このバージョンには PodGroup（ギャングスケジューリング）向けの `scheduleOnePodGroup` や、OpportunisticBatching 用の `StoreScheduleResults` といった新しい経路もあります。単体 Pod の流れは上記のとおりです。

特定の段階、たとえばプリエンプションや Filter プラグインの並列実行の仕組みを詳しく知りたければ、そこを深掘りします。

## Answer 3

# Pod 作成からバインドまでの流れ（Kubernetes v1.37.2）

全体は次の 4 段階です。下の関数名と行番号は、このリポジトリのコードで確認したものです。

```
kubectl/API → kube-apiserver(etcd) → [Informer] → SchedulingQueue
   → ScheduleOne: スケジューリングサイクル(同期・1Pod ずつ)
   → go runBindingCycle: バインディングサイクル(非同期)
   → POST pods/{name}/binding → apiserver が spec.nodeName を更新
```

---

## 0. スケジューラーの起動

- `pkg/scheduler/scheduler.go:538` `Scheduler.Run()`
  - `SchedulingQueue.Run()` でキューのバックグラウンド処理を始めます（backoff から active キューへの移し替えなど）。
  - `go wait.UntilWithContext(ctx, sched.ScheduleOne, 0)` で、メインループ `ScheduleOne` を間を空けずに回し続けます。
- `scheduler.go:473` で `sched.NextEntity = podQueue.Pop`、`scheduler.go:129` で `sched.SchedulePod = sched.schedulePod` が設定されます。

## 1. Pod 作成 → スケジューリングキューに入る

1. ユーザーが Pod を作ると、apiserver が etcd に保存します。この時点では `spec.nodeName` は空です。
2. スケジューラーの Pod Informer が Add イベントを受け取ります。
   - ハンドラーの登録は `pkg/scheduler/eventhandlers.go:624` の `addAllEventHandlers()` です（`AddFunc: sched.addPod`）。
3. `eventhandlers.go:129` `addPod()` の動き:
   - 自分のプロファイルが担当する Pod（`responsibleForPod`）で、まだノードが決まっていない（`!assignedPod`）場合は、`addPodToSchedulingQueue()` に渡します。
   - ノードが決まっている Pod は `addAssignedPodToCache()` でキャッシュに反映します。
4. `eventhandlers.go:238` `addPodToSchedulingQueue()` が `SchedulingQueue.Add(...)` を呼びます。
   - 実装は `pkg/scheduler/backend/queue/scheduling_queue.go:873` `PriorityQueue.Add()` です。PreEnqueue プラグインを通ったら activeQ に入ります。
   - キューには **activeQ**（`active_queue.go`）、**backoffQ**（`backoff_queue.go`）、**unschedulable**（`unschedulable_entities.go`）の 3 つがあります。

## 2. スケジューリングサイクル（同期処理）

すべて `pkg/scheduler/schedule_one.go` の中です。

| 関数 | 行 | 役割 |
|---|---|---|
| `ScheduleOne()` | 67 | `NextEntity` で取り出します（実体は `PriorityQueue.Pop()`、`scheduling_queue.go:1335`）。Pod なら `scheduleOnePod`、PodGroup なら `scheduleOnePodGroup` に振り分けます。 |
| `scheduleOnePod()` | 93 | `frameworkForPod` でプロファイルを選び、`CycleState` を作って `schedulingCycle` を呼びます。成功したら **`go sched.runBindingCycle(...)`** を起動します。 |
| `schedulingCycle()` | 169 | 次の 3 ステップを順に実行します。 |

### 2-1. スナップショットの更新

`Cache.UpdateSnapshot()`（`pkg/scheduler/backend/cache/cache.go:206`）で、ノードの状態をこのサイクル用のスナップショットに固定します。

### 2-2. ノード選定：`schedulingAlgorithm()`（250）→ `schedulePod()`（572）

- **Filter**：`findNodesThatFitPod()`（630）
  - `RunPreFilterPlugins` を実行します（`framework/runtime/framework.go:934`）。
  - `findNodesThatPassFilters()`（779）でノードを並列に評価し、各ノードに `RunFilterPluginsWithNominatedPods` → `RunFilterPlugins` をかけます（`framework.go:1275`, `1105`）。
  - 評価するノード数は `numFeasibleNodesToFind()`（866）で上限が決まります（percentageOfNodesToScore）。
  - Extender があれば `findNodesThatPassExtenders()`（894）も通します。
- **Score**：`prioritizeNodes()`（945）
  - `RunPreScorePlugins` → `RunScorePlugins` の順に実行します（`framework.go:1354`, `1405`）。
- **ノードの決定**：`framework.NewSortedScoredNodes(priorityList).Pop()` で最高スコアのノードを選び、`ScheduleResult.SuggestedHost` に入れます（608〜614 行目）。
  - 以前のバージョンにあった `selectHost()` はもうありません。
- **どのノードにも入らない場合**：`FitError` になり、`RunPostFilterPlugins`（`framework.go:1152`）が呼ばれます。代表例は DefaultPreemption によるプリエンプションで、結果は NominatedNodeName として返されます。

### 2-3. メモリ上での確保：`prepareForBindingCycle()`（196）

- `assumeAndReserve()`（315）
  - `assume()`（1060）が `Pod.Spec.NodeName = host` を設定し、`Cache.AssumePod()`（`cache.go:447`）を呼びます。API の応答を待たずに「このノードに載った」とみなす楽観的な処理で、これにより次の Pod のスケジューリングをすぐ始められます。
  - 続いて `RunReservePluginsReserve`（`framework.go:2014`）を実行します。
- `RunPermitPlugins`（`framework.go:2097`）
  - Wait が返ったら `AddWaitingPod` で待機させます（Gang スケジューリングなどで使われます）。
  - 拒否されたら `unreserveAndForget()`（366）で Unreserve と `ForgetPod` を行い、確保を取り消します。

失敗したときは `FailureHandler`、つまり `handleSchedulingFailure()`（1152）が Pod を unschedulable キューか backoffQ に戻し、PodScheduled=False の condition を書き込みます。

## 3. バインディングサイクル（非同期・goroutine）

| 関数 | 行 | 役割 |
|---|---|---|
| `runBindingCycle()` | 145 | `bindingCycle` を呼び、失敗したら `handleBindingCycleError()`（507）を呼びます。 |
| `bindingCycle()` | 399 | 下の流れを実行します。 |

1. （feature gate が有効な場合）`RunPreBindPreFlights` を実行し、必要なら NominatedNodeName を API に書き込みます。
2. `WaitOnPermit()`（`framework.go:2251`）で、Permit の Wait が解けるまで待ちます。
3. `SchedulingQueue.Done(uid)` を呼びます。ここから先の失敗では unschedulable 扱いにならず、backoffQ からの再試行になります。
4. `RunPreBindPlugins`（`framework.go:1766`）を実行します。例：VolumeBinding による PV のバインド。
5. `bind()`（1100）
   - Binder 機能を持つ Extender があれば、`extendersBinding()`（1114）が優先されます。
   - なければ `RunBindPlugins`（`framework.go:1930`）を実行します。
   - 標準の Bind プラグインは `pkg/scheduler/framework/plugins/defaultbinder/default_binder.go:52` の `DefaultBinder.Bind()` です。`v1.Binding{Target: Node}` を作り、`APICacher().BindPod()`（APIDispatcher が有効な場合）か `util.BindPod()` で **`POST /api/v1/namespaces/{ns}/pods/{name}/binding`** を送ります。
   - `finishBinding()`（1132）が `Scheduled` イベントを記録します。
6. `RunPostBindPlugins`（`framework.go:1979`）を実行します。

失敗したときは `handleBindingCycleError()` が Unreserve と `ForgetPod` で確保を取り消し、Pod をキューに戻します。

## 4. apiserver 側でのバインド反映

`pkg/registry/core/pod/storage/storage.go`

- `BindingREST.Create()`（177）が pods/binding サブリソースへの POST を受け付けます。
- `assignPod()`（290）→ `setPodNodeAndMetadata()`（213）
  - `spec.nodeName` が空であることを確認してから設定し、`PodScheduled=True` の condition を付けて etcd を更新します。
  - すでに nodeName が入っていれば Conflict を返します。

## 5. 完了後

- 更新された Pod はスケジューラーの Informer に Update イベントとして届きます。`updatePod()` から `updateAssignedPodInCache()` が呼ばれ、assume 状態だった Pod がキャッシュ上で確定します。
- 同じイベントは対象ノードの kubelet にも届き、kubelet がコンテナを起動します。

---

**要点**：スケジューリングサイクルは 1 Pod ずつ直列に、バインディングサイクルは goroutine で並行に動きます。この 2 つを切り離せるのは、`assume` で API の更新を待たずにキャッシュ上の確保を済ませているからです。

PodGroup（Gang スケジューリング）の経路（`scheduleOnePodGroup`）を詳しく見たい場合はお知らせください。

## Answer 4

Pod の作成から Node へのバインドまでの流れを、このリポジトリ（v1.37.2）のソースで呼び出し関係を確認しながらまとめました。

## 全体の流れ

```
kubectl / Controller
   │ POST /api/v1/namespaces/{ns}/pods      (spec.nodeName は空)
   ▼
kube-apiserver ── etcd に Pod を保存
   │ watch
   ▼
kube-scheduler
   ① Informer の AddFunc → addPod → SchedulingQueue.Add          (activeQ に入る)
   ② ScheduleOne ループ → Pop                                    (キューから取り出す)
   ③ Scheduling Cycle (同期・1 Pod ずつ)
        PreFilter → Filter → (PostFilter=プリエンプション)
        → PreScore → Score → Node を選ぶ
        → Assume (キャッシュ上で仮配置) → Reserve → Permit
   ④ Binding Cycle (goroutine で非同期)
        WaitOnPermit → PreBind → Bind → PostBind
   │ POST /api/v1/namespaces/{ns}/pods/{name}/binding
   ▼
kube-apiserver ── BindingREST が spec.nodeName を設定して etcd を更新
   │ watch
   ▼
kubelet が自分の Node 宛ての Pod を起動
```

---

## 0. Pod の作成（kube-apiserver）

- Pod の作成は汎用の `genericregistry.Store` が処理し、Pod 固有の処理は `pkg/registry/core/pod/strategy.go` の `podStrategy` が受け持ちます。
  - `PrepareForCreate`（`strategy.go:87`）：status の初期化など
  - `Validate`（`strategy.go:124`）：検証
- 作成直後の Pod は `spec.nodeName` が空です。この「未割り当て」の状態がスケジューラーの処理対象になります。

## 1. Pod をスケジューリングキューに入れる

**`pkg/scheduler/eventhandlers.go`**

- `addAllEventHandlers`（`:624`）で Pod Informer に `AddFunc: sched.addPod` を登録します（`:642`）。
- `addPod`（`:129`）は、次の 2 条件を両方満たす Pod だけを `addPodToSchedulingQueue` に渡します（`:138`）。
  - `responsibleForPod`：`spec.schedulerName` がこのスケジューラーのいずれかのプロファイルと一致する
  - `!assignedPod`：`spec.nodeName` が空である
- `addPodToSchedulingQueue`（`:238`）が `sched.SchedulingQueue.Add(...)` を呼びます。

**`pkg/scheduler/backend/queue/scheduling_queue.go`**

- `PriorityQueue.Add`（`:873`）で Pod が activeQ に入ります。
- キューは activeQ / backoffQ / unschedulable の 3 段構成です。

## 2. スケジューリングループ

**`pkg/scheduler/scheduler.go`**

- `Run`（`:538`）は `SchedulingQueue.Run` でキューを起動したあと、`go wait.UntilWithContext(ctx, sched.ScheduleOne, 0)` で `ScheduleOne` を無限ループで回します。
- `sched.NextEntity = podQueue.Pop`（`:473`）と `sched.SchedulePod = sched.schedulePod`（`:129`）は、関数フィールドとして差し替えられる作りになっています。

**`pkg/scheduler/schedule_one.go`**

- `ScheduleOne`（`:67`）は `NextEntity`（実体は `PriorityQueue.Pop`, `scheduling_queue.go:1335`）で 1 件取り出し、型に応じて振り分けます。
  - 単体の Pod：`scheduleOnePod`
  - PodGroup：`scheduleOnePodGroup`
- `scheduleOnePod`（`:93`）は次の順に処理します。
  1. `frameworkForPod` でプロファイル（Framework）を決める
  2. `CycleState` を作る
  3. `schedulingCycle` を**同期で**実行する
  4. 成功したら `go sched.runBindingCycle(...)` で**バインドを非同期に**実行する（`:141`）
  5. 失敗したら `FailureHandler`（= `handleSchedulingFailure`, `:1152`）に渡す

## 3. Scheduling Cycle（Node を選ぶ）

`schedulingCycle`（`:169`）は次の 3 段階で進みます。

1. **`Cache.UpdateSnapshot`**：クラスタのキャッシュから、このサイクル用の Node 情報のスナップショットを作る
2. **`schedulingAlgorithm`**（`:250`）→ `SchedulePod` → **`schedulePod`**（`:572`）
3. **`prepareForBindingCycle`**（`:196`）

### 3-1. フィルタリング（候補 Node を絞る）

`findNodesThatFitPod`（`:630`）が担当します。

- `RunPreFilterPlugins`（`:641`、実装は `framework/runtime/framework.go:934`）
- NominatedNodeName（プリエンプション後の指名先）がある場合は、まず `evaluateNominatedNode`（`:722`）でその Node だけを試す
- `findNodesThatPassFilters`（`:779`）
  - `Parallelizer().Until` で Node ごとに並列実行し、`RunFilterPluginsWithNominatedPods`（`framework.go:1275`）→ `RunFilterPlugins`（`:1105`）を呼ぶ
  - `numFeasibleNodesToFind` により、`percentageOfNodesToScore` 分の候補が見つかった時点で探索を打ち切る
  - 探索の開始位置は `nextStartNodeIndex` で毎回ずらす
- `findNodesThatPassExtenders`（`:894`）：Scheduler Extender によるフィルタ

### 3-2. スコアリング（最良の Node を選ぶ）

- 候補が 0 件なら `FitError` を返します。このとき `schedulingAlgorithm` 内で `RunPostFilterPlugins`（`framework.go:1152`、DefaultPreemption によるプリエンプション）が動きます。
- 候補が 1 件ならスコアリングを省略してその Node に決めます。
- 2 件以上なら `prioritizeNodes`（`:945`）がスコアを計算します。
  - `RunPreScorePlugins`（`framework.go:1354`）
  - `RunScorePlugins`（`:1405`）
  - Extender の `Prioritize`
- 最後に `NewSortedScoredNodes(...).Pop()` で最高スコアの Node を `SuggestedHost` にします。

### 3-3. 仮確保（`prepareForBindingCycle`）

- **`assumeAndReserve`**（`:315`）
  - **`assume`**（`:1060`）：Pod のコピーに `Spec.NodeName = host` を設定し、`Cache.AssumePod`（`backend/cache/cache.go:447`）でスケジューラーのキャッシュ上で配置済みとして扱う。API へのバインド完了を待たずに次の Pod のスケジューリングへ進めるのは、この楽観的な仮配置があるからです。
  - `RunReservePluginsReserve`（`framework.go:2014`）：失敗したら `unreserveAndForget` で巻き戻す
- `RunPermitPlugins`（`:2097`）：結果が Wait なら `AddWaitingPod` で待機させる（Gang scheduling などで使われる）

## 4. Binding Cycle（非同期で API に反映）

`runBindingCycle`（`:145`）→ **`bindingCycle`**（`:399`）の順に進みます。

1. **`WaitOnPermit`**（`framework.go:2251`）：Permit で Wait になった Pod が許可されるのを待つ
2. `SchedulingQueue.Done(uid)`（`:455`）：これ以降に失敗しても Unschedulable にはならず、backoffQ からの再試行になる
3. **`RunPreBindPlugins`**（`framework.go:1766`）：ボリュームのバインドなど
4. **`bind`**（`:1100`）
   - Extender に Binder があれば `extendersBinding` を使う
   - なければ `RunBindPlugins`（`framework.go:1930`）を呼ぶ
5. **`RunPostBindPlugins`**（`framework.go:1979`）

途中で失敗した場合は `handleBindingCycleError`（`:507`）が Unreserve と `ForgetPod` を実行し、Pod をキューに戻します。

### Bind プラグインの実体

**`pkg/scheduler/framework/plugins/defaultbinder/default_binder.go`**

- `DefaultBinder.Bind`（`:52`）が `v1.Binding{Target: {Kind: "Node", Name: nodeName}}` を作ります。
- 送信経路は次のどちらかです。
  - APICacher が有効なら `APICacher().BindPod`（APIDispatcher 経由で非同期に送る）
  - そうでなければ `util.BindPod`（`pkg/scheduler/util/utils.go:111`）→ `cs.CoreV1().Pods(ns).Bind(...)`。再試行付きで `pods/{name}/binding` サブリソースに POST します。

## 5. kube-apiserver での Binding 処理

**`pkg/registry/core/pod/storage/storage.go`**

- `BindingREST.Create`（`:177`）：`ValidatePodBinding` で検証してから `assignPod` を呼ぶ
- `assignPod`（`:290`）→ `setPodNodeAndMetadata`（`:213`）
  - `Storage.GuaranteedUpdate` で、UID と ResourceVersion を前提条件にして etcd を更新する
  - `spec.nodeName` が**既に設定済みならエラー**にする（二重バインドの防止）
  - `pod.Spec.NodeName = machine` を設定する
  - 条件 `PodScheduled=True` を追加する
  - FeatureGate `ClearingNominatedNodeNameAfterBinding` が有効なら `NominatedNodeName` を消す
  - 競合した場合は `pods/binding` の Conflict エラーを返す

## 6. その後

- 更新された Pod は watch でスケジューラーにも届きます。`nodeName` が付いたので `assignedPod` と判定され、仮配置（assume）中の Pod がキャッシュ上で確定扱いになります。
- 同じ更新を kubelet も受け取り、自分の Node 宛ての Pod としてコンテナを起動します。

---

**押さえておきたいポイント**
- **同期の Scheduling Cycle と非同期の Binding Cycle の分離**：`assume` でキャッシュに仮配置してから `go runBindingCycle` を起動するので、API 呼び出しの待ち時間がスループットを落としません。
- **拡張ポイントの実行はすべて `framework/runtime/framework.go` の `Run*Plugins`**：個々のプラグインは `pkg/scheduler/framework/plugins/` 配下にあります（NodeResourcesFit、InterPodAffinity、DefaultPreemption、DefaultBinder など）。
- このバージョンには PodGroup（`scheduleOnePodGroup`）や OpportunisticBatching の経路もありますが、上の説明は通常の単一 Pod の経路に絞っています。

## Answer 5

Pod の作成からバインドまでを、このリポジトリ（v1.37.2）のコードで追った流れです。大きく分けると次の 4 段階です。kube-apiserver が Pod を保存し、スケジューラーが Informer 経由でキューに入れ、スケジューリングサイクルでノードを決め、バインディングサイクルで API に書き込みます。

---

## 1. kube-apiserver：Pod の作成

`kubectl apply` などから `POST /api/v1/namespaces/{ns}/pods` が届きます。

- `staging/src/k8s.io/apiserver/pkg/registry/generic/registry/store.go:514` `(*Store).Create`
  - 汎用の作成処理です。保存の前に strategy を呼びます。
  - `pkg/registry/core/pod/strategy.go:87` `PrepareForCreate`、`:124` `Validate`
- etcd に保存された直後の Pod は `spec.nodeName` が空で、まだどのノードにも割り当てられていません。

## 2. スケジューラー：Informer からキューへ

**起動時**
- `pkg/scheduler/scheduler.go:286` `New()` が `NextEntity = podQueue.Pop` を設定します（`:473`）。
- `pkg/scheduler/eventhandlers.go:624` `addAllEventHandlers` が Pod Informer に `AddFunc: sched.addPod` を登録します。
- `pkg/scheduler/scheduler.go:538` `Run()` は `SchedulingQueue.Run()` を起動したあと、`wait.UntilWithContext(ctx, sched.ScheduleOne, 0)` で `ScheduleOne` を無限ループさせます。

**Pod 作成イベントを受けたとき**
- `eventhandlers.go:129` `addPod`
  - `responsibleForPod(pod)`（`:464`、`schedulerName` が自分のプロファイルに一致するか）と `!assignedPod(pod)`（`:459`、`nodeName` が空か）の両方を満たせば、次に進みます。
  - `addPodToSchedulingQueue`（`:238`）→ `SchedulingQueue.Add()`
- `pkg/scheduler/backend/queue/scheduling_queue.go:873` `(*PriorityQueue).Add` が Pod を activeQ に積みます。activeQ は優先度順のヒープで、並び順は QueueSort プラグインで決まります。
  - activeQ のほかに、リトライ待ちの backoffQ と、条件が変わるまで待つ unschedulable のキューがあります。backoffQ からは `flushBackoffQCompleted`（`:1289`）が定期的に activeQ へ戻します。

## 3. スケジューリングサイクル（同期処理、一度に 1 Pod）

中心になるファイルは `pkg/scheduler/schedule_one.go` です。

| 順序 | 関数 | 内容 |
|---|---|---|
| ① | `ScheduleOne` (:67) | `NextEntity` で `PriorityQueue.Pop` (:1335) を呼び、次の 1 件を取り出す（キューが空ならブロック）。`QueuedPodInfo` なら次へ（PodGroup の場合は `scheduleOnePodGroup`） |
| ② | `scheduleOnePod` (:93) | `frameworkForPod` でプロファイルを選び、`skipPodSchedule`（削除中や assume 済みの Pod を除外）を確認してから `CycleState` を作る |
| ③ | `schedulingCycle` (:169) | `Cache.UpdateSnapshot` でノード情報のスナップショットを更新する（`backend/cache/cache.go:206`） |
| ④ | `schedulingAlgorithm` (:250) → `schedulePod` (:572) | ノード選択の本体 |
| ④-a | `findNodesThatFitPod` (:630) | **PreFilter** (`RunPreFilterPlugins`) を実行する。NominatedNodeName があれば、まず `evaluateNominatedNode` でそのノードを試す |
| ④-b | `findNodesThatPassFilters` (:779) | `Parallelizer().Until` で全ノードを並列に調べ、`RunFilterPluginsWithNominatedPods` で **Filter** を実行する。`numFeasibleNodesToFind` で、条件に合うノードがある数見つかった時点で打ち切る |
| ④-c | `findNodesThatPassExtenders` (:894) | Extender によるフィルタ |
| ④-d | `prioritizeNodes` (:945) | **PreScore** と **Score**（`RunPreScorePlugins` / `RunScorePlugins`）、Extender の `Prioritize` を実行する。最高スコアのノードを `SuggestedHost` にする（条件に合うノードが 1 つだけならスコアリングは省略） |
| ⑤ | 失敗時 | `FitError` になると `RunPostFilterPlugins`（**PostFilter**、プリエンプションを担う DefaultPreemption）を実行する。最後に `FailureHandler` → `handleSchedulingFailure` (:1152) で Pod をキューに戻す |
| ⑥ | `prepareForBindingCycle` (:196) → `assumeAndReserve` (:315) | `assume` (:1060) が `pod.Spec.NodeName = host` を設定し、`Cache.AssumePod` (`cache.go:447`) を呼ぶ |
| ⑦ | 同上 | **Reserve** (`RunReservePluginsReserve`) を実行する。失敗したら `unreserveAndForget` でロールバック |
| ⑧ | 同上 | **Permit** (`RunPermitPlugins`) を実行する。Wait が返った場合は `AddWaitingPod` で待機中の Pod として登録する |
| ⑨ | `scheduleOnePod` | `go sched.runBindingCycle(...)` でバインド処理を**非同期**に開始し、すぐ次の Pod の処理に移る |

ポイントは ⑥ の **assume** です。API への書き込みを待たずに、キャッシュ上では「この Pod はこのノードで動いている」として扱います。そのため、次の Pod のスケジューリングでもこの Pod の消費リソースが正しく反映され、バインドの完了を待たずに処理を進められます。

## 4. バインディングサイクル（非同期 goroutine）

`runBindingCycle` (:145) → `bindingCycle` (:399) の順に進みます。

1. （NominatedNodeNameForExpectation が有効な場合）`RunPreBindPreFlights` を実行し、必要なら `updatePod` で `status.nominatedNodeName` を書き込みます。
2. `WaitOnPermit`（`framework/runtime/framework.go:2251`）で、Permit が Wait を返した Pod の承認を待ちます。
3. `SchedulingQueue.Done(uid)` で、キュー側に残っている処理中扱いを解除します。
4. **PreBind** を実行します（`RunPreBindPlugins`、`framework.go:1766`）。たとえば VolumeBinding の PV/PVC バインドがここで行われます。
5. `bind` (:1100) を実行します。
   - Binder 機能を持つ Extender があれば、`extendersBinding` で Extender に任せます。
   - なければ **Bind** プラグイン（`RunBindPlugins`、`framework.go:1930`）を実行します。
   - `pkg/scheduler/framework/plugins/defaultbinder/default_binder.go:52` `DefaultBinder.Bind` が `v1.Binding{Target: Node}` を作ります。
   - `pkg/scheduler/util/utils.go:111` `BindPod` → `CoreV1().Pods(ns).Bind()` で、**`POST pods/{name}/binding`** を送ります（APICacher が有効なら APICacher 経由になります）。
   - `finishBinding` (:1132) が `Scheduled` イベントを記録します。
6. **PostBind** を実行します（`RunPostBindPlugins`）。

途中で失敗した場合は `handleBindingCycleError` (:507) が呼ばれます。Unreserve と `Cache.ForgetPod` で assume を取り消し、Pod をキューに戻します。

## 5. kube-apiserver：binding サブリソースの処理

- `pkg/registry/core/pod/storage/storage.go:177` `(*BindingREST).Create` が `ValidatePodBinding` で検証します。
- 続いて `assignPod` (:290) → `setPodNodeAndMetadata` (:213) が `GuaranteedUpdate` で etcd 上の Pod を楽観ロック付きで更新します。
  - `nodeName` がすでに設定されていればエラーを返します（二重バインドの防止）。
  - `pod.Spec.NodeName = machine` を設定し、`PodScheduled=True` の Condition を付けます。ClearingNominatedNodeNameAfterBinding が有効なら `nominatedNodeName` もクリアします。

## 6. 後処理

- 更新された Pod が Informer に届くと、スケジューラーの `updatePod` / `addAssignedPodToCache` (`eventhandlers.go:401`) が assume 済みの Pod を確定状態にします（`cache.AddPod`、`cache.go:609`）。
- 同じ更新を対象ノードの kubelet も watch しており、コンテナの起動が始まります。

---

### 全体の流れ（図）

```
apiserver: Store.Create ──etcd(nodeName="")
   │ watch
scheduler: addPod → PriorityQueue.Add (activeQ)
   │
ScheduleOne → Pop → scheduleOnePod
   └ schedulingCycle
       ├ UpdateSnapshot
       ├ schedulePod: PreFilter → Filter → (Extender) → PreScore → Score → 最良ノード
       │     └失敗時 PostFilter(プリエンプション) → キューに戻す
       └ assume(Cache) → Reserve → Permit
   └ go runBindingCycle
       └ WaitOnPermit → PreBind → Bind(DefaultBinder: POST pods/binding) → PostBind
apiserver: BindingREST.Create → setPodNodeAndMetadata (nodeName設定)
   │ watch
scheduler cache 確定 / kubelet が起動
```

このバージョンでは PodGroup（ギャングスケジューリング）用の `scheduleOnePodGroup` や `IsPodGroupSchedulingCycle` の分岐、OpportunisticBatching などの新しい経路も入っています。上の説明は通常の単一 Pod の経路に絞っています。

## Answer 6

Pod の作成からバインドまでは、大きく 6 段階に分かれます。以下はこのリポジトリ（v1.37.2）のソースを直接読んで確認した内容です。古いバージョンにあった `selectHost` はもうありません。代わりに `scheduleOnePod`、`prepareForBindingCycle`、`assumeAndReserve` といった関数に処理が分かれています。

## 全体像

```
kubectl → kube-apiserver (Pod を etcd に保存, nodeName は空)
        → [scheduler] Informer の Add イベント → SchedulingQueue (activeQ)
        → ScheduleOne ループで Pop
        → スケジューリングサイクル（同期処理）: Filter → Score → Assume → Reserve → Permit
        → バインディングサイクル（goroutine で非同期）: WaitOnPermit → PreBind → Bind
        → kube-apiserver の pods/binding サブリソース → Pod.Spec.NodeName を設定
```

---

## 1. API サーバーで Pod を作成する
- `pkg/registry/core/pod/strategy.go:87` の `podStrategy.PrepareForCreate` と `:124` の `Validate` を通って、Pod が etcd に保存されます。
- この時点では `spec.nodeName` は空です。

## 2. スケジューラーがイベントを受け取り、キューに入れる
- `pkg/scheduler/eventhandlers.go:624` の `addAllEventHandlers` が、Pod Informer にハンドラを登録します（`AddFunc: sched.addPod`）。
- `addPod`（`eventhandlers.go:130` 付近）は Pod の状態で振り分けます。
  - 自分の担当（`responsibleForPod`。`spec.schedulerName` がどれかのプロファイルに一致する）で、かつ未割り当て（`assignedPod` が false、つまり `NodeName == ""`）の Pod は `addPodToSchedulingQueue` に回します。
  - すでに割り当て済みの Pod は `addAssignedPodToCache` でキャッシュに反映するだけです。
- `addPodToSchedulingQueue`（`eventhandlers.go:238`）が `SchedulingQueue.Add` を呼びます。実体は `pkg/scheduler/backend/queue/scheduling_queue.go:873` の `PriorityQueue.Add` です。
  - キューは activeQ、backoffQ、unschedulable の 3 つで構成されています。

## 3. スケジューリングループ
- `pkg/scheduler/scheduler.go:538` の `Scheduler.Run` が `go wait.UntilWithContext(ctx, sched.ScheduleOne, 0)` で無限ループを起動します。
- `pkg/scheduler/schedule_one.go:67` の `ScheduleOne` は次のように動きます。
  - `sched.NextEntity` で次の対象を取り出します。`scheduler.go:473` で `podQueue.Pop` に設定されており、中身は `activeQ.pop` です。
  - 取り出したのが Pod なら `scheduleOnePod`、PodGroup なら `scheduleOnePodGroup` に振り分けます。
- `scheduleOnePod`（`schedule_one.go:93`）の処理は次の順です。
  1. `frameworkForPod` でプロファイルを選びます。
  2. `skipPodSchedule` で、削除中の Pod などをスキップします。
  3. `CycleState` を作って `schedulingCycle` を同期実行します。
  4. 失敗したら `FailureHandler`（= `handleSchedulingFailure`、`:1152`）がキューに戻すなどの後処理をします。
  5. 成功したら `go sched.runBindingCycle(...)` でバインド処理を非同期に起動します（`:141`）。

## 4. スケジューリングサイクル（ノードを決める）
`schedulingCycle`（`schedule_one.go:169`）は次の順に進みます。

1. `Cache.UpdateSnapshot`（`backend/cache/cache.go:206`）で、ノード情報のスナップショットを最新化します。
2. `schedulingAlgorithm`（`:250`）が `sched.SchedulePod`（= `schedulePod`、`:572`）を呼びます。
   - `findNodesThatFitPod`（`:630`）がノードを絞り込みます。
     - `RunPreFilterPlugins`（`framework/runtime/framework.go:934`）
     - NominatedNodeName があれば、先に `evaluateNominatedNode` で評価します。
     - `findNodesThatPassFilters`（`:779`）が各ノードに `RunFilterPlugins` / `RunFilterPluginsWithNominatedPods` を並列に実行します。探索するノード数は `numFeasibleNodesToFind` で上限が決まります。
     - `findNodesThatPassExtenders` で Extender による絞り込みをします。
   - 候補ノードが 1 つだけなら、スコアリングせずにそのノードに決めます。
   - 複数あれば `prioritizeNodes`（`:945`）が `RunPreScorePlugins`、`RunScorePlugins`、Extender のスコアを順に計算します。
   - `framework.NewSortedScoredNodes(priorityList).Pop()` で最高スコアのノードを選び、`SuggestedHost` にします（`:613-614`）。
   - どのノードにも収まらない（`FitError`）場合は `RunPostFilterPlugins` を実行します。プリエンプションはここで行われます（`:296`）。
3. `prepareForBindingCycle`（`:196`）でバインドの準備をします。
   - `assumeAndReserve`（`:315`）
     - `assume`（`:1060`）は Pod のコピーに `Spec.NodeName = host` を設定し、`Cache.AssumePod`（`cache.go:447`）でキャッシュに「このノードに載った」と楽観的に記録します。バインド完了を待たずに次の Pod のスケジューリングへ進めるのは、この仕組みのおかげです。
     - `RunReservePluginsReserve`（`framework.go:2014`）を実行します。失敗したら `unreserveAndForget` で取り消します。
   - `RunPermitPlugins`（`framework.go:2097`）を実行します。結果が Wait なら `AddWaitingPod` で待機中として登録します。
   - プラグインが要求した Pod があれば、`SchedulingQueue.Activate` で activeQ に移します。

## 5. バインディングサイクル（非同期）
`runBindingCycle`（`:145`）が `bindingCycle`（`:399`）を呼びます。

1. 必要な場合は `RunPreBindPreFlights` を実行し、NominatedNodeName を API に書き込みます。
2. `WaitOnPermit`（`framework.go:2251`）で、Permit の承認を待ちます。
3. `SchedulingQueue.Done(uid)` で、キュー上の「処理中」状態を解除します。
4. `RunPreBindPlugins`（`framework.go:1766`）を実行します。ボリュームのバインドなどはここで行われます。
5. `bind`（`:1100`）
   - Binder 機能を持つ Extender があれば、そちらが優先されます（`extendersBinding`）。
   - なければ `RunBindPlugins`（`framework.go:1930`）を実行し、デフォルトでは `DefaultBinder.Bind`（`framework/plugins/defaultbinder/default_binder.go:52`）が使われます。
     - `v1.Binding{Target: Node}` を作り、`util.BindPod`（`pkg/scheduler/util/utils.go:111`）で `CoreV1().Pods(ns).Bind()` を呼びます。リトライ付きです。APICacher が有効な場合は `APICacher().BindPod` を経由します。
   - `finishBinding` が `Scheduled` イベントを記録します。
6. `RunPostBindPlugins` を実行します。
- どこかで失敗した場合は `handleBindingCycleError`（`:507`）が `unreserveAndForget` を呼んで、キャッシュから Pod を外し、Reserve を取り消します。

## 6. API サーバー側のバインド処理
- `pkg/registry/core/pod/storage/storage.go:177` の `BindingREST.Create` が `pods/{name}/binding` への POST を受け付けます。
- `assignPod`（`:290`）から `setPodNodeAndMetadata`（`:213`）が呼ばれ、`GuaranteedUpdate` の中で次の処理をします。
  - `NodeName` がすでに設定されていたらエラーにします（"already assigned"）。
  - `pod.Spec.NodeName = machine` を設定します。
  - `PodScheduled=True` の Condition を付けます。
  - feature gate が有効なら NominatedNodeName をクリアします。
- この更新は Informer の Update イベントとしてスケジューラーに返ってきます。スケジューラーは「assumed」だった Pod を確定した Pod としてキャッシュに反映します。kubelet も同じイベントを受け取って、Pod の起動を始めます。

---

**押さえておきたい設計上のポイント**
- 同期で 1 Pod ずつ処理するスケジューリングサイクルと、並行して動くバインディングサイクルが分かれています。
- その間をつないでいるのが、キャッシュ上の **Assume** です。

PodGroup（`scheduleOnePodGroup`）を使う場合は、Snapshot 側で `AssumePod` / `ForgetPod` を行うなど少し違う経路を通ります。必要であれば、そちらも追って説明します。

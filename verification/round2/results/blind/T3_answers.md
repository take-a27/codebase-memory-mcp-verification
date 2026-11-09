# T3 の回答（6 件）

## Answer 1

Pod が作られてから Node にバインドされるまでは、大きく 6 つの段階に分かれます。① API Server が Pod を保存する、② スケジューラーの informer が検知してキューに入れる、③ キューから取り出す、④ **Scheduling Cycle**（同期処理でノードを決め、メモリ上で仮確定する）、⑤ **Binding Cycle**（goroutine で非同期に API へ反映する）、⑥ API Server が `spec.nodeName` を書き込む、の順です。

## 全体の流れ

```
kubectl create pod
  └─ API Server が Pod を保存（spec.nodeName は空）
       └─ informer が Add イベントを通知
            └─ Scheduler.addPod → addPodToSchedulingQueue → PriorityQueue.Add（activeQ に入る）
                 └─ ScheduleOne ループ: NextEntity(=Pop) → scheduleOnePod
                      ├─ schedulingCycle（同期）
                      │    ├─ schedulingAlgorithm → schedulePod
                      │    │    ├─ findNodesThatFitPod（PreFilter / Filter / Extender）
                      │    │    └─ prioritizeNodes（PreScore / Score）→ 最高スコアのノードを選ぶ
                      │    └─ prepareForBindingCycle
                      │         ├─ assumeAndReserve（Cache.AssumePod + Reserve）
                      │         └─ RunPermitPlugins
                      └─ go runBindingCycle → bindingCycle（非同期）
                           ├─ WaitOnPermit → PreBind → bind（Extender か Bind プラグイン）
                           │    └─ DefaultBinder.Bind → pods/binding サブリソースへ POST
                           └─ PostBind
```

## 1. 起動とキューへの投入

- **`Scheduler.Run`**（`pkg/scheduler/scheduler.go:538`）
  - `SchedulingQueue.Run` を呼びます。backoffQ から activeQ に移す処理と、unschedulable なものを定期的に流し直す処理（30 秒ごと）を goroutine で起動します（`backend/queue/scheduling_queue.go:508`）。
  - そのあと `go wait.UntilWithContext(ctx, sched.ScheduleOne, 0)` で、スケジューリングループを止まらずに回し続けます。
- **`addAllEventHandlers`**（`pkg/scheduler/eventhandlers.go:624`）
  - Pod informer に `AddFunc: sched.addPod` を登録します（642 行）。
- **`Scheduler.addPod`**（`eventhandlers.go:129`）
  - 次の 2 条件を両方満たす Pod を `addPodToSchedulingQueue` に渡します（238 行）。
    - `responsibleForPod`：`spec.schedulerName` が自分のプロファイルのどれかと一致する。
    - `!assignedPod`：`spec.nodeName` が空。
  - すでにノードが割り当て済みの Pod は、`addAssignedPodToCache` でキャッシュに入れるだけです。
- **`PriorityQueue.Add`**（`scheduling_queue.go:873`）
  - Pod を activeQ に入れます。

## 2. キューから取り出す

- **`ScheduleOne`**（`pkg/scheduler/schedule_one.go:67`）
  - `sched.NextEntity` を呼びます。実体は `PriorityQueue.Pop`（`scheduler.go:473`、`scheduling_queue.go:1335`）で、activeQ が空の間はここでブロックします。
  - 取り出したものが単体の Pod（`*QueuedPodInfo`）なら `scheduleOnePod`（93 行）へ進みます。PodGroup なら `scheduleOnePodGroup` へ分かれます。
- **`scheduleOnePod`**
  - `frameworkForPod` で、schedulerName に対応するプロファイル（Framework）を選びます。
  - `skipPodSchedule` で、削除中の Pod や assume 済みの Pod を除外します。
  - `CycleState` を作ってから `schedulingCycle` を呼びます。

## 3. Scheduling Cycle（同期処理で、1 回に 1 Pod ずつ）

**`schedulingCycle`**（`schedule_one.go:169`）は次の順で進みます。

1. **`Cache.UpdateSnapshot`**
   - ノード情報のスナップショットを最新にします。
2. **`schedulingAlgorithm`**（250 行）→ **`SchedulePod`**
   - `SchedulePod` の実体は `schedulePod`（572 行）です。`scheduler.go:129` で代入されています。
   - **`findNodesThatFitPod`**（630 行）
     - `RunPreFilterPlugins` を実行します。
     - Pod に `NominatedNodeName` があれば、`evaluateNominatedNode` でそのノードを先に試します。プリエンプション後の再スケジュールのためです。
     - `findNodesThatPassFilters` で **Filter プラグイン**を全ノードに並列実行し、続けて `findNodesThatPassExtenders` を実行します。
   - 条件に合うノードが 0 件なら `FitError` を返します。1 件ならそのノードに決まります。
   - 2 件以上なら **`prioritizeNodes`**（945 行）で PreScore / Score / NormalizeScore と Extender のスコアを合計し、`SortedScoredNodes.Pop()` で最高スコアのノードを `SuggestedHost` にします。
   - 失敗したときは `RunPostFilterPlugins` を実行します。プリエンプション（DefaultPreemption）はここで動き、結果として `NominatedNodeName` が決まります。
3. **`prepareForBindingCycle`**（196 行）
   - **`assumeAndReserve`**（315 行）
     - **`assume`**（1060 行）で、Pod のコピーに `Spec.NodeName = host` を設定し、**`Cache.AssumePod`**（`backend/cache/cache.go:447`）でキャッシュ上は「配置済み」として扱います。
     - これにより、API への書き込みを待たずに次の Pod のスケジューリングを始められます。
     - 続けて `RunReservePluginsReserve` を実行します。
   - **`RunPermitPlugins`**
     - プラグインが Wait を返した場合は `AddWaitingPod` で待機状態にします。
     - Reject の場合は `unreserveAndForget` で、ここまでの処理を元に戻します。

どこかで失敗した場合は `FailureHandler` が呼ばれます。実体は `handleSchedulingFailure`（1152 行）で、Pod を unschedulable のキューか backoffQ に戻します。

## 4. Binding Cycle（goroutine で非同期に実行）

`scheduleOnePod` の最後で `go sched.runBindingCycle(...)`（141 行）を起動します。中身は **`bindingCycle`**（399 行）です。

1. （feature gate が有効な場合）`RunPreBindPreFlights` を実行し、`NominatedNodeName` を API に書き込みます。
2. **`WaitOnPermit`**
   - Permit が Wait を返していた場合、ここで承認されるまでブロックします。
3. `SchedulingQueue.Done` を呼びます。
4. **`RunPreBindPlugins`**
   - 例えば VolumeBinding による PV のバインドなどがここで行われます。
5. **`bind`**（1100 行）
   - まず `extendersBinding`（Binder 機能を持つ Extender があればそちら）を試します。
   - なければ `RunBindPlugins`（`framework/runtime/framework.go:1930`）を実行します。
   - **`DefaultBinder.Bind`**（`framework/plugins/defaultbinder/default_binder.go:52`）が `v1.Binding{Target: Node}` を作ります。APICacher 経由か、`util.BindPod`（`pkg/scheduler/util/utils.go:111`）経由で `Pods(ns).Bind(...)`、つまり **`pods/{name}/binding` サブリソースへの POST** を、リトライ付きで送ります。
   - 終わると `finishBinding` が呼ばれます。
6. `RunPostBindPlugins` を実行し、メトリクスを記録します。

失敗した場合は `handleBindingCycleError`（507 行）が処理します。Unreserve を実行して `Cache.ForgetPod` で assume を取り消し、他の Pod をキューから再評価させたうえで `FailureHandler` に渡します。

## 5. API Server 側

- **`BindingREST.Create`**（`pkg/registry/core/pod/storage/storage.go:177`）→ `assignPod`（290 行）→ `setPodNodeAndMetadata`（213 行）
  - 実際に etcd 上の Pod に `spec.nodeName` を書き込み、`PodScheduled` condition を設定します。この部分は場所を確認しただけで、中身は読んでいません。
- 更新された Pod はスケジューラーの informer に Update イベントとして届きます。`updatePod` が「割り当て済みの Pod」としてキャッシュに反映し、assume 状態が確定します。同じ更新を kubelet も watch で受け取り、コンテナを起動します。

## 補足

- 引用した行番号は、このチェックアウト（v1.37.2）でソースを読んで確認したものです。
- このバージョンでは PodGroup（ギャングスケジューリング）の経路（`scheduleOnePodGroup`）や OpportunisticBatching などが入っていて、分岐が増えています。上の説明は単体 Pod の基本経路です。

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

Pod が作られてからノードにバインドされるまでの流れを、v1.37.2 のコードをもとに順に説明します。下に書いた関数とファイルは、[tool] の[tool]で場所を確認したうえで、ソースを直接読んで裏を取りました。

## 全体の流れ

```
kubectl / コントローラ → apiserver に Pod が保存される（spec.nodeName は空）
   ↓ Informer の Add イベント
① eventhandlers.go   addPod → addPodToSchedulingQueue → SchedulingQueue.Add
   ↓
② scheduler.go       Run → wait.UntilWithContext(ScheduleOne)
   ↓ NextEntity (= PriorityQueue.Pop) で 1 件ずつ取り出す
③ schedule_one.go    ScheduleOne → scheduleOnePod
   ├─ スケジューリングサイクル（同期処理。1 回に 1 Pod）
   │    schedulingCycle → schedulingAlgorithm → schedulePod
   │      → findNodesThatFitPod（Filter） → prioritizeNodes（Score） → 最高スコアのノードを選ぶ
   │    prepareForBindingCycle → assumeAndReserve（Assume + Reserve） → Permit
   └─ バインディングサイクル（goroutine で非同期に実行）
        runBindingCycle → bindingCycle
          → WaitOnPermit → PreBind → bind（Extender か Bind プラグイン） → PostBind
   ↓
④ DefaultBinder.Bind → pods/binding サブリソースへ POST
   ↓
⑤ apiserver: BindingREST.Create → assignPod → spec.nodeName が書き込まれる
```

## ① Pod をキューに入れる（`pkg/scheduler/eventhandlers.go`）

- `addPod`（`pkg/scheduler/eventhandlers.go:129`）は Pod Informer の Add ハンドラです。次の 2 条件を満たす Pod を `addPodToSchedulingQueue` に渡します。
  - `responsibleForPod`: `schedulerName` がこのスケジューラーのプロファイルに一致する。
  - `!assignedPod`: `spec.nodeName` がまだ空。
- `nodeName` がすでに入っている Pod は、`addAssignedPodToCache` でキャッシュに入れるだけです。
- `addPodToSchedulingQueue`（`pkg/scheduler/eventhandlers.go:238`）は `sched.SchedulingQueue.Add(...)` を呼びます。その中身は `PriorityQueue.Add`（`pkg/scheduler/backend/queue/scheduling_queue.go:873`）で、Pod は activeQ に積まれます。
- これらのハンドラは `addAllEventHandlers`（`pkg/scheduler/eventhandlers.go:624`）で登録されます。

## ② スケジューリングループ（`pkg/scheduler/scheduler.go`）

- `Scheduler.Run`（`pkg/scheduler/scheduler.go:538`）は `go wait.UntilWithContext(ctx, sched.ScheduleOne, 0)` で `ScheduleOne` を間を空けずに回し続けます（`pkg/scheduler/scheduler.go:552`）。
- 次の Pod の取り出しには `sched.NextEntity = podQueue.Pop` を使います（`pkg/scheduler/scheduler.go:473`）。実体は `PriorityQueue.Pop`（`pkg/scheduler/backend/queue/scheduling_queue.go:1335`）で、キューが空のあいだはブロックします。
- 試行に使う関数は `sched.SchedulePod = sched.schedulePod` で差し込まれています（`pkg/scheduler/scheduler.go:129`）。テストではここをモックに置き換えています。

## ③ スケジューリングサイクル（`pkg/scheduler/schedule_one.go`）

| 関数 | 行 | 役割 |
|---|---|---|
| `ScheduleOne` | `pkg/scheduler/schedule_one.go:67` | キューから取り出し、Pod なら `scheduleOnePod`、PodGroup なら `scheduleOnePodGroup` に振り分ける |
| `scheduleOnePod` | `pkg/scheduler/schedule_one.go:93` | プロファイルに対応する Framework を選び、`CycleState` を作り、`schedulingCycle` を呼ぶ。失敗したら `FailureHandler` を呼び、成功したら `go sched.runBindingCycle(...)` を起動する |
| `schedulingCycle` | `pkg/scheduler/schedule_one.go:169` | `Cache.UpdateSnapshot` でノード情報のスナップショットを更新し、`schedulingAlgorithm` と `prepareForBindingCycle` を順に呼ぶ |
| `schedulingAlgorithm` | `pkg/scheduler/schedule_one.go:250` | `SchedulePod` を呼ぶ。`FitError` の場合は PostFilter（プリエンプション）を実行し、NominatedNodeName を決める |
| `schedulePod` | `pkg/scheduler/schedule_one.go:572` | 本体。下で説明する |
| `findNodesThatFitPod` | `pkg/scheduler/schedule_one.go:630` | PreFilter、Filter プラグイン、Extender の Filter を順に実行する |
| `prioritizeNodes` | `pkg/scheduler/schedule_one.go:945` | PreScore、Score（正規化と重み付けを含む）、Extender の Prioritize を実行する |
| `assumeAndReserve` | `pkg/scheduler/schedule_one.go:315` | `assume` と `RunReservePluginsReserve` を実行する |
| `assume` | `pkg/scheduler/schedule_one.go:1060` | Pod のコピーに `Spec.NodeName = host` を入れ、`Cache.AssumePod` を呼ぶ |
| `prepareForBindingCycle` | `pkg/scheduler/schedule_one.go:196` | Assume と Reserve のあとに `RunPermitPlugins` を実行する。結果が Wait なら `AddWaitingPod` に登録する |

`schedulePod` の中身は次のとおりです。

- フィルタを通ったノードが 0 個なら `FitError` を返します。
- 1 個ならスコア計算を省き、そのノードに決めます。
- 2 個以上なら `prioritizeNodes` でスコアを付け、`NewSortedScoredNodes(...).Pop()` で最高スコアのノードを選び、`ScheduleResult.SuggestedHost` に入れます。

「Assume」は、apiserver の更新を待たずに「この Pod はこのノードに載った」とスケジューラーのキャッシュ上で先に決めてしまう処理です。これがあるので、バインドを待たずに次の Pod のスケジューリングへ進めます。バインドに失敗したときは `unreserveAndForget`（`pkg/scheduler/schedule_one.go:366`）が Unreserve と `Cache.ForgetPod` で元に戻します。

## ④ バインディングサイクル（非同期。`pkg/scheduler/schedule_one.go`）

- `runBindingCycle`（`pkg/scheduler/schedule_one.go:145`）は `bindingCycle` を呼び、失敗したら `handleBindingCycleError` を呼びます。
- `bindingCycle`（`pkg/scheduler/schedule_one.go:399`）は次の順に処理します。
  1. （NominatedNodeNameForExpectation が有効な場合だけ）PreBindPreFlight を実行し、必要なら NominatedNodeName を apiserver に書き込みます。
  2. `WaitOnPermit` で、Permit プラグインが Wait を返した Pod の許可を待ちます。
  3. `SchedulingQueue.Done` を呼びます。この時点より後の失敗は Unschedulable 扱いにならず、BackoffQ に戻されて再試行されます。
  4. `RunPreBindPlugins` を実行します。ボリュームのバインドや DRA などがここで動きます。
  5. `sched.bind(...)` を実行します。
  6. `RunPostBindPlugins` を実行し、メトリクスを記録します。
- `bind`（`pkg/scheduler/schedule_one.go:1100`）は、まず `extendersBinding` で Binder 機能を持つ Extender があればそちらに任せます。なければ `RunBindPlugins` を呼びます。最後に `finishBinding` を呼びます。
- 標準の Bind プラグインは `DefaultBinder.Bind`（`pkg/scheduler/framework/plugins/defaultbinder/default_binder.go:52`）です。`v1.Binding{Target: Node}` を作り、APICacher（API ディスパッチャ）があればそれ経由で、なければ `util.BindPod`（`pkg/scheduler/util/utils.go:111`）で `pods/{name}/binding` に POST します。

## ⑤ apiserver 側（`pkg/registry/core/pod/storage/storage.go`）

- `BindingREST.Create`（`pkg/registry/core/pod/storage/storage.go:177`）が binding リクエストを受けます。
- `assignPod`（`pkg/registry/core/pod/storage/storage.go:290`）から `setPodNodeAndMetadata`（`pkg/registry/core/pod/storage/storage.go:213`）が呼ばれ、etcd 上の Pod の `spec.nodeName` が書き込まれます。nodeName がすでに入っている場合は Conflict になります。PodScheduled 条件もここで更新されます。
- 書き込みの結果は Informer の Update イベントとしてスケジューラーに戻ります。スケジューラーは Assume しておいた Pod を確定させ、同じイベントを受け取った kubelet がコンテナを起動します。

## 補足

- 最近のバージョンでは `ScheduleOne` が PodGroup（gang scheduling 系）も扱います。上の説明は通常の単一 Pod の経路（`scheduleOnePod`）だけを追ったものです。
- 失敗時の再キューイング（`FailureHandler` → `handleSchedulingFailure`）と、Filter・Score の並列化の詳細（`findNodesThatPassFilters` など）は省きました。必要なら続けて追います。

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

Pod が作成されてから Node にバインドされるまでの流れを、v1.37.2 のソースに沿って説明します。関数と行番号は、[tool]で見つけたものを実際のソースで確認しています。

## 全体の流れ

```
kubectl create pod
  └─ kube-apiserver が etcd に保存（この時点では spec.nodeName は空）
       └─ スケジューラーの informer が Add イベントを受け取る
            └─ addPod → addPodToSchedulingQueue → PriorityQueue.Add
                 └─ ScheduleOne ループ（Pop → scheduleOnePod）
                      ├─ スケジューリングサイクル（同期処理）: Filter → Score → Assume → Reserve → Permit
                      └─ バインディングサイクル（別 goroutine）: WaitOnPermit → PreBind → Bind → PostBind
                           └─ POST pods/{name}/binding → apiserver が spec.nodeName を設定
```

## 1. Pod の検知とキューへの追加
- **`addAllEventHandlers`**（`pkg/scheduler/eventhandlers.go:624`）が Pod informer にハンドラーを登録します。
- **`addPod`**（`pkg/scheduler/eventhandlers.go:129`）
  - 自分の担当で（`responsibleForPod`）、まだ Node に割り当てられていない（`!assignedPod`）Pod は `addPodToSchedulingQueue` に渡します。
  - すでに割り当て済みの Pod は `addAssignedPodToCache` でキャッシュに入れます。
- **`addPodToSchedulingQueue`**（`pkg/scheduler/eventhandlers.go:238`）は **`PriorityQueue.Add`**（`pkg/scheduler/backend/queue/scheduling_queue.go:873`）を呼び、Pod を activeQ に入れます。

## 2. スケジューリングループ
- **`Scheduler.Run`**（`pkg/scheduler/scheduler.go:538`）は、キューを起動したあと `wait.UntilWithContext(ctx, sched.ScheduleOne, 0)` で `ScheduleOne` を繰り返し呼びます。
- **`ScheduleOne`**（`pkg/scheduler/schedule_one.go:67`）
  - `sched.NextEntity` で次の対象を取り出します。実体は `PriorityQueue.Pop` で（`scheduler.go:473` で代入、定義は `scheduling_queue.go:1335`）、キューが空なら待ちます。
  - 取り出した対象が Pod なら `scheduleOnePod`、PodGroup なら `scheduleOnePodGroup` に分かれます。
- **`scheduleOnePod`**（`schedule_one.go:93`）
  - `frameworkForPod` で `spec.schedulerName` に対応するプロファイルを選びます。
  - `skipPodSchedule` で、削除中の Pod や assume 済みの Pod を飛ばします。
  - `CycleState` を作ってから `schedulingCycle` を呼びます。
  - 成功したら `go sched.runBindingCycle(...)` で、バインドを**別の goroutine で非同期に**行います。

## 3. スケジューリングサイクル（同期処理）
**`schedulingCycle`**（`schedule_one.go:169`）は次の 3 段階で進みます。

1. `Cache.UpdateSnapshot` で、Node 情報のスナップショットを最新にします。
2. **`schedulingAlgorithm`**（`:250`）が `sched.SchedulePod` を呼びます。実体は **`schedulePod`** で（`:572`、代入は `scheduler.go:129`）、中身は次のとおりです。
   - **`findNodesThatFitPod`**（`:630`）
     - `RunPreFilterPlugins` を実行します。
     - NominatedNodeName（プリエンプションで指名された Node）があれば、`evaluateNominatedNode` でその Node を先に試します。
     - `findNodesThatPassFilters`（`:779`）で Filter プラグインを Node ごとに並列実行します。
     - `findNodesThatPassExtenders` で extender による絞り込みを行います。
   - 条件に合う Node が 1 つだけなら、その Node に決めます。複数あれば **`prioritizeNodes`**（`:945`）で PreScore・Score を実行し、`NewSortedScoredNodes(...).Pop()` で最高スコアの Node を選びます。
   - 合う Node が 0 なら `FitError` を返します。その場合 `schedulingAlgorithm` が `RunPostFilterPlugins`（DefaultPreemption によるプリエンプション）を実行します。
3. **`prepareForBindingCycle`**（`:196`）
   - **`assumeAndReserve`**（`:315`）
     - **`assume`**（`:1060`）が `Pod.Spec.NodeName = host` を設定し、`Cache.AssumePod` を呼びます。API からの結果を待たずに「この Node に載った」とキャッシュ上で仮定するので、次の Pod のスケジューリングにすぐ進めます。
     - そのあと `RunReservePluginsReserve` を実行します。
   - `RunPermitPlugins` の結果が Wait なら `AddWaitingPod` で待機中の Pod として登録します。失敗した場合は `unreserveAndForget` で、Reserve と Assume を取り消します。

失敗したときは `FailureHandler`（`handleSchedulingFailure`、`:1152`）が呼ばれ、Pod は unschedulable キューまたは backoff キューに戻されます。

## 4. バインディングサイクル（非同期処理）
`runBindingCycle`（`:145`）から **`bindingCycle`**（`:399`）が呼ばれます。

1. （機能が有効な場合）`RunPreBindPreFlights` を実行し、必要なら NominatedNodeName を API に書き込みます。
2. **`WaitOnPermit`** で、Permit が Wait を返していた場合に、許可されるか拒否されるかを待ちます。
3. `SchedulingQueue.Done` を呼びます。ここより後で失敗しても、Pod は「unschedulable」とは扱われず、backoff 後に再試行されます。
4. **`RunPreBindPlugins`** を実行します（ボリュームのバインドや DRA など）。
5. **`bind`**（`:1100`）
   - まず `extendersBinding` で、バインドを担当する extender がいればそれに任せます。
   - いなければ **`RunBindPlugins`**（`pkg/scheduler/framework/runtime/framework.go:1930`）を実行します。
   - `finishBinding` で `Scheduled` イベント（"Successfully assigned ..."）を記録します。
6. `RunPostBindPlugins` を実行します。

失敗したときは `handleBindingCycleError`（`:507`）が `unreserveAndForget` を呼んで取り消し、他の Pod をキューで再評価させます。

## 5. 実際のバインド API 呼び出し
- **`DefaultBinder.Bind`**（`pkg/scheduler/framework/plugins/defaultbinder/default_binder.go:52`）は `v1.Binding{Target: Node}` を組み立てます。
  - APICacher（非同期 API ディスパッチャー）があれば `APICacher().BindPod` を使います。
  - なければ **`util.BindPod`**（`pkg/scheduler/util/utils.go:111`）を呼びます。中身は `cs.CoreV1().Pods(ns).Bind(...)` で、リトライ付きで `POST /pods/{name}/binding` を送ります。
- apiserver 側では **`BindingREST.Create`**（`pkg/registry/core/pod/storage/storage.go:177`）が受け付け、`assignPod` → `setPodNodeAndMetadata` の順に呼ばれて、Pod の `spec.nodeName` を設定し PodScheduled condition を付けます。
- この更新を informer が受け取ると、スケジューラーは assume していた Pod を確定させます。kubelet は自分の Node に割り当てられた Pod を検知して起動します。

## 押さえておくポイント
- **assume を使った楽観的な非同期化**: スケジューリングサイクルは 1 Pod ずつ直列に動きます。一方、遅い API 呼び出しを含むバインドは goroutine に切り出され、その間もキャッシュ上では Pod が Node に載っている前提で次の Pod の判断が進みます。
- **拡張ポイントの順番**: PreFilter → Filter → (PostFilter) → PreScore → Score → Reserve → Permit がスケジューリングサイクル、WaitOnPermit → PreBind → Bind → PostBind がバインディングサイクルです。

上で挙げたファイルについて、インデックスのカバレッジチェックで問題は報告されていません（ただしこのチェックは目安です）。PodGroup（ギャングスケジューリング）の `scheduleOnePodGroup` 側の流れは、今回の説明には含めていません。

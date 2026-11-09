# T1 の回答（6 件）

## Answer 1

## 定義

`podutil` は通常 `k8s.io/kubernetes/pkg/api/v1/pod` を指します（`pkg/api/v1/pod/util.go:297`）。

```go
func IsPodReady(pod *v1.Pod) bool {
	return IsPodReadyConditionTrue(pod.Status)
}
```

`pod.Status.Conditions` の中の `Ready` condition が `True` かどうかだけを見ています。kubelet が readiness probe の結果などから設定する値を読むだけで、自分でコンテナの状態を調べるわけではありません。

同じ名前の関数がほかに 3 つあります。

| 定義場所 | 対象の型 | 使っている側 |
|---|---|---|
| `pkg/api/pod/util.go:257` | 内部型 `api.Pod` | apiserver の eviction 処理 |
| `staging/.../endpointslice/util/controller_utils.go:360` | `v1.Pod` | EndpointSlice ライブラリ |
| `staging/.../kubectl/pkg/util/podutils/podutils.go:46` | `v1.Pod` | kubectl と e2e テストのフレームワーク |

## 主な呼び出し元と用途

### 1. 「利用可能か（Available）」を判定する土台
- `pkg/api/v1/pod/util.go:284` `IsPodAvailable`：Ready であり、かつ `minReadySeconds` が経過していれば Available とみなします。Deployment、ReplicaSet、StatefulSet、DaemonSet の `availableReplicas` はこの関数で数えられます。
- `pkg/controller/controller_utils.go:1033` `nextPodAvailabilityCheck`：まだ `minReadySeconds` が経っていない Pod について、次にいつ確認し直すかを計算します。

### 2. 減らすときに消す Pod を選ぶ順番（`pkg/controller/controller_utils.go`）
- `ActivePods.Less`（719行目）、`ActivePodsWithRanks.Less`（855行目）：ReplicaSet などがレプリカを減らすとき、**NotReady の Pod から先に消す**ための並べ替えです。
- `podReadyTime`（937行目）：どちらも Ready の場合は、Ready になってからの時間が短い方を先に消します。

### 3. Ready への変化をきっかけに再処理する
- `replicaset/replica_set.go:558`、`statefulset/stateful_set.go:325`、`daemon/daemon_controller.go:664`：Pod が NotReady から Ready に変わり、かつ `minReadySeconds > 0` のときは、その時間が過ぎた後にもう一度処理を予約します（Available になる瞬間を拾うため）。

### 4. ステータスの数を集計する
- `replicaset/replica_set_utils.go:111`：`readyReplicas` を数えます。
- `job/job_controller.go:1238, 1293, 2208`：Job の `status.ready` を数えます。
- `daemon/daemon_controller.go:1235`：`numberReady` を数えます。
- `statefulset/stateful_set_utils.go:468` `isRunningAndReady`：StatefulSet が Pod を 1 つずつ順に起動する（OrderedReady）ときの判定に使います。

### 5. ロールアウトの判断
- `daemon/daemon_controller.go:948`：DaemonSet の surge 付きローリングアップデートで、古い Pod が NotReady なら新しい Pod の Ready を待たずに古い方を消します。

### 6. Service のエンドポイント
- `endpoint/endpoints_controller.go:654`：Ready の Pod は `addresses` に、NotReady の Pod は `notReadyAddresses` に分けます（`publishNotReadyAddresses` が設定されていれば常に `addresses` 側）。
- `staging/.../endpointslice/utils.go:39`：EndpointSlice の `serving` と `ready` を決めます。

### 7. 健全性の判定（PDB / Eviction / HPA）
- `disruption/disruption.go:931`：PodDisruptionBudget の `currentHealthy` を数えるとき、「健全」を「Ready」と同じ意味で扱っています。
- `registry/core/pod/storage/eviction.go:241`（内部型版）：Eviction API で、NotReady の Pod は PDB の保護対象から外せるかを判断します（`UnhealthyPodEvictionPolicy` と組み合わせて判定）。
- `podautoscaler/replica_calculator.go:343`：HPA が scale-to-zero や外部メトリクスで計算するときに、Running かつ Ready の Pod だけを数えます。

### 8. テスト
- `test/e2e/...` や `test/integration/...` に多数あります。主に「Pod が Ready になるまで待つ」ためのポーリング条件として使われています（例：`test/e2e/framework/pod/wait.go:103`、`test/e2e/common/node/container_probe.go`）。

## まとめ

コントローラーが「この Pod はトラフィックを受けられる、正常な Pod か」を判断する基本の関数です。主な用途は次のとおりです。
- Available の判定と `minReadySeconds` の処理
- レプリカを減らすときに消す Pod の優先順位
- ready 数などのステータス集計
- Service のエンドポイントに入れるかどうか
- PDB、Eviction、HPA における健全な Pod の数え方

## Answer 2

## 結論

`podutil.IsPodReady` は `pkg/api/v1/pod/util.go:297` にあります（`k8s.io/kubernetes/pkg/api/v1/pod` を `podutil` という名前で import して使うのが慣例です）。中身は `IsPodReadyConditionTrue(pod.Status)` を返すだけで、**Pod の `Ready` condition が `True` かどうか**を判定します。

主な用途は 3 つです。
1. **数える**: status の `readyReplicas` などに入れる Ready な Pod の数を数える。
2. **選ぶ**: スケールダウンや更新のとき、Ready でない Pod を先に削除する。
3. **気づく**: Pod が Ready になった瞬間を捉えて、`minReadySeconds` の経過後に処理をやり直す。

## 主な呼び出し元（本体コード）

| 場所 | 関数 | 用途 |
|---|---|---|
| `pkg/api/v1/pod/util.go:284` | `IsPodAvailable` | 「Available = Ready が `minReadySeconds` 以上続いている」の前提チェック |
| `pkg/controller/controller_utils.go:709/749/843` | `ByLogging` / `ActivePods` / `ActivePodsWithRanks` の `Less` | Pod の並べ替え。`ActivePods` 系では Ready でない Pod を前に並べ、スケールダウン時に先に削除されるようにする |
| `pkg/controller/controller_utils.go:936` | `podReadyTime` | Ready になった時刻（`LastTransitionTime`）を取り、並べ替えの基準にする |
| `pkg/controller/controller_utils.go:1032` | `nextPodAvailabilityCheck` | `minReadySeconds` を過ぎて Available になるまでの残り時間を計算する |
| `pkg/controller/replicaset/replica_set_utils.go:111` | `calculateStatus` | RS の `readyReplicas` を数える |
| `pkg/controller/replicaset/replica_set.go:558` | `updatePod` | Not Ready から Ready に変わったら、`minReadySeconds` の経過後に再処理を予約する |
| `pkg/controller/replicaset/replica_set.go:957/964` | `reportSortingDeletionAgeRatioMetric` | 削除する Pod の選び方に関するメトリクス |
| `pkg/controller/statefulset/stateful_set_utils.go:468` | `isRunningAndReady` | Running かつ Ready か。順番どおりに Pod を作る・更新する処理の前提になる |
| `pkg/controller/statefulset/stateful_set.go:325` | `updatePod` | RS と同じく、Ready になったら再処理を予約する |
| `pkg/controller/daemon/daemon_controller.go:664` | `updatePod` | 同上 |
| `pkg/controller/daemon/daemon_controller.go:948` | `podsShouldBeOnNode` | surge 更新中、古い Pod が Not Ready なら新しい Pod に置き換える |
| `pkg/controller/daemon/daemon_controller.go:1235` | `updateDaemonSetStatus` | `numberReady` を数える |
| `pkg/controller/endpoint/endpoints_controller.go:654` | `addEndpointSubset` | Ready な Pod（または `tolerateUnreadyEndpoints` の場合）を `addresses` に入れ、それ以外を `notReadyAddresses` に入れる |
| `pkg/controller/job/job_controller.go:2208` | `countReadyPods` | Job status の `ready` を数える |
| `pkg/controller/job/job_controller.go:1238/1293` | `deleteJobPods` / `deleteActivePods` | Pod を削除するときに Ready だった Pod の数を数え、`ready` の数を合わせる |
| `pkg/controller/podautoscaler/replica_calculator.go:343` | `getReadyPodsCount` | HPA のレプリカ数計算で、Running かつ Ready な Pod を数える |
| `pkg/registry/core/pod/storage/eviction.go:241` | Eviction API の中 | PDB が言う「健全」の判定に使う。Not Ready な Pod は `UnhealthyPodEvictionPolicy` に従って、PDB の予算を消費せずに退避できる場合がある |

テストのコードでも多く使われています。例えば `test/e2e/common/node/container_probe.go` の readiness probe の検証、`test/e2e/apps/wait.go` や `disruption.go` での待ち合わせ、`test/utils/conditions.go` などです。

## 補足
- `pkg/controller/disruption/disruption.go:931` の `countHealthyPods`（PDB の `currentHealthy` を数える）は、別名 `apipod` で import した同じ関数を呼んでいます。grep で `podutil.` を探しても見つかりませんが、用途は同じです。
- 同じ名前の関数がほかにも 3 つあり、どれも別物です。
  - `pkg/api/pod/util.go`
  - `staging/.../kubectl/pkg/util/podutils`（kubectl 用）
  - `staging/.../endpointslice/util`（EndpointSlice コントローラ用）
- [tool]の呼び出し元一覧（テスト以外で 18 件）と grep の結果は、ほぼ一致しました。ただし `eviction.go` の呼び出しは[tool]に出てこず、grep で見つけました。また 3 つの `Less` メソッドは[tool]上で 1 つにまとまっていました。インデックスの対象範囲チェックでは問題は記録されていませんが、これは「抜けがない」ことの保証にはなりません。

## Answer 3

`podutil.IsPodReady` は `pkg/api/v1/pod/util.go:297` で定義されています。中身は `IsPodReadyConditionTrue(pod.Status)` を返すだけで、Pod の `Ready` condition が `True` かどうかを判定します。本番コード（テスト以外）では、ほぼ全部が **kube-controller-manager の各コントローラ** と、**PDB に従う Eviction API** から呼ばれています。

## 主な呼び出し元と用途

### 1. コントローラ共通ユーティリティ（`pkg/controller/controller_utils.go`）
- **`ActivePods.Less` / `ActivePodsWithRanks.Less`**（:749, :843）: スケールダウン時に**どの Pod から消すか**を決める並べ替えです。Ready でない Pod を先に消します。ReplicaSet や Job などが使います。
- **`ByLogging.Less`**（:709）: ログを出す対象の Pod を選ぶための並べ替えです。こちらは Ready な Pod を優先します。
- **`podReadyTime`**（:936）: Ready になった時刻を取り出します。Ready の Pod 同士を「Ready になってからの長さ」で比べるのに使います。
- **`nextPodAvailabilityCheck`**（:1032）: `minReadySeconds` を満たして Available になるまでの残り時間を計算します。
- 同じパッケージの **`IsPodAvailable`**（`pkg/api/v1/pod/util.go`）も、内部で `IsPodReady` を呼んだうえで `minReadySeconds` を判定しています。

### 2. ワークロード系コントローラ
| 呼び出し元 | 用途 |
|---|---|
| ReplicaSet `calculateStatus`（`replica_set_utils.go:111`） | `status.readyReplicas` を数える |
| ReplicaSet / StatefulSet / DaemonSet の `updatePod` | Pod が「Ready でない → Ready」に変わったことを検知し、`minReadySeconds` 経過後にもう一度処理するよう予約する |
| ReplicaSet `reportSortingDeletionAgeRatioMetric` | 消した Pod の古さに関するメトリクスを出す |
| StatefulSet `isRunningAndReady`（`stateful_set_utils.go:468`） | Running かつ Ready かを判定する。OrderedReady のときに順番に作成・更新を進めるかどうかの条件 |
| DaemonSet `podsShouldBeOnNode`, `updateDaemonSetStatus` | ローリング更新で古い Pod を消してよいかの判断、`numberReady` の集計 |
| Job `countReadyPods`, `deleteActivePods`, `deleteJobPods` | `status.ready` の集計、Pod を消したときに Ready だった数を記録する |

### 3. トラフィック・オートスケール・可用性
- **Endpoints コントローラ `addEndpointSubset`**（`endpoints_controller.go:654`）: Ready な Pod を `addresses` に入れ、Ready でない Pod は `notReadyAddresses` に入れます。ただし `publishNotReadyAddresses` が有効なら Ready でなくても `addresses` に入れます。つまり Service がトラフィックを送る先を決めています。
- **HPA `getReadyPodsCount`**（`replica_calculator.go:343`）: Running かつ Ready な Pod の数を数え、スケール計算に使います。
- **Disruption（PDB）コントローラ `countHealthyPods`**（`disruption.go:931`、`apipod` という別名で import）: PDB の `currentHealthy` を数えます。
- **Eviction API**（`pkg/registry/core/pod/storage/eviction.go:241`）: コード内のコメントにある通り「IsPodReady が IsHealthy の現在の実装」です。Ready でない Pod を退避させるときに、PDB の `disruptionsAllowed` を減らさずに消せるかどうかを判断します（`UnhealthyPodEvictionPolicy` と関係します）。

### 4. テスト
integration テスト（replicaset、statefulset、daemonset、job、deployment）と e2e テスト（`test/e2e/apps/wait.go`, `disruption.go`, `network/service.go`, `common/node/container_probe.go` など）で、Pod が Ready になるのを待つために多く使われています。

## 補足
- **同じ名前の別関数**が3つあります。`kubectl/pkg/util/podutils.IsPodReady`、`endpointslice/util.IsPodReady`（EndpointSlice の serving 判定に使われる）、`pkg/api/pod.IsPodReady` です。上の一覧はどれも `pkg/api/v1/pod` 版の呼び出しだけです。
- **[tool]の結果の注意点**: [tool]（呼び出し元 18 件）には **Eviction API からの呼び出しが入っていませんでした**。上の一覧は grep で補いました。また、3つの `Less` メソッドが1件にまとめて表示されていました。

## Answer 4

## 定義

`podutil.IsPodReady` は通常 `k8s.io/kubernetes/pkg/api/v1/pod`（`pkg/api/v1/pod/util.go:297`）を指します。

```go
func IsPodReady(pod *v1.Pod) bool {
    return IsPodReadyConditionTrue(pod.Status)   // PodReady condition が True か
}
```

処理は、`pod.Status.Conditions` の中に `Type=Ready` かつ `Status=True` があるかを見るだけです。phase や DeletionTimestamp は確認しません。そのため、多くの呼び出し元は `Phase == Running` などの条件を自分で組み合わせています。

同じ名前の関数は他にもあります。
- `pkg/api/pod/util.go:257`：内部型 `api.Pod` 版
- `staging/.../kubectl/pkg/util/podutils/podutils.go:46`：kubectl 用
- `staging/.../endpointslice/util/controller_utils.go:360`：EndpointSlice 用

## 主な呼び出し元と用途（本番コード）

### 1. 他の判定のベース（可用性・Ready 時刻）
- `pkg/api/v1/pod/util.go:284` `IsPodAvailable`：Ready であることを前提に、`minReadySeconds` 経過済みかで「Available」を判定します。Deployment/ReplicaSet/DaemonSet の `availableReplicas` の土台です。
- `pkg/controller/controller_utils.go:1033` `nextPodAvailabilityCheck`：あと何秒で Available になるかを計算し、再キューのタイミングに使います。
- `pkg/controller/controller_utils.go:937` `podReadyTime`：Ready になった時刻（LastTransitionTime）を取得します。

### 2. スケールダウン時に消す Pod の優先順位付け（ソート）
`pkg/controller/controller_utils.go` 内の次の `Less` 関数で使われています。
- `ActivePods.Less` (:761)
- `ActivePodsWithRanks.Less` (:855)：ReplicaSet がスケールダウンするときの削除順を決めます。
- `ByLogging.Less` (:719)：`kubectl logs` などでどの Pod を選ぶかの順序です。

未 Ready の Pod を先に削除し、Ready の Pod を残す方向に並べます。両方とも Ready なら、Ready になってからの時間で比べます。

### 3. ワークロードコントローラの status 集計
- ReplicaSet：`replica_set_utils.go:111` `calculateStatus` で `readyReplicas` を数えます。
- Job：`job_controller.go:2208` `countReadyPods` で `status.ready` を数えます。:1238/:1293 は Pod 削除時に ready 数を差し引く処理です。
- DaemonSet：`daemon_controller.go:1235` `updateDaemonSetStatus` で `numberReady` を数えます。
- StatefulSet：`stateful_set_utils.go:468` `isRunningAndReady` は「Running かつ Ready」の判定で、順序付きのロールアウトやスケーリングの進行判断に使われます。

### 4. 未 Ready から Ready への遷移を検知して再キュー
- `replica_set.go:558`、`stateful_set.go:325`、`daemon_controller.go:664` の Pod update ハンドラで使われています。
- Pod が Ready に変わったとき、`minReadySeconds > 0` なら `minReadySeconds` 後に再同期をスケジュールします。こうして Available への遷移を拾います。

### 5. DaemonSet のローリングアップデート
- `daemon_controller.go:948` `podsShouldBeOnNode`：surge 時に、古い Pod が未 Ready なら新しい Pod の起動を待たずに消してよい、といった判断に使います。

### 6. サービスのエンドポイント
- `endpoints_controller.go:654`：Ready なら `Addresses`、未 Ready なら `NotReadyAddresses` に振り分けます。`publishNotReadyAddresses` が true なら Ready でなくても `Addresses` に入れます。
- EndpointSlice 側は `staging/src/k8s.io/endpointslice/utils.go:39` で同名の別実装を使い、`serving`/`ready` を決めます。

### 7. PodDisruptionBudget（PDB）
- `disruption.go:931` `countHealthyPods`：PDB の `currentHealthy` を数えます。PDB では「Ready = healthy」です。
- `pkg/registry/core/pod/storage/eviction.go:241`（内部型版 `pkg/api/pod`）：Eviction API で、未 Ready の Pod なら `unhealthyPodEvictionPolicy` に従って PDB を消費せずに退避を許可するかを判断します。

### 8. HPA
- `replica_calculator.go:343`：「Running かつ Ready」の Pod 数を数えます。

## テストコード
`test/e2e/...` と `test/integration/...` に多数の呼び出しがあります。用途はほぼすべて「Pod が Ready になる（またはならない）まで待つ」ことで、probe のテスト、StatefulSet/Deployment の待機、Service のテストなどです。

## まとめ
`IsPodReady` は「Ready condition が True か」を判定する最小の関数で、次の用途に使われています。
- **集計**：`readyReplicas`、PDB の healthy 数、HPA
- **トラフィック制御**：Endpoints
- **削除・退避の優先度**：スケールダウン時のソート、Eviction
- **可用性判定の土台**：`IsPodAvailable` と `minReadySeconds`

Kubernetes 全体で「Pod を使える状態か」を判断する共通の基準になっています。

## Answer 5

## `podutil.IsPodReady` の呼び出し元と用途

**定義**: `pkg/api/v1/pod/util.go:297`

```go
func IsPodReady(pod *v1.Pod) bool {
    return IsPodReadyConditionTrue(pod.Status)
}
```

Pod の `Ready` コンディションが `True` かどうかを返すだけの関数です。「この Pod はトラフィックを受けられる正常な Pod か」を判定するための共通部品として、主にコントローラーから呼ばれています。

`IsPodReady` という名前の関数は他に3つあります（`pkg/api/pod`、kubectl の `podutils`、`staging/.../endpointslice/util`）。ここでは `podutil` という別名でよく import される `pkg/api/v1/pod` 版について説明します。

### 主な呼び出し元（テスト以外）

| 分類 | 呼び出し元 | 用途 |
|---|---|---|
| **共通ユーティリティ** | `pkg/api/v1/pod/util.go` `IsPodAvailable` | Ready になってから `minReadySeconds` が経ったかで「Available」を判定する。その前提として Ready かを確認する |
| | `pkg/controller/controller_utils.go:719,761,855` の各 `Less` | Pod を並べ替える。スケールダウン時は Ready でない Pod を先に消し、利用可能な Pod を選ぶときは Ready な Pod を優先する |
| | `controller_utils.go:937` `podReadyTime` | Ready になった時刻を取り出す（並べ替えに使う） |
| | `controller_utils.go:1033` `nextPodAvailabilityCheck` | Ready な Pod が Available になるまでの残り時間を計算し、再チェックの時期を決める |
| **ReplicaSet** | `replica_set_utils.go:111` `calculateStatus` | `status.readyReplicas` を数える |
| | `replica_set.go:558` `updatePod` | Pod が Ready になったとき、`minReadySeconds` 経過後に再び処理するよう登録する |
| | `replica_set.go:957,964` `reportSortingDeletionAgeRatioMetric` | 削除する Pod の選び方に関するメトリクスを出す |
| **StatefulSet** | `stateful_set_utils.go:468` `isRunningAndReady` | 順番に Pod を作るとき、次の Pod に進む条件（Running かつ Ready）を判定する |
| | `stateful_set.go:325` `updatePod` | Ready になったら `minReadySeconds` 後に再び処理するよう登録する |
| **DaemonSet** | `daemon_controller.go:664` `updatePod` | Ready になったら再び処理するよう登録する |
| | `daemon_controller.go:948` `podsShouldBeOnNode` | 同じノードに Pod が重複したとき、どれを残すか決める |
| | `daemon_controller.go:1235` `updateDaemonSetStatus` | `numberReady` を数える |
| **Job** | `job_controller.go:2208` `countReadyPods` | `status.ready` を数える |
| | `job_controller.go:1238,1293` `deleteJobPods` / `deleteActivePods` | Pod を消すときに、Ready だった Pod の数を数える |
| **Endpoints** | `endpoints_controller.go:654` `addEndpointSubset` | Ready な Pod は `addresses`、Ready でない Pod は `notReadyAddresses` に分けて Endpoints に入れる（`publishNotReadyAddresses` が有効なら全部 `addresses` に入る） |
| **HPA** | `replica_calculator.go:343` `getReadyPodsCount` | Running かつ Ready な Pod を数えて、スケール計算に使う |
| **PDB** | `disruption.go:931` `countHealthyPods` | PDB の `currentHealthy` を数える（ここでは `apipod` という別名で import されている） |
| **API サーバー（Eviction）** | `pkg/registry/core/pod/storage/eviction.go:241` | Eviction API で、Pod を「健全」とみなすかの判定に使う。Ready でない Pod は `unhealthyPodEvictionPolicy` などの条件によって、PDB の残り枠を減らさずに退避できる |

このほか、統合テストや e2e テスト（`test/e2e/...`、`test/utils/conditions.go` など）の待機処理でも多数使われています。

### まとめ
用途は大きく次の3つです。
1. **数える**: 各ワークロードの `ready*` ステータスや、PDB の healthy 数
2. **選ぶ・並べる**: スケールダウンで消す Pod、残す Pod、Endpoints への振り分け
3. **再処理のタイミングを決める**: Ready になった時点から `minReadySeconds` を経て Available になるまでを追う

### 調べ方について
- [tool]（`[tool]`）で見つかった、テスト以外の直接の呼び出し元は18件でした。
- grep で照合したところ、`eviction.go:241` の呼び出しは[tool]に出ていませんでした（無名関数の中にあるためと思われます）。上の表には加えてあります。
- [tool]の結果は網羅を保証するものではありません。そのため、grep で確認できた `podutil.IsPodReady(` の呼び出し箇所を一次情報として扱いました。

## Answer 6

## 定義

`pkg/api/v1/pod/util.go:297`

```go
func IsPodReady(pod *v1.Pod) bool {
	return IsPodReadyConditionTrue(pod.Status)
}
```

Pod の `status.conditions` に `type: Ready` があり、その `status` が `True` かどうかを返すだけの関数です。Readiness Probe やコンテナの状態は直接見ません。その判定は kubelet が済ませて `Ready` condition に書き込んでいるので、この関数は書き込まれた結果を読むだけです。kubelet 本体からは呼ばれておらず、主な利用者は**コントロールプレーン側（各コントローラと API サーバー）**です。

## 主な呼び出し元と用途

### 1. Available の判定の前提（土台になる関数）
- `pkg/api/v1/pod/util.go:284` `IsPodAvailable`：Ready であることを前提に、`minReadySeconds` が経過したかを見て「Available」と判定します。Deployment、ReplicaSet、DaemonSet、StatefulSet の `availableReplicas` はこの判定に依存しています。
- `pkg/controller/controller_utils.go:1033` `nextPodAvailabilityCheck`：Ready になった Pod が Available になるまでの残り時間を計算し、再キューのタイミングを決めます。

### 2. ワークロードコントローラの status 集計
- **ReplicaSet** `replica_set_utils.go:111`：`readyReplicas` と `availableReplicas` を数えます。
- **DaemonSet** `daemon_controller.go:1235`：`numberReady` と `numberAvailable` を数えます。
- **StatefulSet** `stateful_set_utils.go:468` `isRunningAndReady`：Running かつ Ready かを判定します。順序どおりに Pod を作成・更新するときの「前の Pod が Ready になるまで待つ」制御に使われます。
- **Job** `job_controller.go:2208` `countReadyPods`：`status.ready` を数えます。1238 と 1293 では、削除した Pod のうち Ready だった数を記録しています。

### 3. スケールダウンで消す Pod の選定（ソート）
- `pkg/controller/controller_utils.go:719, 761, 855, 879`（`ActivePods`、`ActivePodsWithRanks` の `Less`）：**NotReady の Pod を先に削除候補にし**、Ready 同士なら Ready になってからの時間（`podReadyTime`）が短い Pod を優先します。ReplicaSet がレプリカを減らすときにどの Pod を消すかを決める中心的な処理です。
- `replica_set.go:957, 964`：削除する Pod を選ぶ際の補助判定にも使われています。

### 4. イベントハンドラで「Ready に変わった」ことを検出
- `replica_set.go:558`、`stateful_set.go:325`、`daemon_controller.go:664`：更新前は NotReady で更新後に Ready になった場合、`minReadySeconds > 0` なら一定時間後にもう一度 sync するよう予約します（Available への遷移を拾うため）。

### 5. DaemonSet のローリング更新（surge）
- `daemon_controller.go:948`：古い Pod が Ready でなくなっていれば、新しい Pod の Available を待たずに古い Pod をすぐ置き換えます。

### 6. Service のエンドポイント
- `pkg/controller/endpoint/endpoints_controller.go:654`：Ready の Pod は `addresses` に、そうでない Pod は `notReadyAddresses` に振り分けます（`publishNotReadyAddresses` が指定されていれば常に Ready として扱います）。
- EndpointSlice 側（`staging/src/k8s.io/endpointslice/utils.go:39`）では同じ名前の別実装 `endpointutil.IsPodReady` を使い、`serving` などの条件を決めています。

### 7. PodDisruptionBudget（PDB）
- `pkg/controller/disruption/disruption.go:931`：PDB の `currentHealthy` を数えます（Ready を「健全」とみなします）。
- `pkg/registry/core/pod/storage/eviction.go:241`：Eviction API の処理です。コメントにあるとおり「IsPodReady が現時点での IsHealthy の実装」です。NotReady の Pod は `unhealthyPodEvictionPolicy`（`AlwaysAllow` / `IfHealthyBudget`）に従って、PDB の残り予算を減らさずに追い出せる場合があります。

### 8. HPA
- `pkg/controller/podautoscaler/replica_calculator.go:343`：Running かつ Ready の Pod 数を数え、レプリカ数を計算する材料にします。

### 9. テスト
`test/e2e/...` と `test/integration/...` に多数あります。主に「Pod が Ready になるまで（あるいは NotReady になるまで）待つ」用途です。Probe、StatefulSet、PDB などのテストで使われています。

## 補足：同じ名前の別実装
同じ名前の関数がほかにもあり、依存関係を避けるためにそれぞれ別に実装されています。
- `staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:46`：kubectl 用です（e2e テストの framework もこちらを使っています）。
- `staging/src/k8s.io/endpointslice/util/controller_utils.go:360`：EndpointSlice ライブラリ用です。
- `pkg/api/pod/util.go:257`：v1 型ではなく内部 API 型（`api.Pod`）用です。

**まとめ**：`IsPodReady` は「kubelet が書いた Ready condition を、コントロールプレーンが判断材料として読む」ための共通関数です。主に次の 4 つの用途に使われています。
- レプリカの ready/available 数の集計
- スケールダウン時の削除順の決定
- トラフィックの振り分け（Endpoints）
- 退避の可否判定（PDB と Eviction）

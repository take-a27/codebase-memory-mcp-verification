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

`podutil.IsPodReady` は、Pod の `Status.Conditions` に `Ready=True` があるかを判定するだけの関数です。定義は `pkg/api/v1/pod/util.go:297` で、中身は `IsPodReadyConditionTrue(pod.Status)` を呼んでいるだけです。

主に **kube-controller-manager の各コントローラ**から呼ばれます。用途は大きく次の4つです。

- Ready なレプリカ数を数える
- 削除する Pod の優先順位を決める
- Service のエンドポイントに含めるか判定する
- `minReadySeconds` に基づく Available 判定の前提チェック

`podutil` という別名は複数のパッケージに使われているので注意が必要です。下の一覧は、eviction 以外はすべて `k8s.io/kubernetes/pkg/api/v1/pod` を import しています（import 文で確認済み）。

## 本体コード（pkg/）の主な呼び出し元

### 1. 共通コントローラユーティリティ（`pkg/controller/controller_utils.go`）
- **Pod の並び替え（L719〜879）**: ActivePods 系の比較関数で、Ready でない Pod を「削除候補として先」に並べます。スケールダウン時に、まだ Ready でない Pod から消すための処理です。
- **`podReadyTime`（L937）**: Ready のときだけ、Ready 条件の `LastTransitionTime` を返します。これも並び替えの基準に使われます。
- **`nextPodAvailabilityCheck`（L1033）**: Ready で `minReadySeconds > 0` の場合に、Available になるまでの残り時間を計算します。その時間後に再チェックするためのものです。

### 2. ReplicaSet（Deployment の下位）
- `replica_set_utils.go:111`: ステータスの `readyReplicas` を数え、Ready なものはさらに `IsPodAvailable` で `availableReplicas` も数えます。
- `replica_set.go:558`: Pod 更新イベントで「NotReady から Ready に変わった」かつ `MinReadySeconds > 0` のとき、Available 判定のために遅延して再キューします。
- `replica_set.go:957, 964`: 一番新しい Ready Pod を探したり、NotReady の Pod を判別したりします（ステータスや条件の計算用）。

### 3. StatefulSet
- `stateful_set.go:325`: ReplicaSet と同じく、Ready への遷移を検知して `MinReadySeconds` 後に再キューします。
- `stateful_set_utils.go:468`: `isRunningAndReady` の中で「Running かつ Ready」を判定します。OrderedReady の順次起動や、ローリングアップデートを進めるかどうかの判断に使われます。

### 4. DaemonSet（`daemon_controller.go`）
- L664: Ready への遷移を検知して再キューします。
- L948: ローリングアップデートで、古い Pod が NotReady なら優先して置き換えます。
- L1235: ステータスの `numberReady` / `numberAvailable` を数えます。

### 5. Endpoints コントローラ（`endpoint/endpoints_controller.go:654`）
- Pod の IP を `Addresses`（Ready）に入れるか `NotReadyAddresses` に入れるかを振り分けます。`publishNotReadyAddresses`（`tolerateUnreadyEndpoints`）が true なら、Ready でなくても Ready 側に入れます。Service のトラフィックを Ready な Pod にだけ流すための中心的な判定です。

### 6. Job コントローラ（`job/job_controller.go`）
- L2208 `countReadyPods`: Job ステータスの `ready` フィールドを数えます。
- L1238, L1293: Pod を削除したとき、そのうち Ready だった数を数えてステータスの整合性を保ちます。

### 7. HPA（`podautoscaler/replica_calculator.go:343`）
- Running かつ Ready な Pod の数を数えます。オブジェクト／外部メトリクスで「Pod 1つあたり」の値を計算する際の Pod 数に使われます。

### 8. Eviction API（`pkg/registry/core/pod/storage/eviction.go:241`）
- このファイルの `podutil` だけは **`k8s.io/kubernetes/pkg/api/pod`**（内部 API 型用の同名関数）を指していて、上の関数とは別物です。
- 用途は PodDisruptionBudget の判定です。NotReady（＝unhealthy）な Pod は、`UnhealthyPodEvictionPolicy=AlwaysAllow` の場合、または PDB がすでに満たされている場合に、disruption の予算を消費せずに退去させます。

## テストコード（test/）
e2e テストと統合テストでも多く使われています。主な用途は、Pod が Ready になるまで（またはならないことを）待つことです。
- `test/e2e/common/node/container_probe.go`: readiness probe のテスト
- `test/e2e/apps/wait.go`, `disruption.go`: ワークロード／PDB のテスト
- `test/e2e/network/service.go`: Service のテスト
- `test/utils/conditions.go`, `test/integration/deployment/util.go`: 共通ヘルパー

## 補足
- よく似た名前の関数が別に3つあり、これらは上の一覧に含めていません。
  - `staging/src/k8s.io/kubectl/pkg/util/podutils.IsPodReady`（kubectl 用）
  - `staging/src/k8s.io/endpointslice/util.IsPodReady`（EndpointSlice 用）
  - `pkg/api/pod.IsPodReady`（内部 API 型用。eviction が使っているもの）
- 一覧はテスト以外のファイルを grep した結果です。vendor 配下と、`podutil` 以外の別名で import しているファイルは数えていません。

## Answer 3

## 定義

`podutil` は通常 `k8s.io/kubernetes/pkg/api/v1/pod` の別名です。関数本体は次の 1 行だけです（`pkg/api/v1/pod/util.go:297`）。

```go
func IsPodReady(pod *v1.Pod) bool {
	return IsPodReadyConditionTrue(pod.Status)
}
```

`pod.Status.Conditions` の中に `PodReady` 条件があり、その値が `True` なら true を返します。Phase（Running かどうか）は見ません。そのため、Phase も確認したい呼び出し元は自分で併せてチェックしています。

注意点として、同じ名前の関数がほかに 3 つあります。
- `pkg/api/pod/util.go:257`：内部型 `api.Pod` 用
- `staging/.../kubectl/pkg/util/podutils/podutils.go:46`：kubectl 用
- `staging/.../endpointslice/util/controller_utils.go:360`：EndpointSlice 用

`eviction.go` だけは `podutil` という別名で `pkg/api/pod`（内部型版）を import しています。

## 主な呼び出し元と用途（本番コード）

### 1. ステータスの Ready / Available 数の集計
コントローラーが `.status.readyReplicas` などを計算するときに使います。
- `pkg/controller/replicaset/replica_set_utils.go:111`：`readyReplicasCount` を数えます。Ready なら続けて `IsPodAvailable`（minReadySeconds を考慮）で Available 数も数えます。Deployment のステータスもこの結果を元にしています。
- `pkg/controller/daemon/daemon_controller.go:1235`：DaemonSet の `numberReady` / `numberAvailable` を数えます。
- `pkg/controller/job/job_controller.go:2208`（`countReadyPods`）：Job の `.status.ready` を数えます。

### 2. スケールダウン時に消す Pod の優先順位
`pkg/controller/controller_utils.go` にあるソート用の型で使います。
- `ActivePods.Less`（:761）と `ActivePodsWithRanks.Less`（:855）：「Not ready < ready」の順に並べ、Ready でない Pod を先に削除候補にします。ReplicaSet や Job が余った Pod を消すときに使います。
- `ByLogging.Less`（:719）：逆に Ready な Pod を優先します。ログ取得対象の Pod を選ぶためのものです。
- `podReadyTime`（:937）：Ready になった時刻を取得します。Ready の Pod 同士で順位を決めるのに使います。

### 3. minReadySeconds 後の再チェックのタイミング
Pod が「NotReady から Ready に変わった」ことを検出し、minReadySeconds 経過後に Available になったかを確かめるため、もう一度キューに入れます。
- `pkg/controller/replicaset/replica_set.go:558`
- `pkg/controller/statefulset/stateful_set.go:325`
- `pkg/controller/daemon/daemon_controller.go:664`
- `controller_utils.go:1033` の `nextPodAvailabilityCheck`：次に確認する時刻を計算します。

### 4. ローリングアップデートの判断
- `daemon_controller.go:948`：surge 更新で、古い Pod が Ready でなければすぐ削除して新しい Pod に置き換えます。
- `pkg/controller/statefulset/stateful_set_utils.go:468`（`isRunningAndReady`）：Phase が Running かつ Ready かを判定します。StatefulSet が順番に作成・更新を進めるときの条件です。

### 5. Service のトラフィック振り分け
- `pkg/controller/endpoint/endpoints_controller.go:654`：Ready の Pod（または `publishNotReadyAddresses` が有効な場合）は `Addresses` に入れます。それ以外は `NotReadyAddresses` に入れます。つまり、Ready でない Pod にトラフィックを流さないための判定です。

### 6. HPA のレプリカ数計算
- `pkg/controller/podautoscaler/replica_calculator.go:343`：Running かつ Ready の Pod 数を数えます。

### 7. Eviction API と PodDisruptionBudget
- `pkg/registry/core/pod/storage/eviction.go:241`（内部型版）：コメントに「IsPodReady is the current implementation of IsHealthy」とあり、Ready を「健全」とみなしています。Ready でない Pod は、`unhealthyPodEvictionPolicy: AlwaysAllow` の場合、PDB の disruptionsAllowed を減らさずに退避できます。

### テストでの利用
e2e テスト（`test/e2e/common/node/container_probe.go`、`test/e2e/apps/wait.go`、`disruption.go` など）や integration テストでは、readiness probe の検証や「Pod が Ready になるまで待つ」ポーリングに大量に使われています。

## まとめ
`IsPodReady` は「kubelet が報告した Ready 条件が True かどうか」を共通の方法で判定する関数です。主な用途は次のとおりです。
- ワークロード系コントローラーが Ready / Available 数を数える
- どの Pod から削除するかを決める
- ローリングアップデートを進めてよいか判断する
- Service の Endpoints に Pod を含めるかを決める
- HPA や PDB（Eviction）で「健全な Pod」を数える

minReadySeconds を考慮した「Available」の判定は、その上に重ねた `IsPodAvailable` が担当しています。

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

## 結論

`podutil.IsPodReady` は `pkg/api/v1/pod/util.go:297` で定義されています。中身は `IsPodReadyConditionTrue(pod.Status)` を呼ぶだけで、Pod の `Ready` condition が `True` かどうかを返します。

本番コードからの呼び出しは、ほぼすべて `pkg/controller/` 配下のコントローラです。使い方は次の 4 種類に分かれます。

1. status の Ready 数を数える
2. スケールダウン時にどの Pod を消すか決める
3. Ready への変化をきっかけに再処理をスケジュールする
4. Service / PDB / HPA で「健全な Pod」を判定する

## 主な呼び出し元と用途

### 1. Ready 数の集計（status 更新）
| 呼び出し元 | 用途 |
|---|---|
| `pkg/controller/replicaset/replica_set_utils.go:111` | `readyReplicas` を数える。Ready の Pod だけ、さらに `IsPodAvailable` で `availableReplicas` を判定する |
| `pkg/controller/daemon/daemon_controller.go:1235` | DaemonSet の `numberReady` / `numberAvailable` を数える |
| `pkg/controller/job/job_controller.go:2208` (`countReadyPods`) | Job の `status.ready` を数える |
| `pkg/controller/job/job_controller.go:1238, 1293` | Pod 削除時に、消した Pod のうち Ready だったものを数える |
| `pkg/controller/statefulset/stateful_set_utils.go:468` | `isRunningAndReady`。StatefulSet が Pod を順番に作るときや、Ready 数の判定に使う |

### 2. 削除する Pod の優先順位付け（`pkg/controller/controller_utils.go`）
- 719〜725行、761〜768行、855〜879行は、`ActivePods` / `ActivePodsWithRanks` などのソート処理です。スケールダウン時に「Ready でない Pod を先に消す」ために使われます。Ready 同士なら、Ready になった時刻を比べます。
- 937行の `podReadyTime` は、Ready の Pod について Ready condition の `LastTransitionTime` を取り出します。
- 1033行の `nextPodAvailabilityCheck` は、`minReadySeconds` が経って Available になるまでの残り時間を計算します。

### 3. Ready への変化をきっかけにした再処理
- `pkg/controller/replicaset/replica_set.go:558` と `pkg/controller/statefulset/stateful_set.go:325`：Pod が「Ready でない → Ready」に変わり、かつ `MinReadySeconds > 0` のとき、`minReadySeconds` 後にもう一度処理するようキューに入れます（Available の判定のため）。
- `pkg/controller/daemon/daemon_controller.go:664`：Ready への変化を検知します。
- `pkg/controller/daemon/daemon_controller.go:948`：ローリングアップデート（surge）中に古い Pod が Ready でなくなったら、すぐ置き換えます。
- `pkg/controller/replicaset/replica_set.go:957, 964`：削除する Pod の年齢比率をメトリクスとして出すときに使います（Ready の Pod だけが対象）。

### 4. Service、オートスケール、Disruption（中断予算）
- `pkg/controller/endpoint/endpoints_controller.go:654`：Ready の Pod か `tolerateUnreadyEndpoints`（Service の `publishNotReadyAddresses`）が有効な場合だけ、Endpoints の ready なアドレスとして登録します。
- `pkg/controller/podautoscaler/replica_calculator.go:343`：HPA がレプリカ数を計算するとき、Running かつ Ready の Pod を数えます。
- `pkg/controller/disruption/disruption.go:931`：別名 `apipod` で呼んでいます。PDB の `currentHealthy` を数えるために使います。

### テスト・e2e
`test/e2e/common/node/container_probe.go`（readiness probe の検証）、`test/e2e/apps/wait.go`、`disruption.go`、`test/e2e/network/service.go`、`test/utils/conditions.go`、`test/integration/deployment/util.go` などでは、「Pod が Ready になるまで待つ」ための条件として使われています。

## 注意：同じ名前の別の関数
- `pkg/registry/core/pod/storage/eviction.go:241` にも `podutil.IsPodReady` がありますが、これは別名が同じだけで、実体は **`pkg/api/pod`**（内部 API 型 `api.Pod` 用）の `IsPodReady` です。Eviction API で、Ready でない Pod を PDB の判定でどう扱うか（`UnhealthyPodEvictionPolicy`）を決めています。
- 同じ名前の関数はほかに `staging/.../kubectl/pkg/util/podutils`（kubectl 用）と `staging/.../endpointslice/util`（EndpointSlice 用）にもあります。

まとめると、`IsPodReady` は Ready condition を判定する共通部品です。ワークロードの status 報告、スケールダウン時の削除順、Service へのトラフィック振り分け、PDB と HPA の健全性判定の土台になっています。

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

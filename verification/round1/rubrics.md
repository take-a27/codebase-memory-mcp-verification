# 採点観点（本番実行の前に確定。以後変更しない）

対象: kubernetes v1.37.2（c50e5395f1ae2d4a2c05f28fdcc38c0916cc1a32）。
T4〜T6 の正解の一覧は `ground_truth.md`（ソースから機械的に抽出）を正とする。

## 共通の採点基準（1〜5）

- **5**: 正解の要点をほぼ網羅しており、誤りがない。具体的なファイルや関数を挙げている。
- **4**: 要点の大半を押さえている。抜けが少しあるか、ごく軽い不正確さがある。
- **3**: 主要な点はあるが、目立つ抜けか、軽い誤りがある。
- **2**: 要点の一部しかない、または誤りが複数ある。
- **1**: 的外れ、または重大な誤りがある（存在しない関数やファイル、逆の説明、使われている関数を「未使用」と断定する、など）。

正解の要点に載っていなくても、ソースで確認できる妥当な指摘は加点してよい。存在しないファイルや関数、誤った呼び出し関係は減点する。

---

## T1: `podutil.IsPodReady` の呼び出し元と用途

**正解の要点**
- **K1 定義**: `pkg/api/v1/pod/util.go:297`。`IsPodReadyConditionTrue` を経由して、Pod の `Ready` condition が `True` かどうかを返す薄いラッパー。同じファイルの `IsPodAvailable`（:283、minReadySeconds を考慮）からも使われている。
- **K2 コントローラーの共通処理**: `pkg/controller/controller_utils.go`。Pod の並び替え（`ByLogging`、`ActivePods`、`ActivePodsWithRanks` の `Less`。スケールダウン時に Ready でない Pod から削除する、など）、`podReadyTime`、`nextPodAvailabilityCheck`。
- **K3 ワークロードのコントローラー**: ReplicaSet（`replica_set_utils.go:111` で ready / available を数えて status に反映、`replica_set.go:558` で Ready になったときに minReadySeconds 後に再キュー）、StatefulSet（`stateful_set_utils.go:468` の running かつ ready の判定、`stateful_set.go:325`）、DaemonSet（`daemon_controller.go:664` など。ローリングアップデートと status）、Job（`job_controller.go` で ready の Pod を数える）。
- **K4 サービスのトラフィックと PDB**: `pkg/controller/endpoint/endpoints_controller.go:654`（Ready な Pod だけを ready なアドレスにする。`tolerateUnreadyEndpoints` の場合は例外）、`pkg/controller/disruption/disruption.go:931`（`apipod` という別名で import。PDB の健全な Pod 数を数える）。
- **K5 Eviction API**: `pkg/registry/core/pod/storage/eviction.go:241` は、`podutil` という別名で、内部型向けの `k8s.io/kubernetes/pkg/api/pod` にある同名の関数を呼んでいる（v1 版の呼び出し元ではない）。これを呼び出し元として挙げても、別の関数だと区別していても、K5 を満たしたとみなす。
- **K6 HPA**: `pkg/controller/podautoscaler/replica_calculator.go:343`。メトリクスからレプリカ数を計算するときに、Ready な Pod だけを数える。

**補足（加点の対象）**: kubectl や e2e テストでは、staging にある別実装の `podutils.IsPodReady`（`staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:46`）が使われており、同名の関数が複数存在することを区別できている。

---

## T2: `pkg/controller` 配下の重複処理とまとめ方

**正解の要点**（実在する重複パターン。いずれかを根拠付きで挙げていれば該当）
- **K1 ワーカーループの定型処理**: `processNextWorkItem` が 27 ファイル、`worker(ctx)` が 22 ファイルにあり、workqueue の Get / Done / Forget / AddRateLimited と、エラー時のリトライが各コントローラーでほぼ同じ形で書かれている。`Run` での `WaitForNamedCacheSync` と、ワーカーの起動も同様。
- **K2 イベントハンドラーと enqueue**: `KeyFunc(obj)` でキーを作る enqueue 処理（約 25 箇所）や、`resolveControllerRef`（6 つのコントローラーに別々の実装）、`addPod` / `updatePod` / `deletePod` で owner を解決して enqueue する処理が重複している。
- **K3 Ready になったときの minReadySeconds 再キュー**: `replicaset/replica_set.go:558`、`statefulset/stateful_set.go:325`、`daemon/daemon_controller.go:664-666` が、「Ready でなかった Pod が Ready になり、`MinReadySeconds > 0` なら一定時間後に再キュー」という同じロジックを持つ。ready と available を数える処理も似ている。
- **K4 Pod の並び替え**: `controller_utils.go` の `ByLogging`、`ActivePods`、`ActivePodsWithRanks` の `Less` が、ノードの割り当て → phase → Ready → Ready になった時刻、という比較をほぼ同じ順で重複して書いている。
- **K5 既存の共通化を踏まえた提案**: すでに共通化されている部品（`controller_utils.go` の `ControllerExpectations`、`PodControlInterface` / `RealPodControl`、`ControllerRefManager` など）を認識したうえで、現実的なまとめ方（共通ヘルパー、ジェネリクス、ベースのワーカーなど）と、そのリスク（コントローラーごとの細かい差、テストへの影響）を提案している。

---

## T3: Pod の作成からバインドまでの流れ

**正解の要点**
- **K1 API サーバーでの作成**: Pod の作成リクエストを `pkg/registry/core/pod/strategy.go`（`PrepareForCreate`、バリデーション）と `pkg/registry/core/pod/storage/storage.go` が処理し、etcd に保存する。この時点では `spec.nodeName` は空。
- **K2 スケジューラーのキューへの投入**: `pkg/scheduler/eventhandlers.go` の `addAllEventHandlers`（:624）で informer のハンドラーを登録し、`addPodToSchedulingQueue`（:238）が未割り当ての Pod を `pkg/scheduler/backend/queue/scheduling_queue.go` の `PriorityQueue.Add`（:873）に入れる（activeQ / backoffQ / unschedulable pods）。
- **K3 取り出しとスケジューリングサイクル**: `pkg/scheduler/scheduler.go` の `Run`（:538）が `ScheduleOne` を回す。`pkg/scheduler/schedule_one.go` の `ScheduleOne`（:67）が `PriorityQueue.Pop`（:1335）で Pod を取り出し、`scheduleOnePod` → `schedulingCycle`（:169）へ進む。
- **K4 ノードの選択**: `schedulePod`（:572）→ `findNodesThatFitPod`（:630。PreFilter / Filter、`findNodesThatPassFilters`）→ `prioritizeNodes`（:945。PreScore / Score）→ ノードを選ぶ。候補がなければ PostFilter（preemption）と `handleSchedulingFailure`。
- **K5 assume と Reserve / Permit**: `assumeAndReserve`（:315）と `assume`（:1060）で、スケジューラーのキャッシュ上で先にノードに割り当てる。Reserve と Permit の拡張点を通る。
- **K6 バインディングサイクル**: `runBindingCycle` / `bindingCycle`（:399。非同期の goroutine）→ PreBind → `bind`（:1100）→ `DefaultBinder.Bind`（`pkg/scheduler/framework/plugins/defaultbinder/default_binder.go:52`）が `v1.Binding` を作って `pods/binding` サブリソースに POST する → API サーバー側の `BindingREST.Create`（`storage.go:177`）→ `assignPod`（:290）/ `setPodNodeAndMetadata`（:213）が `spec.nodeName` を設定する → PostBind。

**補足（加点の対象）**: その後、kubelet が自分のノードに割り当てられた Pod を watch して起動する、という流れに触れている。

---

## T4: `pkg/api/v1/pod.IsPodAvailable` に引数を追加するときの影響範囲

**正解の要点**（一覧は `ground_truth.md` の T4）
- **K1 直接の呼び出し元（本番コード）**: daemon（`daemon_controller.go` の `podsShouldBeOnNode` と `updateDaemonSetStatus`、`update.go` の `rollingUpdate` の 6 か所）、replicaset（`replica_set_utils.go` の `calculateStatus`）、statefulset（`stateful_set_utils.go` の `isRunningAndAvailable` と `isUnavailable`）。この 3 つのコントローラー・計 11 か所。
- **K2 直接の呼び出し元（テスト・e2e のヘルパー）**: `test/utils/deployment.go:65`、`test/e2e/apps/daemon_set.go`（644、1202）、`test/e2e/network/loadbalancer.go:1432`。
- **K3 波及（2 段目）**: statefulset の `isRunningAndAvailable` と `isUnavailable` は `minReadySeconds` を受け取るラッパーで、`stateful_set_control.go` の多数の箇所から呼ばれている。ラッパーのシグネチャを変えるなら、そこまで波及する。
- **K4 別物との区別**: kubectl の `podutils.IsPodAvailable`（staging）、`test/e2e/framework/daemonset/fixtures.go` が使う kubectl 版などは、別の関数なので修正対象ではない。これを区別できている。
- **K5 進め方の提案**: 引数を追加する代わりに新しい関数を足す、などの現実的な選択肢やリスク（e2e も壊れる、など）に触れている。

## T5: `pkg/controller` 配下の未使用関数

**正解の要点**（一覧は `ground_truth.md` の T5。kubernetes は lint で未使用コードを取り除いているため、本当に未使用の関数はほとんどない）
- **K1 本当に未使用**: `pkg/controller/nodeipam/ipam/sync/sync.go` の `IsValidMode`（本番コードからもテストからも参照されていない）。
- **K2 テストからだけ使われている**: `MakeVolumeAttributesClass`、`NewStatefulPodControlFromManager`、`SetTestMode`、`NewFakeHistory`、`NewPersistentVolumeControllerOptions`。「本番では未使用だがテストが使っているので、消すならテストも直す」と区別できている。
- **K3 誤検出をしない**: `ground_truth.md` で same-file に分類された関数（定義ファイルの中で使われている）や、他のパッケージから使われている関数を「未使用」と断定していない。インターフェースのメソッド、リフレクション、生成コード経由の参照など、静的に追いにくい使われ方に注意を払っている。
- **K4 テスト用ヘルパーの扱い**: `testing/`、`testutil/`、`test/`、`fake_*` のパッケージの関数は、テスト用なので削除候補ではない、と扱えている。
- **K5 根拠の示し方**: 候補ごとに、どう確認したか（参照箇所の検索結果など）を示している。

採点の注意: 「未使用」と挙げた関数は、ソースで参照を検索して確かめること。使われている関数を未使用と断定していたら、1 件ごとに大きく減点する。

## T6: 既存の Filter プラグインの一覧

**正解の要点**（一覧は `ground_truth.md` の T6）
- **K1 網羅**: `pkg/scheduler/framework/plugins/` 配下の 15 個（DeferredPodScheduling、DynamicResources、InterPodAffinity、NodeAffinity、NodeDeclaredFeatures、NodeName、NodePorts、Fit（NodeResourcesFit）、NodeUnschedulable、CSILimits（NodeVolumeLimits）、PodTopologySpread、TaintToleration、VolumeBinding、VolumeRestrictions、VolumeZone）を挙げている。
- **K2 説明の正しさ**: それぞれが何をチェックしているかの一言の説明が、実装と合っている。
- **K3 プラグイン以外との区別**: `framework/runtime/instrumented_plugins.go` の `instrumentedFilterPlugin`（計測用のラッパー）、`pkg/scheduler/testing/framework/fake_plugins.go` のテスト用の偽プラグイン、`HTTPExtender.Filter`（Extender で、プラグインではない）を、本物のプラグインと混同していない。
- **K4 追加の手がかり**: インターフェースの定義場所（`staging/src/k8s.io/kube-scheduler/framework/interface.go` の `FilterPlugin`）や、登録の仕組み（registry）など、新しく追加するための手がかりに触れている。

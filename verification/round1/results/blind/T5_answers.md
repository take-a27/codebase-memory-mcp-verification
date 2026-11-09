# T5 の回答（6 件）

## Answer 1

pkg/controller 配下（`_test.go` を除く）で、削除してよさそうな関数は次の 3 つです。どれもリポジトリ全体を `git grep` しても定義行とコメント行しか出てきません。コードは編集していません。

## 削除候補

### 1. `daemon.NewPod` — `pkg/controller/daemon/daemon_controller.go:1447`
```go
func NewPod(ds *apps.DaemonSet, nodeName string) *v1.Pod
```
- **[tool]上:** 呼び出し・参照（CALLS / USAGE / CALL_REFERENCE）が 1 件もありません。
- **grep:** `NewPod` という名前はよく出てきますが、すべて別の関数 `testutil.NewPod(name, node)` の呼び出しです。daemon パッケージの中（テストを含む）にも、daemon パッケージを import しているファイルにも `.NewPod(` はありません。
- **注意:** 中で呼んでいる `util.AddOrUpdateDaemonPodTolerations` はほかの場所でも使われているので、`NewPod` だけ消せば済みます。

### 2. `protectionutil.MakePod` — `pkg/controller/util/protectionutil/wrappers.go:30`
```go
func MakePod() *PodWrapper
```
- **[tool]上:** 呼び出しは 0 件です。
- **grep:** パッケージ外から呼ばれている `protectionutil.*` は `IsDeletionCandidate`、`NeedToAddFinalizer`、`MakePersistentVolume`、`MakePersistentVolumeClaim`、`MakeVolumeAttributesClass` の 5 つだけで、`MakePod` は 0 件です。パッケージ内でも使われていません。
- **補足:** `PodWrapper` 型とそのメソッド（`Obj`、`Name`、`UID`、`SchedulerName`、`Namespace` など）を作れるのは `MakePod` だけなので、これらもまとめて消せる可能性が高いです。ほかで出てくる `PodWrapper` は `pkg/scheduler/testing` にある同名の別の型です。メソッドは今回の集計に入れていないので、消す前に `PodWrapper` を参照している箇所を確認してください。

### 3. `sync.IsValidMode` — `pkg/controller/nodeipam/ipam/sync/sync.go:87`
```go
func IsValidMode(m NodeSyncMode) bool
```
- **[tool]上:** 入ってくる辺も出ていく辺もありません。
- **grep:** 定義以外にヒットせず、`sync_test.go` でも使われていません。
- **もっと大きな話:** `k8s.io/kubernetes/pkg/controller/nodeipam/ipam/sync` パッケージそのものを import している Go ファイルが、自分自身のテスト以外にありません。パスが出てくるのは `cmd/cloud-controller-manager/.import-restrictions:28` だけです。関数 1 つではなく、パッケージごと削除できるか検討する価値があります。その場合は `.import-restrictions` の該当行も整理が必要です。

## 候補から外したもの
- **`init` 関数 32 個:** 呼び出し元がないのは Go の仕様どおりです（`zz_generated.conversion.go` や scheme の登録など）。
- **`podautoscaler.init`:** テストファイル（`horizontal_test.go`）の中にあります。

## 調べ方と、この結果で言えないこと
- **手順:** [tool]（2026-11-08 12:18 時点のフルインデックス）で、test 以外の関数 1083 個のうち被参照辺が 0 のものを抽出しました。その結果を `git grep` で、パッケージ名付きの呼び出し・パッケージ内の呼び出し・import 元に分けて確かめています。インデックスの抜けを調べたところ、3 ファイルとも問題は記録されていませんでした。pkg/controller 内で解析が一部失敗しているのはテストファイル 5 つだけで、候補の判断には影響しません。
- **メソッドは対象外:** 対象は通常の関数だけです。メソッドはインターフェースを満たすために置かれていることが多く、参照がなくても未使用とは言えないため外しました。メソッドまで見るなら別の方法で調べる必要があります。
- **外部から使われている可能性:** `k8s.io/kubernetes` はふつう外部から import されないので、リポジトリの外で使われているとは考えにくいです。ただ、フォークや下流のプロジェクトで使っている可能性はゼロではありません。
- **reflection での呼び出し:** これは静的解析では検出できません。
- **確実に確かめるには:** 実際に消すときは、`go build ./... && go vet ./pkg/controller/...` が通るか確認するのが確実です。

## Answer 2

# `pkg/controller` の未使用関数の調査結果

コードは編集していません。作業ツリーは clean のままです。

## 調べ方
1. **staticcheck の U1000 チェック**(v0.8.2):未使用の非公開関数・型・フィールドを探すチェックで、テストファイルも対象です。`./pkg/controller/...` 全体で**検出は 0 件**でした。手元の v0.4.7 は Go 1.26 でパニックしたため、モジュールキャッシュにあった v0.8.2 をネットワーク無しでビルドして使いました。
2. **公開関数**:staticcheck では判定できないので、[tool]で「テスト以外からの呼び出しが 0 件」の関数を抽出しました。そのうえで、リポジトリ全体(cmd / pkg / test / staging / plugin)を grep して実際の参照を確認しています。
3. [tool]上は未使用に見えても誤検出だったものは除外しました。
   - `NewAlpha/Beta/GAClusterTrustBundlePublisher` は `cmd/kube-controller-manager/app/certificates.go:317-319` で関数値として使われていました。
   - `RegisterConversions` や `fuzzer.Funcs` などの生成コードも除外しています。

## 削除候補

### A. どこからも参照されていない(確度:高)

| 対象 | 根拠 |
|---|---|
| **`pkg/controller/nodeipam/ipam/sync` パッケージ全体**(`sync.go`。`New`, `NodeSync`, `IsValidMode` など) | このパッケージを import している Go ファイルが、vendor 以外に1つもありません(`grep "nodeipam/ipam/sync"` が 0 件)。使っているのは同じディレクトリの `sync_test.go` だけです。`IsValidMode`(`sync.go:87`)はテストからも呼ばれていません。 |
| `daemon.NewPod`(`pkg/controller/daemon/daemon_controller.go:1447`) | リポジトリ内に呼び出し元がありません。grep に出る `NewPod(` は全部 `testutil.NewPod` という別の関数でした。 |
| `protectionutil.MakePod` と `PodWrapper` 型・メソッド一式(`pkg/controller/util/protectionutil/wrappers.go:26-95` 付近) | `protectionutil.MakePod` の参照は 0 件です。同名の `st.MakePod`(`pkg/scheduler/testing`)は別物です。`PodWrapper` は `MakePod` からしか作られないので、型ごと未使用です。[tool]上はスケジューラ側の同名メソッドと混同されて「使用あり」に見えていました。 |

### B. テストからしか呼ばれていない(確度:中。テスト用ヘルパーとして残す判断もあり得ます)

| 対象 | 参照元 |
|---|---|
| `replication.SetCondition` / `RemoveCondition`(`pkg/controller/replication/replication_controller_utils.go:37,47`) | 自分自身のユニットテスト(`replication_controller_utils_test.go`)だけです。テストを消すだけのために存在している状態に近いです。 |
| `util/node.DeletePods`(`pkg/controller/util/node/controller_utils.go:45`) | `node_lifecycle_controller_test.go:988` だけです。 |
| `statefulset.NewStatefulPodControlFromManager`(`stateful_pod_control.go:74`) | statefulset のテスト2ファイルだけです。フェイクを差し込む仕組みとして使われているので、**残すのが妥当**です。 |

テスト用ヘルパー(`protectionutil.MakePersistentVolume` や `NewPersistentVolumeControllerOptions` など)はテストや統合テストで使われているので、候補から外しました。

## 注意点
- `k8s.io/kubernetes/pkg/...` はリポジトリの外から import することが公式にはサポートされていませんが、外部のプロジェクトが使っている可能性はゼロではありません。
- 公開関数の判定は[tool]と grep に頼っています。reflection や、関数値を生成コード経由で渡すような参照は見落とす可能性があります。最終確認として、削除後に `go build ./... && go vet ./pkg/controller/...` を通すことをおすすめします。
- インデックスの取りこぼしは `pkg/controller` 内のテストファイル5つ(`device_taint_eviction_test.go` など)だけで、根拠にしたファイルには記録上の問題はありませんでした。
- 作業用の一時ファイルが `/tmp/cbmtools/staticcheck`、`/tmp/cbmtools2/`(空)、`/tmp/sc*.txt`、`/tmp/dc.txt`、`/tmp/exported.txt`、`/tmp/prodcalled.txt` に残っています。不要なら削除してください。

削除を進める場合は、A の `ipam/sync` パッケージ(テストごと)が一番効果が大きく、安全です。

## Answer 3

## 結論

`pkg/controller` で、本番コードからもテストからもどこからも呼ばれていない関数は **27 件** ありました。中身を確認した結果、ほとんどは削除してよさそうです。コードは編集していません（ツールのビルドも `/tmp` で行い、リポジトリは `git status` で clean のままです）。

### 調べ方
1. **staticcheck U1000**（リポジトリ指定の v0.7.0）→ **0 件**。小文字始まりの非公開関数で使われていないものは、CI のチェックがあるため残っていません。
2. **`deadcode`**（x/tools v0.42.0）で、プログラム全体を対象に「どこからか呼ばれうるか」を解析しました。
   - `./cmd/...` だけを起点にした場合：88 件
   - テストも起点に含めた場合（`-test ./cmd/... ./pkg/... ./test/... ./plugin/...`）：**27 件**。これを候補にしました。
3. 候補ごとに `git grep` で、staging を含むリポジトリ全体の参照を確認しました。`pkg/controller` には `//go:build` で OS 限定になっているファイルがないので、darwin で解析しても見落としはありません。

---

## A. 削除してよさそうなもの（確度が高い順）

| # | 対象 | 根拠 |
|---|---|---|
| 1 | `ControllersByCreationTimestamp` 型と Len/Swap/Less（`pkg/controller/controller_utils.go:1216-1225`） | 定義以外の参照が全リポジトリで 0 件。隣の `ReplicaSetsByCreationTimestamp` などは使われているので、ReplicationController 版だけ取り残されたと思われます。 |
| 2 | `ByLogging` 型と Len/Swap/Less（`pkg/controller/controller_utils.go:704-`） | `k8s.io/kubernetes` 内からの参照は 0 件。実際に使われているのは kubectl 側にある別の定義 `staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:91` で、こちらはそのコピーが残っているだけです。 |
| 3 | `daemon.NewPod`（`pkg/controller/daemon/daemon_controller.go:1447`） | 呼び出しは 0 件。同じ名前の `testutil.NewPod` と `attachdetach/testing.NewPod` は別パッケージの別の関数です。 |
| 4 | `sync.IsValidMode`（`pkg/controller/nodeipam/ipam/sync/sync.go:87`） | 呼び出しは 0 件。 |
| 5 | `(*SVMController).Name`（`pkg/controller/storageversionmigrator/storageversionmigrator.go:111`） | kube-controller-manager は `NewSVMController(...).Run` しか使っていません（`cmd/kube-controller-manager/app/storageversionmigrator.go:94`）。このメソッドを必要とする interface に代入している箇所もありません。 |
| 6 | `(*PersistentVolumeControllerOptions).AddFlags`（`pkg/controller/volume/persistentvolume/options/options.go:66`） | 同じフラグ（`--pvclaimbinder-sync-period` など）は `cmd/kube-controller-manager/app/options/persistentvolumebindercontroller.go:36` で別に登録されていて、こちらは重複した古い実装です。型と `NewPersistentVolumeControllerOptions` は `test/integration/volume/attach_detach_test.go:473` で使われているので残し、**削除は `AddFlags` だけ**にしてください。 |
| 7 | `protectionutil` のテスト用ヘルパーの一部（`pkg/controller/util/protectionutil/wrappers.go`） | **`PodWrapper` は丸ごと未使用**です（`MakePod` と Obj/Name/UID/SchedulerName/Namespace/Terminating/PVC/Annotation/Annotations の計 10 個）。`PersistentVolumeClaimWrapper` は `Make`/`Obj`/`Name` は使われていますが、`Namespace`/`Annotation`/`VolumeName`/`Finalizer` は未使用です。テスト用のビルダーなのに `_test.go` ではないファイルに置かれています。 |

## B. テストファイル内の未使用メソッド（優先度は低い）

| # | 対象 | 根拠 |
|---|---|---|
| 8 | `fakeServerResources.ServerResourcesForGroupVersion` / `ServerPreferredResources`（`pkg/controller/resourcequota/resource_quota_controller_test.go:1240,1244`） | この fake は interface に代入されておらず、渡されているのは `ServerPreferredNamespacedResources` メソッドだけです（同ファイル :1089, :1108）。garbagecollector のテストからコピーしてきた名残と思われます。 |
| 9 | `(*fakeAction).String`（同ファイル :1276） | ポインタで受けるメソッドですが、保持しているのは値のスライス `[]fakeAction` です。そのため `fmt` 経由で暗黙に呼ばれることもありません。 |

---

## 注意点
- **外部から使われている可能性**：`k8s.io/kubernetes` を外部から import することは公式にはサポートされていませんが、実際には一部で使われています。A の 1〜7 は公開関数なので、消すと外部のコードが壊れる可能性はゼロではありません。上流に PR を出すなら、この点はレビューで判断されるところです。
- **経緯は追えない**：この clone は履歴が 1 コミット（v1.37.2 のリリースコミット）しかないので、いつ呼び出し元がなくなったかは確認できませんでした。
- **テストからしか使われていない関数**：`cmd/...` からは呼ばれず、テストからだけ呼ばれている関数が別に約 60 件あります（`history/controller_history.go` 8 件、`statefulset/stateful_set_utils.go` 6 件、`cronjob/injection.go` 5 件など）。「使われていない」わけではないので候補からは外しましたが、`_test.go` や testing パッケージへ移す整理の対象にはなります。必要なら一覧にします。

## Answer 4

# `pkg/controller` で使われていない関数（削除候補）

本番コードからもテストからも呼ばれていない関数が **約20個** 見つかりました。このうち **7グループは消して問題なさそう** です。インターフェースを満たすために必要なものなど、消せないものは下の表で分けています。コードは編集していません。

## 調べ方
- `golang.org/x/tools/cmd/deadcode`（v0.41.0）を `/tmp/cbmtools` にビルドして、リポジトリ全体（`./...`）に対して実行しました。結果は `pkg/controller` 配下だけに絞っています。この解析は、`main` や各テストを起点にして到達できない関数を、インターフェース経由の呼び出しも含めて洗い出すものです。
  - `-test` あり: テストからも到達できない関数 → 27件
  - `-test` なし: 本番の実行経路から到達できない関数 → 194件
- 手元の `staticcheck` は 2023.1.7 で古く、go1.26 で落ちたため使っていません。
- 解析は darwin 上の実行なので、Linux 専用などのビルドタグで別のファイルが使っている可能性もあります。そこで候補ごとに `git grep -w` でリポジトリ全体（`staging/` を含み、`vendor/` は除く）を検索し、参照がないことを確かめました。

## A. 削除してよさそうな候補（確度が高い順）

| # | 関数 / 型 | 根拠 |
|---|---|---|
| 1 | `pkg/controller/util/protectionutil/wrappers.go:30` の `MakePod` と `PodWrapper` の全メソッド（`Obj`, `Name`, `UID`, `SchedulerName`, `Namespace`, `Terminating`, `PVC`, `Annotation`, `Annotations`） | `protectionutil.MakePod` を参照している箇所はリポジトリにありません。テストが使っている `MakePod()` はすべてスケジューラ側の `st.MakePod()` です。`PodWrapper` 型ごと消せます。 |
| 2 | 同じファイルの `PersistentVolumeClaimWrapper` のうち `Namespace`(:115), `Annotation`(:121), `VolumeName`(:128), `Finalizer`(:133) | 型自体は `vacprotection` のテストが使っていますが、使われているのは `Name` / `VolumeAttributesClassName` / `Obj` などだけです。この4メソッドを呼んでいる箇所はありません。 |
| 3 | `pkg/controller/controller_utils.go:704` の `ByLogging` 型（`Len`/`Swap`/`Less`） | `pkg/controller` 内に参照はありません。同名・同内容の型が `staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:91` にあり、kubectl はそちらを使っています。重複コードの消し残りとみられます。 |
| 4 | `pkg/controller/controller_utils.go:1216` の `ControllersByCreationTimestamp` 型（`Len`/`Swap`/`Less`） | リポジトリ全体で、定義以外に参照がありません。 |
| 5 | `pkg/controller/daemon/daemon_controller.go:1447` の `NewPod` | `NewPod(` を呼んでいる箇所は daemon、`cmd`、`test` のどこにもありません。 |
| 6 | `pkg/controller/nodeipam/ipam/sync/sync.go:87` の `IsValidMode` | 定義以外に参照がありません。 |
| 7 | `pkg/controller/volume/persistentvolume/options/options.go:66` の `PersistentVolumeControllerOptions.AddFlags` | `NewPersistentVolumeControllerOptions()` は `test/integration/volume/attach_detach_test.go:473` で使われていますが、`AddFlags` を呼んでいる箇所はありません。kube-controller-manager は `cmd/kube-controller-manager/app/options` にある別のフラグ定義を使っています。 |

## B. 検出はされたが消すべきでない / 判断が要るもの

| 関数 | 理由 |
|---|---|
| `storageversionmigrator.go:111` の `SVMController.Name` | 現時点では呼ばれていません。`cmd/kube-controller-manager/app/storageversionmigrator.go:94` が `.Run` しか使っていないためです。ただし、他のコントローラにも揃えて付いている公開メソッドなので、消すかどうかは方針次第です。確度は低めです。 |
| `resource_quota_controller_test.go:1240,1244` の `fakeServerResources.ServerResourcesForGroupVersion` / `ServerPreferredResources` | discovery のインターフェースを満たすために必要なメソッドなので、消すとコンパイルが通らなくなる可能性が高いです。 |
| `resource_quota_controller_test.go:1276` の `fakeAction.String` | テストファイルの中の小さなヘルパーです。消すことはできますが、効果はほぼありません。 |

## C. 参考: テストからしか使われていない本番コードの関数
`-test` なしでだけ検出された、テストファイル以外にある関数は 100件超あります。多いのは `nodeipam/ipam/sync/sync.go`（12件）、`statefulset/stateful_set_utils.go`（6件）、`controller_utils.go`（6件）、`deployment/util/deployment_util.go`（5件）、`replication/replication_controller_utils.go`（4件）などです。これらはテストが使っているので、すぐには消せません。消すならテスト側の整理も一緒に必要です。

## 注意点
- ここで言う「使われていない」は、このリポジトリの中での話です。`k8s.io/kubernetes/pkg/...` を直接 import している外部プロジェクトが使っている可能性は確かめられません。Kubernetes はこの使い方を公式にはサポートしていません。
- git の履歴が単一のリリースコミット（`c50e5395`）に潰されているので、いつ使われなくなったかは追えませんでした。
- 解析結果の全文は `/tmp/cbmtools/dead_test.txt`（テスト込みで到達できないもの）と `/tmp/cbmtools/dead_prod.txt`（本番経路で到達できないもの）に残しています。

## Answer 5

## 結論：削除してよさそうな候補は3つです

| # | 関数 | 場所 | 根拠 |
|---|---|---|---|
| 1 | `IsValidMode` | `pkg/controller/nodeipam/ipam/sync/sync.go:87` | 定義とそのコメント以外、リポジトリ全体（vendor を含む）で一度も参照されていません。 |
| 2 | `NewPod` | `pkg/controller/daemon/daemon_controller.go:1447` | パッケージ内でも、外部（`cmd/`・`test/` などから `daemon` パッケージを import している箇所）でも呼ばれていません。`daemon.NewPod` で検索しても0件でした。 |
| 3 | `MakePod`（と `PodWrapper` 型およびそのメソッド9個） | `pkg/controller/util/protectionutil/wrappers.go:30` | `protectionutil.MakePod` も `PodWrapper` も、どこからも参照されていません。`wrappers.go` はファイルごと不要に見えます。 |

### 消す前に気をつけること
- `NewPod` と `MakePod` は exported です。`k8s.io/kubernetes/pkg/...` を直接 import している外部プロジェクトが使っている可能性は残ります（公式にはサポートされていない使い方ですが）。
- `IsValidMode` は `NodeSyncMode` 用のバリデータです。削除するより、本来呼ぶべき場所で呼ばれていないのでは、という観点でも一度見ておくとよいです。

## 調べ方と、候補から外したもの
1. [tool]索引（2026-11-08 時点で最新）で、`pkg/controller` 配下の `_test.go` 以外の関数1,083件のうち、呼び出しエッジ（CALLS）が入ってこないものを抽出しました。85件ありました。
2. 85件のうち、次のものは使われているので候補から外しました。
   - 自動生成ファイル `zz_generated.conversion.go` の `RegisterConversions` と各 `init`：Go のランタイムやスキームの登録処理から使われます。
   - **関数値として渡されているもの**（[tool]上は呼び出しとして数えられていませんが、実際には使われています）：
     - インデックス関数：`podResourceClaimIndexFunc` など resourceclaim の7個、`workloadByJobNameIndexFunc`、`podGroupByWorkloadNameIndexFunc`、`accessModesIndexFunc`
     - signer の `isKubeletServing`、`isKubeletClient`、`isKubeAPIServerClient`、`isLegacyUnknown`（`signer.go:244-250` で関数として返されています）
     - `isSelfNodeClientCert`（`sarapprove.go:65` の `recognize:` に渡されています）
     - `minInt32` / `maxInt32`（`horizontal.go:1442` などで `selectPolicyFn` に代入されています）
     - `addKnownTypes`、`addDefaultingFuncs`（SchemeBuilder に登録されています）
   - `NewAlphaClusterTrustBundlePublisher` と `NewBetaClusterTrustBundlePublisher`：`cmd/kube-controller-manager/app/certificates.go:317-318` の map から参照されています。
   - fuzzer の `Funcs`：`scheme_test.go:27` で使われています。
3. 残ったものはすべて `grep` でリポジトリ全体を確認しました。

## 調べきれていない範囲
- **メソッドは対象外です。** 今回抽出したのはレシーバを持たない関数だけです。インターフェース経由で呼ばれるメソッドは静的には判断しにくいので、別に調べる必要があります。
- [tool]上で `pkg/controller` 配下のいくつかのテストファイルが部分的にしかパースできていませんでした。ただし上の3候補は grep で裏を取っているので、結論には影響しません。
- コードは編集していません。削除するなら、念のため `go build ./...` と `go vet ./pkg/controller/...` で確認することをおすすめします。

## Answer 6

`pkg/controller` の中に、もう誰からも呼ばれていない関数がいくつかありました。2つのツールで見つけた候補を、リポジトリ全体の grep（vendor 以外、staging・test・cmd を含む）で一つずつ確かめてあります。コードは編集していません。

**調べ方**
- **staticcheck U1000（v0.8.2）**: パッケージ内だけで使われる関数が対象です。テストも含めて数えると、未使用はありませんでした。テストを除くと、テストからしか使われていない関数が見つかりました（B に記載）。
- **deadcode（x/tools）**: 外部に公開している関数が対象です。`./cmd/... ./pkg/... ./test/...` を起点に、テストも含めて呼ばれる関数をたどり、どこからも届かないものを出しています。
- 手元の `~/go/bin/staticcheck`（v0.4.7）は Go 1.26 で異常終了したため、新しい版を `/tmp/deadtools` に入れて使いました。

## A. 削除してよさそうな候補（本番コードからもテストからも呼ばれていない）

| # | 場所 | 根拠 |
|---|---|---|
| 1 | `pkg/controller/controller_utils.go:704` `type ByLogging` とその Len/Swap/Less | k/k の中に使っている所がありません。同じ実装が `staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:91` にあり、kubectl はそちらを使っています（`logsforobject.go:174`）。古い複製が残っているようです。 |
| 2 | `pkg/controller/controller_utils.go:1216` `type ControllersByCreationTimestamp` とその Len/Swap/Less | 定義している行以外に参照がありません。 |
| 3 | `pkg/controller/daemon/daemon_controller.go:1447` `func NewPod(ds, nodeName)` | `daemon.NewPod` を呼んでいる所がありません。同じ名前の `testutil.NewPod` と `attachdetach/testing.NewPod` は別の関数です。 |
| 4 | `pkg/controller/nodeipam/ipam/sync/sync.go:87` `func IsValidMode` | 定義している行以外に参照がありません。 |
| 5 | `pkg/controller/volume/persistentvolume/options/options.go:66` `(*PersistentVolumeControllerOptions).AddFlags` | 実際に使われているフラグ登録は `cmd/kube-controller-manager/app/options/persistentvolumebindercontroller.go:36` です。こちらは同じ名前のフラグ（`pvclaimbinder-sync-period` など）を登録していますが、呼ばれていません。型と `NewPersistentVolumeControllerOptions` は `test/integration/volume/attach_detach_test.go:473` で使われているので残す必要があります。 |
| 6 | `pkg/controller/util/protectionutil/wrappers.go` の未使用メソッド | PodWrapper は全部（`MakePod`、`Obj`、`Name`、`UID`、`SchedulerName`、`Namespace`、`Terminating`、`PVC`、`Annotation`、`Annotations`）が未使用です。PVC 用のラッパーは `Namespace`、`Annotation`、`VolumeName`、`Finalizer` が未使用です。使っているのは `vacprotection` のテストだけで、それも PVC・PV・VAC 用のラッパーの一部に限られます。grep で出てくる `st.MakePod()` などは scheduler 側にある別の実装です。 |
| 7 | `pkg/controller/storageversionmigrator/storageversionmigrator.go:111` `(*SVMController).Name` | `cmd/kube-controller-manager/app/storageversionmigrator.go:94` は `.Run` しか使っていません。ただし、何かのインターフェースを満たすために置いてある可能性もあります。消す前に、その意図がないか確認してください。 |

## B. テストからしか使われていない（消すとテストも直す必要があり、優先度は低め）

- `pkg/controller/nodeipam/ipam/cidr_allocator.go:147` `occupyServiceCIDR`：使っているのは `controller_test.go:44` だけです。本番コードからは呼ばれていません。
- `pkg/controller/statefulset/stateful_set_utils.go:59` `overlappingStatefulSets` と `:664` `ascendingOrdinal`：どちらもテストでしか使われていません。
- `pkg/controller/podautoscaler/replica_calculator.go:41-43` `defaultTesting*` の定数：テスト専用の値ですが、テストでないファイルに置かれています。削除するより `_test.go` に移すほうが合っています。

## 対象外にしたもの
- `resource_quota_controller_test.go` の `fakeServerResources.ServerResourcesForGroupVersion` などは、テスト用の偽物がインターフェースを満たすためのメソッドです。削除対象ではありません。

## 注意点
- deadcode は macOS 向けのビルドだけを解析しています。ただ、上の候補はすべて OS に関係なく grep で参照がないことを確かめています。
- `k8s.io/kubernetes/pkg/...` を外部プロジェクトから import するのは公式にはサポートされていませんが、実際にしている所はあります。1〜5 のような公開関数を消すと、そうした外部のコードが壊れる可能性はあります。
- 削除するときは、`go build ./...` と、関係するパッケージの `go test` も通るか確かめてください。

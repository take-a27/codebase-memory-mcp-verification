# T5 の回答（6 件）

## Answer 1

`pkg/controller` 配下で削除してよさそうなのは次の 3 つです。[tool]で「呼び出し元 0」と出た関数を、grep でリポジトリ全体（`vendor/` を除く）に対して一つずつ確かめました。コードは編集していません。

## 削除候補（確度の高い順）

### 1. `IsValidMode` — `pkg/controller/nodeipam/ipam/sync/sync.go:87`
- 定義行とそのコメント以外に、`IsValidMode` という語はリポジトリのどこにも出てきません。
- さらに、`nodeipam/ipam/sync` パッケージを import している Go ファイル自体が 1 つもありません。パス文字列が出てくるのは `cmd/cloud-controller-manager/.import-restrictions:28`（import を許可する一覧）だけです。
- そのため、この関数だけでなくパッケージ全体が使われていない可能性があります。ただ、パッケージごと消すかどうかは影響が大きいので、別途判断したほうがよいです。

### 2. `NewPod`（DaemonSet 用）— `pkg/controller/daemon/daemon_controller.go:1447`
- `daemon` パッケージの中では、`NewPod(` を呼んでいる箇所は定義行だけでした。
- `k8s.io/kubernetes/pkg/controller/daemon` を import しているファイルにも、`.NewPod(` の呼び出しはありません。
- 注意点：公開関数（大文字始まり）です。このリポジトリの外のコードが使っている可能性は否定できません。

### 3. `MakePod` — `pkg/controller/util/protectionutil/wrappers.go:30`
- テスト用のヘルパー（ビルダー）ですが、`protectionutil.MakePod` を使っている箇所はテストを含めてありません。同じファイルにある `MakeVolumeAttributesClass` や `MakePersistentVolume` は `vacprotection` のテストで使われています。
- `PodWrapper` 型も、このファイルの外では参照されていません（scheduler にある `st.PodWrapper` は名前が同じだけの別物です）。消すなら `PodWrapper` とそのメソッドもまとめて消せる見込みです。

## 候補から外したもの（[tool]では呼び出し元 0 でも、実際は使われている）
[tool]が拾うのは直接の呼び出しだけで、関数を値として渡す使い方は拾えないため、次の 22 件は誤検知でした。
- **インデックス関数**：`cache.Indexers{...}` に渡しています。`resourceclaim/controller.go` の 7 つ、`job_scheduling_manager.go` の 2 つ、`persistentvolume/index.go` の `accessModesIndexFunc` が該当します。
- **関数を返す・代入するもの**：
  - signer の `isKubeletServing` など 4 つは、`signer.go:244-250` で関数として返しています。
  - `isSelfNodeClientCert` は `sarapprove.go:65` の `recognize:` に代入しています。
  - `minInt32` と `maxInt32` は `horizontal.go:1442-1474` の `selectPolicyFn` に代入しています。
- **他パッケージからの参照**：`NewAlpha/BetaClusterTrustBundlePublisher` は `cmd/kube-controller-manager/app/certificates.go:317-318` の map に登録されています。
- **スキーマ登録**：`addKnownTypes` と `addDefaultingFuncs` は `SchemeBuilder` 経由で使われ、`fuzzer.Funcs` は `scheme_test.go` で使われています。

## 調べきれていない範囲
- 対象は通常の関数だけで、メソッド（レシーバ付きの関数）は調べていません。interface を満たすために必要なメソッドがあり、機械的には判定しにくいためです。必要なら続けて調べます。
- インデックスのカバレッジを確認したところ、上の 3 ファイルは問題ありませんでした。解析が一部欠けていたのは `pkg/controller` 内のテストファイル 5 つだけで、上の結論はすべて grep で裏を取っているので影響はありません。
- 削除する前に、`go build ./...` と `go vet`（または `staticcheck` の U1000 チェック）で確認することをおすすめします。

## Answer 2

## 結論

`pkg/controller` 配下のテスト以外の関数 838 個（`zz_generated*` は除外）のうち、本番コードから一度も呼ばれていないものは 39 個でした。それぞれを grep で確認し、`init`・fuzzer・テスト用ヘルパーを除いた結果、削除してよさそうな候補は次のとおりです。コードは編集していません。

### A. どこからも使われていない（優先して削除してよい候補）

| 関数 | 場所 | 根拠 |
|---|---|---|
| `nodeipam/ipam/sync` パッケージ全体 | `pkg/controller/nodeipam/ipam/sync/sync.go` | このパッケージを import している Go ファイルは、vendor を除いてリポジトリに一つもありません。名前が出てくるのは `cmd/cloud-controller-manager/.import-restrictions:28` という許可リストだけです。`New` は自分のパッケージの `sync_test.go` からしか呼ばれていません。 |
| ↳ `IsValidMode` | `pkg/controller/nodeipam/ipam/sync/sync.go:87` | [tool]上もテストを含めて呼び出し元が 0 件で、grep でも定義とコメントしか見つかりません。 |
| `protectionutil.MakePod`（と `PodWrapper` 一式） | `pkg/controller/util/protectionutil/wrappers.go:30` | 呼び出し元が 0 件です。同じファイルの `MakePersistentVolume`、`MakePersistentVolumeClaim`、`MakeVolumeAttributesClass` は `vac_protection_controller_test.go` で使われていますが、`MakePod` だけはどこからも使われていません。 |
| `daemon.NewPod` | `pkg/controller/daemon/daemon_controller.go:1447` | 呼び出し元が 0 件です。`daemon.NewPod` でも、パッケージ内からの `NewPod(` でも grep に一致しません。公開関数なので、外部のリポジトリが使っていないかだけ確認が必要です。 |

### B. テストからしか呼ばれていない（本番コードとしては使われていない）

消す場合は、そのテストも合わせて直す必要があります。

| 関数 | 場所 | 使っている場所 |
|---|---|---|
| `replication.SetCondition` / `RemoveCondition` | `pkg/controller/replication/replication_controller_utils.go:37,47` | 自分のテスト（`TestSetCondition` / `TestRemoveCondition`）だけです。`replicaset` に同じ名前の関数がありますが、そちらは別の関数で本番コードから使われています。 |
| `util/node.DeletePods` | `pkg/controller/util/node/controller_utils.go:45` | `node_lifecycle_controller_test.go:988` だけです。本番のノードライフサイクル処理からは呼ばれていません。 |
| `occupyServiceCIDR` | `pkg/controller/nodeipam/ipam/cidr_allocator.go:147` | `ipam/controller_test.go:44` だけです。非公開の関数なので、削除しても影響はこのパッケージ内で収まります。 |

### 候補から外したもの（39 個のうち残り）
- **自動的に呼ばれるもの:** 各種 `init` 関数。
- **テストから使う前提で置かれているもの:** `apis/config/fuzzer.Funcs`（`scheme_test.go` から使われています）、`resourceclaim/metrics.SetTestMode`。
- **テスト用ヘルパーやフェイク:** `testutil/`、`*/testing/`、`ipam/test/` の各関数、`NewFakeHistory`、`NewFakeNodeStatusUpdater`、`NewStatefulPodControlFromManager`。
- **リポジトリ内の別の場所から使われているもの:** `NewPersistentVolumeControllerOptions` は `test/integration/volume/attach_detach_test.go:473` から使われています。

## 調べ方と、この調査で分からないこと
- [tool] の[tool]で、本番コードから CALLS・USAGE・CALL_REFERENCE のどれかで参照されている関数（799 個）を出し、全体の 838 個との差分をとりました。そのうえで候補を一つずつ `git grep` で確認しています。
- **メソッドは調べていません。** Go ではインターフェース経由の呼び出しを[tool]で正確に追いにくく、誤って未使用と判定しやすいためです。
- インデックスが一部欠けていたのはテストファイル 5 つ（`horizontal_test.go` など）の数行だけです。テストからの参照は grep で別に確認したので、結論への影響はないと考えています。
- vendor は検索対象から外しました（`staging/` も、前半の確認では外しています）。そのため、公開関数（`daemon.NewPod`、`util/node.DeletePods` など）を外部のリポジトリが import している可能性は残ります。リフレクションや文字列で呼ばれるケースも、この方法では見つけられません。

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

`pkg/controller` で削除してよさそうな候補は8件です。コードは編集していません。

**調べ方:** まず[tool]で、本番コード（`_test.go` 以外）の関数とメソッドのうち、CALLS / USAGE / CALL_REFERENCE / IMPLEMENTS / OVERRIDE のどの辺からも参照されていないものを抜き出しました。関数35件、メソッド277件が出ました。ただし大半は誤検出でした。Go のインターフェースは明示的に宣言しなくても満たせるため、[tool]がそれを辿りきれていないからです（例：`ExpectCreations` や `GetAttachState` は実際には呼ばれています）。そこで、`init`、自動生成された `zz_generated.*` のファイル、`Len`/`Less`/`Swap`、`DeepCopy`、VolumeHost などのインターフェース実装を除き、残りを vendor 以外のリポジトリ全体（`staging/`、`cmd/`、`test/` を含む）に grep して裏を取りました。

## 削除候補（定義以外に参照が見つからなかったもの）

| # | シンボル | 場所 | 根拠 |
|---|---|---|---|
| 1 | `IsValidMode` | `pkg/controller/nodeipam/ipam/sync/sync.go:87` | `\bIsValidMode\b` で grep すると、ヒットは定義とそのコメントだけ。テストからも呼ばれていない。 |
| 2 | `MakePod` と `PodWrapper` 型、そのメソッド全部（`Obj`、`Name`、`UID`、`SchedulerName`、`Namespace`、`Terminating`、`PVC`、`Annotation`、`Annotations`） | `pkg/controller/util/protectionutil/wrappers.go:26-95` 付近 | `protectionutil.*` の使用箇所を集計すると、使われているのは `MakePersistentVolume`、`MakePersistentVolumeClaim`、`MakeVolumeAttributesClass`、`IsDeletionCandidate`、`NeedToAddFinalizer` だけ。`PodWrapper` を作るのは `MakePod` だけなので、型ごと使われていない。 |
| 3 | `NewPod`（DaemonSet 用） | `pkg/controller/daemon/daemon_controller.go:1447` | `daemon.NewPod` の呼び出しはどこにもない。パッケージ内でも、修飾なしの `NewPod(` は定義の1件だけ。外部の `test/e2e` などが使っているのは `NodeShouldRunDaemonPod`、`Match`、`NewDaemonSetsController` だけ。 |
| 4 | `(*TimedWorkerQueue).UpdateWork` | `pkg/controller/tainteviction/timed_workers.go:164` | `UpdateWork\b` で grep すると、ヒットは定義とコメントだけ。 |
| 5 | `(*GarbageCollector).GetDependencyGraphBuilder` | `pkg/controller/garbagecollector/garbagecollector.go:835` | コメントには「テストで便利」とあるが、テストを含めて呼び出しが0件。 |
| 6 | `(*PersistentVolumeControllerOptions).AddFlags` | `pkg/controller/volume/persistentvolume/options/options.go:66` | 構造体は `test/integration/volume/attach_detach_test.go` で使われているが、`AddFlags` はどこからも呼ばれていない。 |

## 削除できるが影響が大きいもの

| # | シンボル | 場所 | 根拠と注意点 |
|---|---|---|---|
| 7 | `RaiseExpectations` | `pkg/controller/controller_utils.go:164`（インターフェース）、`:254`（実装） | `.RaiseExpectations(` の呼び出しは0件。消すには公開インターフェース `ControllerExpectationsInterface` から外す必要があり、このインターフェースを埋め込んでいる型やモックにも影響する。 |
| 8 | `(*NoopConsistencyStore).ReadAt` | `pkg/controller/util/consistency/consistency.go:207` | 呼び出しは0件。ただし `var _ ConsistencyStore = &NoopConsistencyStore{}` があるので、インターフェース側から外さない限り消せない。インターフェースのメソッドを使っている箇所があるかは追加で調べる必要がある。 |

## [tool]では候補に出たが、残すべきもの
- `alphaHandlers` のメソッド（`publisher.go:165-187`）：ジェネリクスのインターフェース `clusterTrustBundleHandlers[T]` の実装で、`publisher.go:270` で使われている。
- `PatchReplicaSet`、`PatchControllerRevision`、`GetMonitor`、`DebuggingHandler`、`GetDesiredStateOfWorld`、`AdoptOrphanRevisions` など：インターフェース経由、または `cmd/`・`test/integration` から呼ばれている。
- `SyncAll`：`framework_test.go:893` で使われている。

## 注意
- 公開されている関数（1〜3、5、6）は、外部のプロジェクトが `k8s.io/kubernetes/pkg/...` を直接 import していると壊れる可能性があります。ただし、この import は公式にはサポートされていません。
- インデックスの取りこぼしは、5つのテストファイルで数行ずつ構文解析が不完全な箇所があるだけでした（`job_scheduling_manager_test.go` など）。上の候補はすべて grep でリポジトリ全体を確認済みなので、この取りこぼしの影響は受けていません。
- 消す前に、`go build ./...`、`go vet ./pkg/controller/...`、テストの通過を確認してください。リポジトリの `hack/verify-*` 系スクリプトがあれば、それも通すのがおすすめです。

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

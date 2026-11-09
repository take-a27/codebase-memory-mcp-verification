# T2 の回答（6 件）

## Answer 1

`pkg/controller` 配下の非テストコード 422 ファイルのうち、ワークロード系コントローラ（ReplicaSet / Deployment / StatefulSet / DaemonSet / Job / CronJob）と共通パッケージ `controller` を中心に確認しました。重複が大きい順に、まとめ方の案を挙げます。コードは編集していません。

---

## 1. `resolveControllerRef`：6 か所でほぼ同じコード 【効果大・リスク小】

場所:
- `statefulset/stateful_set.go:462`
- `deployment/deployment_controller.go:461`
- `job/job_controller.go:384`
- `replicaset/replica_set.go:349`
- `daemon/daemon_controller.go:824`
- `cronjob/cronjob_controllerv2.go:282`

6 つとも「Kind を比べる → Lister で名前から取得 → UID を比べる」という同じ手順で、コメントまで同じです。違うのは Lister と戻り値の型だけです。

**案:** `controller_ref_manager.go` にジェネリクス版を 1 つ置きます。

```go
func ResolveControllerRef[T metav1.Object](
    ref *metav1.OwnerReference, kind string,
    get func(name string) (T, error),
) (T, bool) {
    var zero T
    if ref.Kind != kind { return zero, false }
    obj, err := get(ref.Name)
    if err != nil || obj.GetUID() != ref.UID { return zero, false }
    return obj, true
}
```

各コントローラ側は `controller.ResolveControllerRef(ref, controllerKind.Kind, ssc.setLister.StatefulSets(ns).Get)` の 1 行で済みます。ReplicaSet だけは `rsc.Kind` を使っている点に注意が必要です。ReplicationManager が ReplicaSet のコードを使い回しているためで、kind を引数で渡す形にすればそのまま対応できます。

## 2. `RecheckDeletionTimestamp` に渡すクロージャ：6 か所 【効果中・リスク小】

場所: `replicaset/replica_set.go:862`、`deployment/deployment_controller.go:537`、`job/job_controller.go:880`、`statefulset/stateful_set.go:405`、`daemon/daemon_controller.go:773`、`daemon/update.go:476`

どれも「クライアントで直接 Get → UID が違えば `"original X %v/%v is gone"` エラー」という同じ処理です。

**案:** `RecheckDeletionTimestamp` の隣に、UID チェックまで含めたヘルパーを追加します。

```go
func RecheckDeletionTimestampByUID[T metav1.Object](
    orig metav1.Object, kind string,
    getFresh func(ctx context.Context) (T, error),
) func(context.Context) error
```

## 3. `processNextWorkItem` / `handleErr`：27 ファイル以上 【効果大・リスク中】

`Get → defer Done → sync → 成功なら Forget、失敗なら AddRateLimited` という形が、ReplicaSet、DaemonSet、StatefulSet、ServiceAccount、PV protection、TTLAfterFinished などに繰り返し出てきます。`worker()` の `for c.processNextWorkItem(ctx) {}` も 23 か所あります。

**案:** `pkg/controller/util` などに、型付き workqueue を受け取る汎用ループを置きます。

```go
func ProcessNextWorkItem[T comparable](ctx context.Context,
    q workqueue.TypedRateLimitingInterface[T],
    sync func(context.Context, T) error,
    onError func(ctx context.Context, q workqueue.TypedRateLimitingInterface[T], key T, err error), // 省略時は既定の処理
) bool
func RunWorker[T comparable](ctx, q, sync, ...)  // worker() の置き換え
```

**注意点（リスク中とした理由）:**
- Deployment の `handleErr` は、`NamespaceTerminatingCause` の場合はリトライせず、`maxRetries` を超えたら諦めます。Job、Disruption、EndpointSlice なども独自のリトライ方針を持っています。カスタマイズできる `onError` を必須にするか、単純な形のコントローラだけを対象にする必要があります。
- エラーメッセージの文言はコントローラごとに違います。ログを使った監視やテストが文言に依存していないか、確認が必要です。

## 4. Pod 削除ハンドラの tombstone（`DeletedFinalStateUnknown`）処理：31 ファイル 【効果中・リスク小】

`obj.(*v1.Pod)` に失敗したら `cache.DeletedFinalStateUnknown` を取り出して型アサーションし直す、という 10 行ほどのコードがあちこちにあります（例: `replicaset/replica_set.go:581`、`statefulset/stateful_set.go:350`）。

**案:**
```go
func ObjectFromDeleteEvent[T runtime.Object](logger klog.Logger, obj interface{}) (T, bool)
```
エラーログの出し方は `HandleError` 系と `HandleErrorWithLogger` 系が混ざっているので、まとめる際にどちらかへ統一できます。

さらにその後の「`GetControllerOf` → `resolveControllerRef` → キー化して enqueue」の部分も、RS / SS / DS / Job でほとんど同じです。1 と組み合わせれば、ほぼ共通の関数にできます。ただし RS と Job は expectations の更新（`DeletionObserved`）が挟まるので、完全に共通にするのは難しいです。

## 5. `enqueue` / `enqueueAfter` / `enqueueRateLimited` 【効果小〜中・リスク小】

例: `deployment/deployment_controller.go:399-430` に 3 つ、`replicaset/replica_set.go:367-385` に 2 つ、DaemonSet / StatefulSet / CronJob にも同じものがあります。違いは「`KeyFunc` を呼んだ後に `Add`、`AddAfter`、`AddRateLimited` のどれを使うか」だけです。

**案:** `EnqueueObject(q, obj)` と `EnqueueObjectAfter(q, obj, d)` を共通化します。単独では効果が小さいので、3 と一緒にやるのが自然です。

## 6. `controller_ref_manager.go` の中の重複 【効果中・リスク小】

- `ClaimReplicaSets`（:319）と `ClaimControllerRevisions`（:457）は、型以外まったく同じです。`ClaimPods` も、フィルタがあること以外は同じです。
- `AdoptPod`（:222）、`AdoptReplicaSet`（:348）、`AdoptControllerRevision`（:486）、および対応する `Release*` 3 つも同じ構造です。特に Release の「404 と 422 は無視する」処理は、コメントごとコピーされています。

**案:** 内部にジェネリクス版の `claimObjects[T metav1.Object](ctx, objs []T, match, patch func(...))` と、`releaseObject(patchFn)` を作り、公開 API はその薄いラッパーとして残します。公開シグネチャを変えずに済むので、呼び出し元への影響はありません。

---

## 進め方の提案

1. **まず 1・2・6 から。** 純粋な関数の抽出で動作が変わらず、既存テストで確認できます。`controller_ref_manager.go` は 1 ファイルの中で完結するので、PR も小さくできます。
2. **次に 4（と 5）。** 呼び出し箇所は多いものの、機械的に置き換えられます。
3. **3 は最後に。** リトライ方針の違いをどう引数で表すかを先に決めてから、単純なコントローラ（ServiceAccount、PV/PVC protection、TTLAfterFinished、ReplicaSet など）から順に移していくのが安全です。

補足: Kubernetes 本体は大規模なリファクタ PR を受け入れにくい傾向があります。上流に出すなら、案ごとに PR を分け、まず 1 つのコントローラで試してから横に広げる方が、レビューが通りやすいと思います。

どれかについて、具体的な差分の草案（編集はせずに提示するだけ）が必要なら言ってください。

## Answer 2

`pkg/controller` 配下で重複している処理を、まとめる価値が高い順に5つ挙げます。どれもコードを読んで中身を確認しました。コードは編集していません。

---

## 1. `resolveControllerRef`（6か所でほぼ同じコード）★最優先

**場所**
- `statefulset/stateful_set.go:462`
- `deployment/deployment_controller.go:461`
- `daemon/daemon_controller.go:824`
- `replicaset/replica_set.go:349`
- `job/job_controller.go:384`
- `cronjob/cronjob_controllerv2.go:282`

6か所ともコメントまで同じで、「Kind を比較 → Lister から Name で Get → UID を比較」という処理です。違うのは Lister と戻り値の型だけです。ReplicaSet だけは、ReplicationController と共用するために `controllerKind.Kind` ではなく `rsc.Kind` を使っています。

**提案**: go.mod は `go 1.26` なので、ジェネリクスで `controller_utils.go` にまとめられます。

```go
// pkg/controller/controller_utils.go
func ResolveControllerRef[T metav1.Object](
    ref *metav1.OwnerReference, kind string, get func(name string) (T, error),
) (T, bool) {
    var zero T
    if ref.Kind != kind { return zero, false }
    obj, err := get(ref.Name)
    if err != nil || obj.GetUID() != ref.UID { return zero, false }
    return obj, true
}
```

各コントローラーの中身は1行で済むようになります。
```go
d, _ := controller.ResolveControllerRef(ref, controllerKind.Kind, dc.dLister.Deployments(ns).Get)
```
Kind は引数で渡すので、ReplicaSet と RC の共用も今のまま動きます。

---

## 2. Informer の Delete ハンドラーでの tombstone 処理（16ファイル、25か所）

`cache.DeletedFinalStateUnknown` から元のオブジェクトを取り出す次の処理が、毎回コピーされています。

```go
pod, ok := obj.(*v1.Pod)
if !ok {
    tombstone, ok := obj.(cache.DeletedFinalStateUnknown)
    if !ok { HandleError("Couldn't get object from tombstone"); return }
    pod, ok = tombstone.Obj.(*v1.Pod)
    if !ok { HandleError("Tombstone contained object that is not a pod"); return }
}
```
例: `statefulset/stateful_set.go`（deletePod）、deployment、replicaset、daemon、job、cronjob、disruption、endpointslice、serviceaccount、certificates、pvcprotection など。

**提案**:
```go
func ObjFromTombstone[T any](logger klog.Logger, obj interface{}) (T, bool)
```
上のような関数を作れば、各ハンドラーは `pod, ok := controller.ObjFromTombstone[*v1.Pod](logger, obj); if !ok { return }` だけで済みます。エラーメッセージの表記揺れ（"Couldn't" と "couldn't"）も揃います。

---

## 3. adopt 前のレコード再確認（`canAdoptFunc`）（6か所）

**場所**: `statefulset/stateful_set.go:405`、`deployment/deployment_controller.go:537`、`replicaset/replica_set.go:862`、`job/job_controller.go:880`、`daemon/update.go:476`、`daemon/daemon_controller.go:773`

どれも `RecheckDeletionTimestamp(func(ctx) { fresh := client.Get(...); if fresh.UID != orig.UID { return fmt.Errorf("original X %v/%v is gone ...") } ... })` という同じ形のクロージャです。

**提案**: UID を比較してエラーを作る部分を共通化します。
```go
func RecheckDeletionTimestampByUID[T metav1.Object](
    orig metav1.Object, kind string, get func(ctx context.Context) (T, error),
) func(context.Context) error
```
呼び出し側から渡すのは `client.AppsV1().Deployments(ns).Get(ctx, name, ...)` 相当の関数だけになります。

---

## 4. Condition 操作のヘルパー（Deployment / ReplicaSet / RC）

**場所**
- `deployment/util/deployment_util.go:104-160`（New/Get/Set/Remove/filterOutCondition）
- `replicaset/replica_set_utils.go:147-191`（同じ5関数）
- `replication/replication_controller_utils.go:26-60`（Get/Set/Remove）

型が違うだけでロジックは同じです。Go のジェネリクスは型パラメータのフィールドを直接読めないので、Type を取り出す関数を引数で渡す形にします。

```go
// pkg/controller/util/conditions
func Get[C any, T comparable](conds []C, t T, typeOf func(C) T) *C
func FilterOut[C any, T comparable](conds []C, t T, typeOf func(C) T) []C
```

**注意（動作の違い）**:
- `SetDeploymentCondition` は、status が変わらないときに `LastTransitionTime` を前の値のまま残します。
- ReplicaSet 版の `SetCondition` はこれをしません。

`Set` まで1つにまとめると動作が変わってしまいます。そこで共通化は `Get` と `FilterOut` までにして、`Set` は各パッケージに薄いラッパーとして残すのが安全です。将来 API が `metav1.Condition` に移るなら `meta.SetStatusCondition` を使えますが、apps 系の型は今は独自の Condition 型です。

---

## 5. ワーカーループと `handleErr`（27か所、効果は小さい）

`processNextWorkItem` が27か所、`handleErr` が5か所あります（endpoint、endpointslice、endpointslicemirroring、deployment、ttlafterfinished）。どれも「`Forget` → `NumRequeues < maxRetries` なら `AddRateLimited`、それ以外は破棄」という同じ形です。

**提案**: `workqueue.TypedRateLimitingInterface[string]`、`maxRetries`、ログ用の名前を受け取る `HandleSyncErr(queue, key, err, maxRetries, logger, kind)` があれば共通化できます。

ただし、メトリクスを記録する `trackSync(err)`、`klog.KRef` 用のキー分割、ログレベルなど、コントローラーごとの細かい違いが多いです。無理にまとめるより、新しく書くコントローラーで使ってもらう程度が現実的だと思います。

---

## 進め方の提案

1. **1と2から始める**のがおすすめです。動作は変わらず、機械的に置き換えられて、テストも既存のもので足ります。PR は「ヘルパーの追加」と「各コントローラーの置き換え」に分けるとレビューしやすくなります。
2. 3は1と同じ PR にまとめても自然です。
3. 4は上の動作の違いがあるので、`Get` と `FilterOut` だけの小さな PR にします。
4. 5は優先度が低いです。

今回は調べていないこと:
- `addPod` / `updatePod` の中の「ControllerRef が変わったら古いオーナーと新しいオーナーの両方を enqueue する」処理。deployment、RS、SS、job、daemon で似ていますが、expectations の扱いがそれぞれ違うので、詳しくは見ていません。
- `enqueue*` 系の関数。52個ありますが、`controller.KeyFunc` を呼んでキューに入れるだけの短い関数が多く、共通化してもあまり得をしないと判断しました。

## Answer 3

`pkg/controller` 配下で重複している処理を洗い出し、まとめ方を考えました。コードは編集していません。

**調べ方**：[tool] の[tool]で、`pkg/controller` 配下の関数のうち同じ名前が複数ファイルにあるものを集計し（テストファイルと自動生成の conversion/register は除外）、該当する関数の本体を一つずつ見比べました。インデックスが欠けているのはテストファイル5つだけで、本体のコードは対象に入っています。ただ、**名前が違うのに中身が同じ処理は、この方法では見つけられません**。

## まとめた方がよいもの（ほぼ同じ実装）

**1. Condition の操作関数が3つのパッケージに同じ形である**
- 場所：`replicaset/replica_set_utils.go:158-193`、`replication/replication_controller_utils.go:26-61`、`deployment/util/deployment_util.go:147-156`
- `GetCondition` / `SetCondition` / `RemoveCondition` / `filterOutCondition` が、扱う型だけ違って中身は同じです。
- 提案：型パラメータ（ジェネリクス）を使った共通の関数を `pkg/controller/util/conditions` などに作り、各パッケージの既存の関数はそれを呼ぶだけの薄いラッパーにします。
  - Condition の構造体にはメソッドがないので、ジェネリクスだけではフィールドを読めません。`func filterOut[C any, T comparable](cs []C, t T, typeOf func(C) T) []C` のように、型を取り出す関数を引数で渡す形にします。
  - `slices.DeleteFunc` に置き換える手もあります。ただしこれは元のスライスを書き換えるので、新しいスライスを返し、全部消えたときは nil を返す今の動きとは違います。置き換えるなら注意が必要です。

**2. `uidCache` がまるごと重複している**
- 場所：`resourceclaim/uid_cache.go` と `devicetainteviction/uid_cache.go`
- diff を取ると、違いはパッケージ名と、メソッド名の頭文字の大小（`Add`/`add`、`Has`/`has`）だけでした。
- 提案：`pkg/controller/util/uidcache` に公開する型として切り出します。呼び出し元は `resourceclaim/controller.go:242` と `devicetainteviction/device_taint_eviction.go:763` です。

**3. `printDiff` が一字一句同じ**
- 場所：`resourcequota/resource_quota_controller.go:524` と `garbagecollector/garbagecollector.go:267`
- どちらも、discovery で取れたリソースの差分をログに出す用途です。
- 提案：共通のユーティリティに1つだけ置きます。

**4. `usagesToSet` が一字一句同じ**
- 場所：`certificates/approver/sarapprove.go:165` と `certificates/signer/signer.go:307`
- 提案：どちらも結果を `capihelper` の関数に渡しているだけです。そこで `capihelper` に関数を移すか、`capihelper` の関数が `[]KeyUsage` をそのまま受け取るように変えるのが自然です。

**5. 削除イベントの tombstone 処理**
- 場所：`endpointslice/endpointslice_controller.go:673` と `endpointslicemirroring/utils.go:195` の `getEndpointSliceFromDeleteAction` は、エラーメッセージが違うだけで同じ実装です。
- もっと広く見ると、`cache.DeletedFinalStateUnknown` を型アサーションする同じパターンが、テスト以外の **31ファイル** にあります。
- 提案：`func ObjFromDeleteAction[T any](obj any) (T, bool)` のようなジェネリックな関数を1つ用意し、少しずつ置き換えていきます。効果は一番大きいですが、触るファイルも多くなります。

**6. `getPatch`（spec.template を置き換えるパッチを作る処理）**
- 場所：`statefulset/stateful_set_utils.go:552` と `daemon/update.go:504`
- 違うのはエンコード方法だけです（`runtime.Encode(patchCodec, …)` と `json.Marshal`）。
- 提案：raw map を受け取ってパッチを組み立てる後半部分だけを共通化し、エンコードは呼び出し側に残します。

## 似ているが、まとめるなら注意が必要なもの
- **`slowStartBatch`**（`statefulset/stateful_set_control.go:334` と `replicaset/replica_set.go:887`）
  - 引数の形、上限の `MaxBatchSize`、エラーの返し方（全部まとめて返すか、最初の1つだけ返すか）、インデックスを渡すかどうかが違います。
  - 1つにすると、どちらかの動きが変わります。
- **`recordMetrics`**（`clustertrustbundlepublisher/metrics.go:57` と `rootcacertpublisher/metrics.go:54`）
  - 前者は `errors.As` を使い、後者は型アサーションを使っています。そのため、ラップされたエラーのときに記録されるステータスコードが違います。
  - まとめるなら `errors.As` 側に揃えるのがよいです。これは後者の不整合の修正にもなります。
- **`isPodTerminated`**（`podgc` と `scheduling/podgroupprotection`）
  - 正しい phase の値に対しては同じ結果になります。ただ、未知の phase が来たときの扱いが違います（podgc 側はそれを「終了」とみなします）。

## まとめる必要がないもの
- `AddOrUpdateLabelsOnNode`：`util/node` 側は `controller.AddOrUpdateLabelsOnNode` を呼んで、ログ出力を足しているだけのラッパーです。
- `isOlderThan`：1行の関数で、引数の型も `time.Time` と `metav1.Time` で違います。
- `calculateStatus`・`NewController`・`Register` など：名前が同じなだけで、中身は別物です。

**進める順番の提案**：まず 2・3・4 です。中身がまったく同じで、動きが変わる心配がなく、すぐできます。次に 1、最後に段階的に 5 という順がおすすめです。

## Answer 4

`pkg/controller` の中で、同じ名前の関数が複数のパッケージにあるもの（テストを除く）を[tool]で洗い出しました。そのうえで中身を突き合わせ、本当に重複しているものを優先度順に並べています。コードは編集していません。

## 優先度：高（ほぼ同じコードで、まとめても挙動が変わらないもの）

### 1. `uidCache` がファイルごと重複している
- 場所：`resourceclaim/uid_cache.go` と `devicetainteviction/uid_cache.go`
- `diff` で違うのは、パッケージ名と、メソッド名が `Add`/`Has` か `add`/`has` か（公開か非公開か）だけです。
- **提案**：`pkg/controller/util/uidcache`（例）にメソッドを公開した版を1つ作り、2つのパッケージからはそれを使います。

### 2. `printDiff` が完全に同じ
- 場所：`resourcequota/resource_quota_controller.go:524` と `garbagecollector/garbagecollector.go:267`
- `diff` の差分はゼロでした。どちらも GVR の集合を比べて「追加されたもの」と「削除されたもの」を文字列にしています。
- **提案**：両方とも resource monitor の再同期で使われているので、`pkg/controller/controller_utils.go` か、GVR を扱う小さな util パッケージに移します。

### 3. `usagesToSet` が完全に同じ
- 場所：`certificates/approver/sarapprove.go:165` と `certificates/signer/signer.go:307`
- **提案**：`pkg/controller/certificates` の共通部分（`certificate_controller_utils.go` など）にまとめます。ついでに、非推奨の `sets.String` を `sets.Set[string]` に変えるとよいです。

### 4. `getPatch`（`spec.template` を `$patch: replace` で置き換えるパッチを作る処理）
- 場所：`statefulset/stateful_set_utils.go:552` と `daemon/update.go:504`
- 違うのは最初のエンコード方法だけです。statefulset は `runtime.Encode(patchCodec, set)`、daemon は `json.Marshal(ds)` を使っていて、その後の処理は同じです。
- **提案**：エンコード済みのバイト列を受け取る関数に切り出し、エンコードは呼び出す側に残します。こうすればそれぞれのエンコード方法を変えずに済みます。

  ```go
  func templateReplacePatch(objJSON []byte) ([]byte, error)
  ```

  ControllerRevision の作成に関わる処理なので、パッチの中身が1バイトも変わらないことをテストで確かめてから移すべきです。

### 5. `getEndpointSliceFromDeleteAction`
- 場所：`endpointslice/endpointslice_controller.go:673` と `endpointslicemirroring/utils.go:195`
- 違うのはエラーメッセージの文言だけです。
- **提案**：`pkg/controller/util/endpointslice`（共通 util がすでにあればそこ）に移します。

## 優先度：中（形は同じで、型だけが違うもの）

### 6. Condition 操作（`Get` / `Set` / `Remove` / `filterOutCondition`）が3組ある
- 場所：
  - `replicaset/replica_set_utils.go:158-193`
  - `replication/replication_controller_utils.go:26-61`
  - `deployment/util/deployment_util.go:115-156`
- 3組とも同じ手順で、扱う型（`ReplicaSetCondition`、`ReplicationControllerCondition`、`DeploymentCondition`）だけが違います。
- **注意点**：
  - deployment の `SetDeploymentCondition` だけは、状態が変わらないときに `LastTransitionTime` を前の値のまま残します。replicaset と replication はこれをしないので、完全に同じ処理ではありません。
  - [tool]で呼び出し元を追うと、`replication` パッケージの `GetCondition`/`SetCondition`/`RemoveCondition` を呼んでいるのは自分のテストと `test/e2e/apps/rc.go` だけでした。本番のコードからは使われていないようです。
- **提案**：
  1. まず、replication 側の関数がまだ必要かを確認します。不要なら e2e の呼び出しを置き換えて削除できます。
  2. 残りは、`Type` と `Status` を取り出す関数を引数で渡すジェネリクスの helper にまとめられます。ただ、型ごとに10行ほどの処理なので、読みやすさが落ちるならまとめなくてもよいと思います。

### 7. `slowStartBatch`（並列数を倍々に増やしながら処理を流す）
- 場所：`replicaset/replica_set.go:887` と `statefulset/stateful_set_control.go:334`
- 骨格は同じですが、次の3点が違います。

  | 項目 | replicaset | statefulset |
  |---|---|---|
  | `fn` に渡す引数 | なし | インデックス |
  | 1回あたりの上限 | なし | `MaxBatchSize` で頭打ち |
  | エラーの返し方 | 最初の1件だけ | 全件を Aggregate |

- **提案**：インデックスを受け取る形の関数を `pkg/controller/controller_utils.go` に1つ置きます。上限とエラーの返し方はオプションで切り替えるようにし、replicaset 側は引数を無視するラッパーで呼びます。
- エラーの返し方を変えるとログや event の内容が変わるので、それぞれ今の動作を保つ設定で始めるべきです。

### 8. certificates 系の metrics（`recordMetrics` / `registerMetrics`）
- 場所：`certificates/clustertrustbundlepublisher/metrics.go` と `certificates/rootcacertpublisher/metrics.go`
- 違うのは Subsystem 名と Help の文言、それにステータスコードの取り出し方です。
  - clustertrustbundlepublisher は `errors.As` で `APIStatus` を取り出すので、ラップされたエラーにも対応します。
  - rootcacertpublisher は `*StatusError` への直接の型アサーションなので、ラップされていると取れません。
- **提案**：Subsystem を引数に取るコンストラクタ（例：`newSyncMetrics(subsystem)`）を共通化し、コードの取り出しは `errors.As` 版に揃えます。rootcacertpublisher 側では、ラップされたエラーでも正しいコードが出るようになります（バグ修正の意味合いもあります）。
- **注意点**：メトリクスの名前は変わりませんが、ラベルの値が変わることがあり得ます。

### 9. 削除時の tombstone（`DeletedFinalStateUnknown`）の取り出し
- 非テストの31ファイルに、45か所あります。どれも「型アサーションを試し、失敗したら tombstone から取り出し、`HandleError` を呼ぶ」という同じパターンです。
- **提案**：ジェネリクスの helper を `pkg/controller/controller_utils.go` に置けば、各 `deleteXxx` ハンドラの冒頭が1行で済みます。上の 5. もこれで不要になります。

  ```go
  func ObjFromDeleteEvent[T runtime.Object](obj interface{}) (T, bool)
  ```

- 対象が多いので、コントローラごとに PR を分けて少しずつ移すのが現実的です。

## まとめなくてよいもの（名前が同じでも中身が違う）
- **`isPodTerminated`**（`podgc` と `podgroupprotection`）：podgc は「Pending・Running・Unknown のどれでもない」、もう一方は「Succeeded か Failed」です。既知の phase だけなら結果は同じですが、未知の phase の扱いが違います。まとめるなら、どちらの意味にするかを先に決める必要があります。
- **`isOlderThan`**：引数の型が `time.Time` と `metav1.Time` で違い、本体は1行です。まとめる意味はほとんどありません。
- **`copyLabels`**：引数が違い、Go 1.21 以降なら `maps.Clone` で置き換えられる程度の処理です。
- **`AddOrUpdateLabelsOnNode`**：`util/node` 側は `controller` 側を呼ぶラッパーで、重複ではありません。
- **`calculateStatus`**（deployment と replicaset）：計算している中身が別物です。
- **`NewController` / `Register` / `init` / 変換関数など**：パッケージごとの決まった形のコードや生成コードなので対象外です。

## 進める順番の提案
1. **1〜3**：まったく同じコードなので、移すだけで済み、リスクがありません。
2. **5、8**：挙動の差が小さいものです。
3. **4、7**：テストで動作が変わらないことを確かめながら進めます。
4. **6、9**：設計を決めてから、時間をかけて進めます。

**調べた範囲**：関数名が同じものを起点に調べました。名前が違っていて中身だけ似ているコピーは、tombstone 処理を grep で数えた以外は網羅できていません。インデックスで解析が一部欠けていたのはテストファイル5つだけで、今回の結論に使ったファイルには影響ありません。

## Answer 5

`pkg/controller` 配下で重複している処理を調べ、まとめ方を提案します。コードは編集していません。重複の大きさと、まとめやすさ・リスクのバランスで優先度をつけています。

---

## 1. Tombstone(`DeletedFinalStateUnknown`)の取り出し処理 ★優先度: 高

**重複箇所**: テスト以外で **45 箇所・約 30 ファイル**。1 ファイルの中で何度も繰り返されている例もあります。たとえば `deployment/deployment_controller.go:217`, `:344`, `:378` では Deployment / ReplicaSet / Pod それぞれに同じコードが書かれています。

```go
d, ok := obj.(*apps.Deployment)
if !ok {
    tombstone, ok := obj.(cache.DeletedFinalStateUnknown)
    if !ok { utilruntime.HandleError(...); return }
    d, ok = tombstone.Obj.(*apps.Deployment)
    if !ok { utilruntime.HandleError(...); return }
}
```

**提案**: ジェネリクスを使ったヘルパーを `pkg/controller/controller_utils.go` に置きます。将来的には client-go の `tools/cache` に置くのもありです。

```go
// ObjectFromDeleteEvent は削除イベントの obj から T を取り出す。tombstone にも対応する。
func ObjectFromDeleteEvent[T runtime.Object](logger klog.Logger, obj interface{}) (T, bool)
```

呼び出し側は `d, ok := controller.ObjectFromDeleteEvent[*apps.Deployment](logger, obj); if !ok { return }` の 1 行で済みます。コードベースではすでにジェネリクスが使われているので(`ClusterTrustBundlePublisher[T]`)、導入のハードルは低いはずです。

---

## 2. `processNextWorkItem` / `worker` / `handleErr` ★優先度: 高

**重複箇所**: `processNextWorkItem` が **27 箇所**、`worker` / `runWorker` が 36 箇所。中身はほぼ同じ形です。

- `replicaset/replica_set.go:627`, `daemon/daemon_controller.go:398`, `clusterroleaggregation/clusterroleaggregation_controller.go:202` は、エラーメッセージの `"%v failed with : %v"` まで含めて実質コピペです。
- `deployment/deployment_controller.go:486` と `endpoint/endpoints_controller.go:312` には、`maxRetries` を超えたら `Forget` する `handleErr` がそれぞれ別に書かれています。

**提案**: 小さな共通ランナーを `pkg/controller/util/queue` のような場所に作ります。

```go
type SyncFunc func(ctx context.Context, key string) error

type ErrorPolicy struct {
    MaxRetries  int                 // 0 なら無制限
    IgnoreError func(error) bool    // 例: NamespaceTerminatingCause
}

func ProcessNextWorkItem[T comparable](ctx context.Context, q workqueue.TypedRateLimitingInterface[T],
    sync func(context.Context, T) error, p ErrorPolicy) bool

func RunWorker[T comparable](ctx context.Context, q ..., sync ..., p ErrorPolicy) // for ProcessNextWorkItem(...) {}
```

- まずは処理が完全に同じ RS / DS / ClusterRoleAggregation / StatefulSet から置き換えると安全です。
- Deployment や Endpoints のように独自のリトライ上限やエラー判定を持つものは、`ErrorPolicy` で吸収します。
- Job や HPA のように独自のメトリクスやバックオフを持つものは、無理に寄せずに対象外とします。

---

## 3. `resolveControllerRef` ★優先度: 中(簡単で効果が分かりやすい)

**重複箇所**: 6 つのコントローラーで、コメントまで含めてほぼ同じ実装です。違うのは型と Lister だけです。
`statefulset/stateful_set.go:462`, `deployment/deployment_controller.go:461`, `job/job_controller.go:384`, `daemon/daemon_controller.go:824`, `replicaset/replica_set.go:349`, `cronjob/cronjob_controllerv2.go:282`

**提案**:

```go
func ResolveControllerRef[T metav1.Object](
    ref *metav1.OwnerReference, kind string, get func(name string) (T, error),
) (T, bool)
```

呼び出し側は `controller.ResolveControllerRef(ref, controllerKind.Kind, ssc.setLister.StatefulSets(ns).Get)` のように書けます。既存のメソッドはこのヘルパーを呼ぶ薄いラッパーとして残せば、変更範囲を小さく抑えられます。

---

## 4. 採用(adoption)前の `RecheckDeletionTimestamp` クロージャ ★優先度: 中

**重複箇所**: 6 箇所。どれも「API サーバーから最新の親オブジェクトを Get し、UID が違えば `"original X is gone"` エラーを返す」という同じクロージャです。
`deployment/deployment_controller.go:537`, `replicaset/replica_set.go:862`, `job/job_controller.go:880`, `daemon/daemon_controller.go:773`, `daemon/update.go:476`, `statefulset/stateful_set.go:405`

**提案**: `controller_ref_manager.go` に次のヘルパーを追加します。

```go
func RecheckDeletionTimestampByUID[T metav1.Object](
    original metav1.Object, kind string,
    getFresh func(ctx context.Context, ns, name string) (T, error),
) func(context.Context) error
```

すでにある `RecheckDeletionTimestamp` の上に UID チェックを足すだけなので、他の処理への影響は小さいです。

---

## 5. Pod イベントハンドラ(`addPod` / `updatePod` / `deletePod`) ★優先度: 低(慎重に)

**重複箇所**: RS / StatefulSet / DaemonSet / Job / Deployment / Disruption。流れの骨格は共通です。

1. `DeletionTimestamp` が付いていれば `deletePod` に回す
2. ControllerRef があれば resolve して enqueue する
3. ControllerRef がなければ(孤児なら)ラベルが一致する親をすべて enqueue する

`replicaset/replica_set.go:463` と `statefulset/stateful_set.go:256` を比べると、この形がよく分かります。

**ただし**、コントローラーごとの違いが本質的です。expectations の更新(RS と DS だけ)、Job の finalizer 追跡、DaemonSet のノード判定などがそれに当たります。共通化するなら、`resolve` / `getOrphanOwners` / `enqueue` / `onCreationObserved` を差し込む小さなヘルパーまでにとどめます。全体を 1 つの抽象にまとめるのはおすすめしません。可読性が落ち、挙動が変わるリスクが高いからです。

---

## 進め方の提案

1. **1 → 3 → 4** の順に、純粋関数のヘルパーを追加し、既存コードを機械的に置き換えます。いずれもロジックを変えないリファクタリングで、既存のユニットテストで確認できます。
2. **2** は、処理が完全に同じコントローラーから少しずつ移行します。エラーメッセージやログが変わると、ログを監視している運用に影響することがあるので、PR の説明に明記してください。
3. **5** はやらないか、ヘルパー関数の抽出にとどめます。
4. Kubernetes 本体に提案する場合は、パッケージごとに PR を分けてください。SIG Apps(workload 系)と SIG Network(endpoint 系)で OWNERS が分かれているので、そのほうがレビューを通しやすいです。

`garbagecollector`、`nodelifecycle`、`volume/persistentvolume` などは独自性が強いため、今回の調査では 1(tombstone)以外は対象外と判断し、詳しくは見ていません。

## Answer 6

`pkg/controller` の重複を調べました。コードは編集していません。調べ方は、[tool] の[tool]で「同じ名前の関数が複数のパッケージにあるもの」を洗い出し、中身を `sed` と `diff` で一つずつ比べる、というものです。テストファイルと自動生成コード（`zz_generated` の conversion など）は対象から外しています。インデックスの抜けはテストファイルにしかなく、今回使ったファイルは問題ありません。

## まとめる価値が高いもの

### 1. Condition の取得・設定・削除（ほぼ同じ処理が3か所）
- `replication/replication_controller_utils.go:26-61`：`GetCondition` / `SetCondition` / `RemoveCondition` / `filterOutCondition`
- `replicaset/replica_set_utils.go:158-193`：上と同じ4つ。違うのは型だけ（`v1.ReplicationController*` か `apps.ReplicaSet*` か）
- `deployment/util/deployment_util.go:147-156`：`filterOutCondition` だけが同じ

**提案**：ジェネリクスの共通関数を `pkg/controller` か `pkg/controller/util/conditions` に置きます。Go のジェネリクスでは構造体のフィールド（`.Type`）を直接読めないので、「型を取り出す関数」を引数で渡す形にします。
```go
func FilterOut[C any, T comparable](conds []C, t T, typeOf func(C) T) []C
func Get[C any, T comparable](conds []C, t T, typeOf func(C) T) *C
```
**注意**：今の `filterOutCondition` は新しいスライスを作って返し、全部取り除いたときは `nil` を返します。`slices.DeleteFunc` は元のスライスを書き換えるので、そのまま置き換えないでください。返り値が `nil` か空スライスかはテストの比較結果に影響します。

### 2. `slowStartBatch`（2か所）
- `replicaset/replica_set.go:887`：失敗すると最初のエラーだけ返す。バッチの上限なし
- `statefulset/stateful_set_control.go:334`：何番目の呼び出しかを `fn` に渡す。`MaxBatchSize` で上限をかけ、エラーはまとめて返す

**提案**：`controller_utils.go`（初期バッチサイズの定数 `SlowStartInitialBatchSize` がもうここにある）に `SlowStartBatch(count, initial, maxBatch int, fn func(i int) error) (int, error)` を置き、両方から呼びます。
**注意**：ReplicaSet 側のエラーまで「まとめて返す」に揃えると、エラーメッセージが変わります。

### 3. `printDiff`（中身が完全に同じ）
- `garbagecollector/garbagecollector.go:267`
- `resourcequota/resource_quota_controller.go:524`

どちらも、API サーバーから取得したリソース一覧の変化をログに出すのに使っています。`controller_utils.go` などに公開関数として1つにまとめられます。ついでに、古い `sets.NewString` から新しい `sets.New[string]` に移すとよいです。

### 4. `usagesToSet`（中身が完全に同じ）
- `certificates/approver/sarapprove.go:165`
- `certificates/signer/signer.go:307`

両方の親パッケージにある `certificates/certificate_controller_utils.go` に移すのが一番簡単です。どちらも結果を `capihelper` の関数に渡すためだけに使っているので、`capihelper` が `[]KeyUsage` をそのまま受け取るようにする手もあります。

### 5. `uidCache`（ほぼ同じファイル）
- `resourceclaim/uid_cache.go`
- `devicetainteviction/uid_cache.go`

違いはパッケージ名と、メソッドが公開されているかどうか（`Add`/`Has` と `add`/`has`）だけです。`pkg/controller/util/uidcache` のような小さなパッケージにまとめられます。

### 6. 削除イベントの tombstone 処理
- `getEndpointSliceFromDeleteAction` は `endpointslice/endpointslice_controller.go:673` と `endpointslicemirroring/utils.go:195` にあり、違うのはエラーメッセージの文面だけです。
- もっと広く見ると、`cache.DeletedFinalStateUnknown`（削除の最終状態がわからないときに渡される tombstone）をほどく同じ書き方が、テスト以外の31ファイルにあります（`resourceclaim`、`daemon`、`deployment`、`job`、`disruption` など）。

**提案**：`controller.ObjFromDeleteEvent[T any](obj any) (T, bool)` のようなジェネリクス関数を1つ用意し、少しずつ置き換えていきます。まずは EndpointSlice の2か所から始めるのが安全です。

### 7. 証明書 publisher の `recordMetrics`
- `certificates/clustertrustbundlepublisher/metrics.go:57`：`errors.As` を使うので、包まれたエラーからも HTTP ステータスコードを取り出せる
- `certificates/rootcacertpublisher/metrics.go:54`：`*apierrors.StatusError` への型アサーションだけなので、包まれたエラーだと常に `500` になる

「エラーから `code` ラベルを決める部分」だけを共通関数にするのがよいです。メトリクスの定義は各パッケージに残します。
**注意**：rootca 側のラベルの値が変わることがあります（実質的にはバグ修正です）。

## 似ているが、まとめないほうがよいもの
- **`isPodTerminated`**（`podgc/gc_controller.go:136` と `scheduling/podgroupprotection/...:260`）：意味が違います。podgc のほうは、Pending・Running・Unknown 以外の知らない phase も「終了」とみなします。
- **`getPatch`**（`statefulset/stateful_set_utils.go:552` と `daemon/update.go:504`）：StatefulSet は `patchCodec`、DaemonSet は `json.Marshal` でエンコードしています。このパッチは過去のバージョン履歴（ControllerRevision）として保存されるので、出力が1バイトでも変わると問題になります。共通にするとしても、「`spec.template` を取り出して `$patch: replace` を付ける」部分だけにしてください。
- **`calculateStatus`**（deployment と replicaset）：型も処理も別物です。
- **`AddOrUpdateLabelsOnNode`**：`util/node` 版は `controller` 版にログを足しただけのラッパーで、重複ではありません。
- **`isOlderThan`**：`time.Time` 版と `metav1.Time` 版で1行だけなので、まとめる効果が小さいです。
- **`processNextWorkItem` / `runWorker`**（27か所）：決まった書き方の定型コードで、コントローラごとにエラー処理や再試行の仕方が少しずつ違います。共通化すると読みやすさとの引き換えになるので、優先度は低いです。

## 進める順番
1. 3（`printDiff`）、4（`usagesToSet`）、5（`uidCache`）は、動きを変えずにただ移すだけなので、最初に安全にできます。
2. 次に 1（Condition）と 6（tombstone）。ジェネリクスで書き、テストで動きが変わらないことを確かめます。
3. 2（`slowStartBatch`）と 7（`recordMetrics`）は動き（エラーやメトリクスのラベル）が変わるので、別の PR に分けるのがよいです。

**調べ方の限界**：[tool]で探したのは「同じ名前の関数」なので、名前が違うのに中身が同じ重複は拾えていません。tombstone 処理のように書き方のパターンで探すと、ほかにも見つかる可能性があります。

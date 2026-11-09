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

# `pkg/controller` の重複処理と、まとめ方の提案

コードは変更していません。grep で候補を拾い、そのうち代表的な数ファイル（statefulset / deployment / replicaset / job）を実際に読んで確認しました。残りのファイルについては、関数名や定型句が一致していることまでしか見ていません。

## 1. ワーカーループ（`worker` と `processNextWorkItem`）— 重複が最も多い

**重複している内容**
- 約 40 のコントローラが同じ形のループを持っています。
- `worker()` は `for c.processNextWorkItem(ctx) {}` を回すだけです。
- `processNextWorkItem()` は次の順に処理します。
  - `queue.Get()` で取り出し、`defer queue.Done(key)` する
  - `sync(ctx, key)` を呼ぶ
  - 失敗したら `HandleError` と `AddRateLimited`、成功したら `Forget`
- 例: `statefulset/stateful_set.go:502`、`deployment/deployment_controller.go:486`、`replicaset/replica_set.go:627`、`job/job_controller.go:740`、`cronjob/cronjob_controllerv2.go:192` など。
- 1 つのコントローラで複数のキューを持ち、同じループを複数書いている例もあります（`endpoint` の 2 本、`endpointslice` の 3 本、`disruption` の 3 本、`persistentvolume` の 2 本）。

**提案**
`pkg/controller` に、ジェネリックなヘルパーを 1 つ追加します。

```go
// 案: pkg/controller/worker.go
type SyncFunc[T comparable] func(ctx context.Context, key T) error

func RunWorker[T comparable](ctx context.Context, q workqueue.TypedRateLimitingInterface[T],
    sync SyncFunc[T], errMsg string) {
    for processNext(ctx, q, sync, errMsg) {}
}
```

- 各コントローラの `Run()` は `go wait.UntilWithContext(ctx, func(ctx){ controller.RunWorker(ctx, c.queue, c.sync, "Error syncing X") }, time.Second)` だけになります。
- `processNextWorkItem` はコントローラごとに 15〜20 行あるので、全体で数百行減る見込みです。

**注意点**
- 細部が違う実装があります。最大リトライ回数で諦めるもの（endpoints 系）、エラーの種類で `AddAfter` を使い分けるもの、`ctx` ではなく `logger` を受け取るもの（`endpointslice`、`attachdetach`）などです。
- 対策として、エラー時の処理をオプション（例: `OnError func(key, err)`）で差し替えられるようにし、デフォルトは `AddRateLimited` にするのが現実的です。
- 置き換えは差分のない素直な実装から段階的に進めるのが安全です（statefulset、cronjob、ttlafterfinished、certificates など）。

## 2. 削除ハンドラの tombstone 展開 — 45 か所

**重複している内容**
- `cache.DeletedFinalStateUnknown` への型アサーションが 45 か所あります。
- どれも次の 2 段階チェックを毎回書いています。
  1. obj を `*T` として取り出せなければ tombstone か調べる
  2. `tombstone.Obj` を `*T` として取り出す。どちらかで失敗したらエラーを出して return
- 例: `statefulset/stateful_set.go:350-367`。`deployment/deployment_controller.go` だけでも 3 か所（217, 344, 378 行付近）あります。

**提案（2 案）**
- **A. 小さな変更で済む案:** `ObjectFromDeleteEvent[T any](logger, obj) (T, bool)` を `pkg/controller` に追加し、各 `deleteX` の冒頭を 1 行にします。
- **B. 根本的な案:** client-go にはすでに `cache.TypedResourceEventHandlerFuncs[T]` と `DeletedObject[T]` があります（`staging/src/k8s.io/client-go/tools/cache/controller.go:300-461`）。しかし `pkg/controller` では一度も使われていません。これに移行すれば型アサーション自体がなくなります。
  - 注意点: `DeletedObject.OptionalObj` が nil になり得るので、各ハンドラで nil の扱いを決める必要があります。

まず A で統一し、その後コントローラ単位で B へ移る、という順序を勧めます。

## 3. `resolveControllerRef` — 6 つのコントローラでほぼ同じ実装

**重複している内容**
- 対象は statefulset:462、cronjob:282、daemon:824、deployment:461、job:384、replicaset:349 です。
- どれも次の同じ手順です。
  1. Kind が一致しなければ nil
  2. Lister から名前で Get
  3. UID が一致しなければ nil

**提案**
```go
func ResolveControllerRef[T metav1.Object](ref *metav1.OwnerReference, kind string,
    get func(name string) (T, error)) (T, bool)
```
- 呼び出し側は `controller.ResolveControllerRef(ref, controllerKind.Kind, ssc.setLister.StatefulSets(ns).Get)` になります。
- 現在の `GroupVersionKind` を使った ReplicaSet と RC の共用構造も、`kind` を引数で渡すのでそのまま扱えます。

## 4. `RecheckDeletionTimestamp` に渡すクロージャ — 6 か所

**重複している内容**
- 6 か所で、渡しているクロージャの中身が同じです。
  - API から Get し、UID が違えば `"original X %v/%v is gone: got uid ..."` というエラーを返す
- 場所: `statefulset:405`、`daemon/daemon_controller.go:773`、`daemon/update.go:476`、`deployment:537`、`job:880`、`replicaset:862`。
- DaemonSet は同じクロージャを 2 つのファイルに重複して持っています。

**提案**
- `controller_ref_manager.go` に `RecheckDeletionTimestampFor[T metav1.Object](obj T, kind string, get func(ctx, name string, opts metav1.GetOptions) (T, error))` を追加します。
- UID 比較とエラーメッセージ組み立てはその中に移します。
- 呼び出し側は Get メソッドを渡すだけになります。

## 5. Pod イベントの振り分け（`addPod`、`updatePod`、`deletePod`）— 中程度

**重複している内容**
- 次の処理の流れが ReplicaSet、StatefulSet、DaemonSet、Job で共通です。
  1. ControllerRef があれば resolve してキューに入れる
  2. なければ孤児として `getXForPod` で候補を探してキューに入れる
- 孤児の候補探し（`getStatefulSetsForPod`:441、`getDaemonSetsForPod`:440、`getDeploymentForPod`:431、`getPdbForPod`:606）も、Lister を使ったセレクタ一致の処理で似ています。

**提案**
- 3 と 2 をまとめた後、「Pod から担当コントローラを求めてキューに入れる」部分だけを共通化します。
- ただし expectations の更新、ラベル変更の検出、`minReadySeconds` による遅延キューなど、コントローラ固有のロジックが多く混ざっています。無理に共通化すると読みにくくなるので、優先度は低めです。

## 優先順位のおすすめ

| 順 | 対象 | 効果 | リスク |
|---|---|---|---|
| 1 | tombstone 展開（2-A） | 45 か所、機械的に置換できる | 低 |
| 2 | `resolveControllerRef`（3）と再確認用クロージャ（4） | ワークロード系 6 コントローラがすっきりする | 低 |
| 3 | ワーカーループ（1） | 削減量が最大 | 中（エラー処理の違いを吸収する必要がある） |
| 4 | 型付きハンドラへの移行（2-B） | 型安全になる | 中〜高（nil の扱いを個別に判断） |
| 5 | Pod イベントの振り分け（5） | 限定的 | 高 |

どの案も、既存のユニットテスト（`*_test.go`）はそのまま流用できる見込みです。また、コントローラ単位の小さな PR に分けるとレビューを通しやすくなります。進めたい項目があれば、具体的な差分案を作ります。

## Answer 4

`pkg/controller` 配下で重複している処理を5つ見つけ、それぞれのまとめ方を下に書きました。コードは編集していません。grep で関数を探し、代表的な実装をいくつか読み比べた結果です（全ファイルは読んでいません）。各パッケージの挙動は少しずつ違うので、どれも「同じ挙動を保ったまま差し替えられる範囲から始める」前提の提案です。

---

## 1. `resolveControllerRef` がほぼ同じ形で6つある（優先度：高）

| ファイル | 戻り値の型 |
|---|---|
| `deployment/deployment_controller.go:461` | `*apps.Deployment` |
| `replicaset/replica_set.go:349` | `*apps.ReplicaSet` |
| `statefulset/stateful_set.go:462` | `*apps.StatefulSet` |
| `daemon/daemon_controller.go:824` | `*apps.DaemonSet` |
| `job/job_controller.go:384` | `*batch.Job` |
| `cronjob/cronjob_controllerv2.go:282` | `*batchv1.CronJob` |

どれも「Kind を確認 → Lister で名前から取得 → UID を比べる」という同じ手順で、違うのは型と Lister だけです。

**提案:** `controller_utils.go` にジェネリック関数を1つ置きます。

```go
func ResolveControllerRef[T metav1.Object](
    ref *metav1.OwnerReference, kind string, get func(name string) (T, error),
) (T, bool)
```

各コントローラ側は `get` に `dc.dLister.Deployments(ns).Get` を渡す1行で済みます。挙動が完全に同じなので、一番安全に始められる対象です。

## 2. Pod イベントハンドラの tombstone 処理（優先度：高、ただし影響範囲が広い）

- `cache.DeletedFinalStateUnknown` を自前で取り出しているのは、テスト以外で31ファイルあります。
- 例として `replicaset/replica_set.go:581` と `statefulset/stateful_set.go:350` は、エラーログの書き方以外は1行ずつ同じです。
- その後に続く「`GetControllerOf` → `resolveControllerRef` → expectations 更新 → enqueue」という流れも、RS / StatefulSet / DaemonSet / Job / Deployment の `addPod` / `updatePod` / `deletePod` で繰り返されています。

**提案は2段階です。**

1. **すぐできること:** `GetObjectFromTombstone[T](obj any) (T, bool)` のようなヘルパーを作り、tombstone から取り出す部分の約15行を置き換える。
2. **将来の方向:** client-go にはすでに型付きのハンドラがあります（`cache.TypedResourceEventHandler[T]` と `DeletedObject[T]`、`staging/src/k8s.io/client-go/tools/cache/controller.go:301`）。ハンドラを `AddTypedEventHandler` 経由で登録し直せば、tombstone 処理そのものが不要になります。調べた範囲では、`pkg/controller` でこれを使っている箇所はまだありません。

## 3. ワーカーループ（`processNextWorkItem` / `handleErr`）（優先度：中）

- `processNextWorkItem` は27か所で定義されています。
- `handleErr` は deployment、endpoint、endpointslice、endpointslicemirroring、ttlafterfinished で、ほぼ同じ形です。

ただし、細かい挙動が統一されていません。

- Deployment は `NamespaceTerminatingCause` のエラーなら再試行しません（`deployment_controller.go:501`）。
- ReplicaSet と StatefulSet には `maxRetries` がなく、失敗すると無制限に再試行します。
- EndpointSlice は `trackSync(err)` でメトリクスを記録します。

**提案:** 「1件取り出して処理し、エラーなら再試行」を共通の部品にします。

```go
type WorkerOptions struct {
    MaxRetries  int                          // 0 なら無制限
    ShouldRetry func(error) bool             // NamespaceTerminating 判定などを差し込む
    OnResult    func(key string, err error)  // メトリクスやログ
}
func ProcessNextWorkItem(ctx, queue workqueue.TypedRateLimitingInterface[string],
    sync func(context.Context, string) error, opts WorkerOptions) bool
```

違いはオプションで表現するので、挙動は変わりません。どのコントローラにどの再試行ポリシーがあるかも、コード上で一覧できるようになります。

## 4. ControllerRevision の古い履歴の削除（優先度：中）

- `daemon/update.go:355`（`cleanupHistory`）と `statefulset/stateful_set_control.go:182`（`truncateHistory`）は、どちらも「使用中のリビジョンを除き、`RevisionHistoryLimit` を超えた古いものを消す」処理です。
- 違いは2点です。
  - 使用中かどうかの判定キー: DaemonSet は Pod の hash ラベル、StatefulSet はリビジョン名。
  - limit が負の値の扱い: StatefulSet だけが `historyLimit < 0` を確認しています。
- `daemon/update.go:398` の `maxRevision` と `historiesByRevision` も、`history` パッケージの `SortControllerRevisions` などと役割が重なっています。

**提案:** `history` パッケージに次の関数を追加し、両方から呼ぶようにします。

```go
TruncateHistory(revs, isLive func(*ControllerRevision) bool, limit int, del func(*ControllerRevision) error)
```

Deployment の古い ReplicaSet 削除（`deployment/sync.go` の `cleanupDeployment`）も同じ考え方なので、あとで合わせられる可能性があります。

## 5. enqueue 系の小さなラッパー（優先度：低）

`statefulset/stateful_set.go` の `enqueueStatefulSet` / `enqueueSSAfter` のように、「`KeyFunc` でキーを作り、エラーならログを出して `Add` / `AddAfter`」という数行の関数が各所にあります。`EnqueueKey(queue, obj, after)` 程度のヘルパーにまとめられますが、効果は小さいので、1〜3 をやるついでで十分です。

---

## 進め方の提案

1. 最初は **1** です。挙動が完全に同じで、テストも差し替えるだけで済みます。
2. 次に **2 の段階1**（tombstone ヘルパー）です。
3. **3** は、再試行ポリシーの違いを既存のテストで固定してから進めます。この作業の途中で、RS や StatefulSet が無制限に再試行している点が意図どおりかを確認しておくとよいです。
4. **2 の段階2**（型付きハンドラへの移行）と **4** は影響範囲が大きいので、コントローラごとに PR を分けるのが現実的です。

特定のコントローラについて、差し替え後の具体的なコード案が必要なら作ります。

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

コードは編集せず、`pkg/controller` 配下を調べた結果です。似た処理が多い順に並べ、まとめ方の案も付けました。

## 重複が目立つ箇所

### 1. `resolveControllerRef`（6 箇所。中身はほぼ同じ）
- `statefulset/stateful_set.go:462`
- `replicaset/replica_set.go:349`
- `job/job_controller.go:384`
- `deployment/deployment_controller.go:461`
- `daemon/daemon_controller.go:824`
- `cronjob/cronjob_controllerv2.go:282`

どれも「Kind が違えば nil → Lister から名前で Get → UID が違えば nil」という同じ手順で、コメントまで同じです。違うのは Lister と戻り値の型だけです。

**まとめ方:** ジェネリクスを使った共通関数を `controller_utils.go` に置きます。
```go
func ResolveControllerRef[T metav1.Object](
    ref *metav1.OwnerReference, kind string,
    get func(name string) (T, error)) (T, bool)
```
各コントローラーからは `controller.ResolveControllerRef(ref, controllerKind.Kind, ssc.setLister.StatefulSets(ns).Get)` のように1行で呼べます。

### 2. adopt 前に削除されていないか確かめる `canAdoptFunc`（6 箇所）
- `statefulset/stateful_set.go:405`
- `daemon/daemon_controller.go:773`
- `daemon/update.go:476`（DaemonSet 内でも2回出てきます）
- `deployment/deployment_controller.go:537`
- `job/job_controller.go:880`
- `replicaset/replica_set.go:862`

`RecheckDeletionTimestamp` に渡すクロージャの中身（API から最新を Get → UID が違えば `"original X %v/%v is gone: got uid ..."` エラー）がどこも同じです。

**まとめ方:** UID の照合とエラー生成を共通関数にします。
```go
func RecheckDeletionTimestampByUID(kind string, orig metav1.Object,
    get func(ctx context.Context) (metav1.Object, error)) func(context.Context) error
```
呼び出し側は「クライアントで Get する関数」だけを渡せば済みます。DaemonSet の中の2回分も1つにできます。

### 3. 削除イベントの tombstone（`cache.DeletedFinalStateUnknown`）からの取り出し（約 30 ファイル）
例: `replicaset/replica_set.go:581`、`statefulset/stateful_set.go:350`、`deployment`、`daemon`、`job`、`disruption`、`resourceclaim`、`endpointslicemirroring/utils.go` など。

「型アサーション → 失敗したら tombstone として取り出す → 中身の型を確かめる」という約12行が、ほぼすべての削除ハンドラーに書かれています。エラーの出し方は `HandleError(fmt.Errorf(...))` のものと `HandleErrorWithLogger` のものが混ざっています。

**まとめ方:** ジェネリクスで共通関数を作ります。
```go
func ObjFromDeleteEvent[T runtime.Object](logger klog.Logger, obj interface{}) (T, bool)
```
重複がいちばん多い箇所なので、効果も最も大きいです。ついでにエラーの出し方を contextual logging に揃えられます。

### 4. 削除ハンドラー全体の流れ（ReplicaSet / StatefulSet / Daemon / Deployment / Job）
3 の後も「`GetControllerOf` → `resolveControllerRef` → キーを作る → expectations に反映 → enqueue」という流れが共通で残ります。ただし、expectations を使うかどうかや、Job の finalizer 処理などで違いがあります。

**まとめ方:** 3 と 1 を入れた後に残る部分は数行になるので、無理に共通化しない方がよいと考えます。

### 5. Pod から親リソースの候補を探す処理（`getPodReplicaSets` / `getPodJobs` / `getStatefulSetsForPod` / `getDaemonSetsForPod`）
`replicaset/replica_set.go:333`、`job/job_controller.go:364`、`statefulset/stateful_set.go:441`、`daemon/daemon_controller.go:440`

「Lister で検索 → 2件以上なら "user error! more than one ..." と報告」という形が共通です。

**まとめ方:** 件数が多すぎるときの警告だけを `WarnMultipleControllers(logger, kind, pod, names)` のような共通関数にします。Job だけは値のスライスをポインタのスライスに変換しているので、全体を1つにするのは手間に見合いません。

### 6. ワークキューの処理ループ（`processNextWorkItem` が 27 箇所、`handleErr` が 5 箇所）
- `processNextWorkItem`: どれも「`queue.Get` → `defer Done` → 同期処理 → 失敗なら `AddRateLimited`、成功なら `Forget`」です。
- `handleErr`: `endpoint/endpoints_controller.go:326`、`endpointslice/endpointslice_controller.go:349`、`endpointslicemirroring/endpointslicemirroring_controller.go:271` の3つは、`maxRetries` まで再試行して超えたらキューから外すという同じ作りです。`deployment` と `ttlafterfinished` も近い形です。

**まとめ方:**
- `handleErr` は `HandleErrWithMaxRetries(queue, logger, key, err, maxRetries, msg)` として endpoint 系の3つから共通化するのが現実的です。
- `processNextWorkItem` は、共通化しても1関数あたり約10行しか減りません。また、CronJob の `requeueAfter`、Deployment の NamespaceTerminating 扱い、メトリクス記録など、違いが多いです。汎用のワーカー型にまとめると処理の流れが読みにくくなるので、優先度は低いです。

## 進め方の提案
1. **3（tombstone）→ 1（resolveControllerRef）→ 2（canAdopt）** の順に進めるのがおすすめです。どれも動作を変えずに置き換えられ、テストもしやすいです。`controller_utils.go` には `FilterPodsByOwner` や `RecheckDeletionTimestamp` といった共通関数がすでにあるので、そこに加えるのが自然です。
2. 次に 5 の警告部分と、6 の endpoint 系 `handleErr` を共通化します。
3. 4 と、6 の `processNextWorkItem` は見送ってよいと考えます。

## 注意点
- **ReplicaSet** は ReplicationController と実装を共有しているので、Kind を固定値ではなく `rsc.Kind` から取っています。共通関数では Kind を引数で受け取る必要があります。
- **ログやエラーの文言**が変わると、テストや監視に影響することがあります。3 で出し方を揃えるときは、文言の変更を別の PR に分けると安全です。
- **調べた範囲:** 主に apps/batch 系（ReplicaSet、StatefulSet、Job、Deployment、DaemonSet、CronJob）と endpoint 系の本体を読みました。volume 系や nodelifecycle などは件数を grep で数えただけです。3 を進める前に、各ファイルの中身を確認してください。

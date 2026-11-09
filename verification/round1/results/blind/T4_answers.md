# T4 の回答（6 件）

## Answer 1

`IsPodAvailable` を呼んでいる箇所は本体のテストを含めて 17 か所でした。いずれも位置引数で呼んでいるので、シグネチャを変えると全部コンパイルエラーになります。コードは編集していません。

**定義:** `pkg/api/v1/pod/util.go:283`
```go
func IsPodAvailable(pod *v1.Pod, minReadySeconds int32, now metav1.Time) bool
```

## 1. 修正が必要な呼び出し元（`k8s.io/kubernetes/pkg/api/v1/pod` を import している箇所）

### 本番コード (pkg/controller)
| ファイル | 行 | 囲んでいる関数・用途 |
|---|---|---|
| `pkg/controller/daemon/update.go` | 88, 95, 167, 171, 182, 221 | `rollingUpdate` 系（surge あり/なしの新旧 Pod の可用性判定） |
| `pkg/controller/daemon/daemon_controller.go` | 951 | 最も古い新 Pod の可用性判定。`now` には `dsc.failedPodsBackoff.Clock.Now()` を渡している |
| `pkg/controller/daemon/daemon_controller.go` | 1237 | `updateDaemonSetStatus` の NumberAvailable 集計 |
| `pkg/controller/replicaset/replica_set_utils.go` | 113 | `calculateStatus` の AvailableReplicas 集計 |
| `pkg/controller/statefulset/stateful_set_utils.go` | 472 | `isRunningAndAvailable` というラッパー |
| `pkg/controller/statefulset/stateful_set_utils.go` | 497 | `isUnavailable` というラッパー |

### テスト
- `pkg/api/v1/pod/util_test.go:709`：`TestIsPodAvailable`。新しい引数のテストケースも追加が必要です。
- `test/utils/deployment.go:65`
- `test/e2e/apps/daemon_set.go:644, 1202`
- `test/e2e/network/loadbalancer.go:1432`

## 2. 変更が波及しそうな箇所

### StatefulSet のラッパー経由
新しい値をラッパーの外から渡すなら、ラッパーのシグネチャも変わり、次の呼び出しがすべて対象になります。
- `isRunningAndAvailable`：`pkg/controller/statefulset/stateful_set_control.go:385, 485, 529, 885`
- `isUnavailable`：同ファイルの `:634, 644, 762, 850, 902`
- テスト：`stateful_set_control_test.go:1612` と `:4237` のコメントが、`IsPodAvailable` と `isUnavailable` の挙動を前提にしています。

### 同じロジックを持つ兄弟実装（自動では追従しない）
- `pkg/controller/controller_utils.go:1031` の `nextPodAvailabilityCheck` は、コメントにある通り `IsPodAvailable` と同じ判定ロジックを持っています。ここから `FindMinNextPodAvailabilityCheck` を経由して、次の 2 か所で使われています。
  - `pkg/controller/replicaset/replica_set.go:849`
  - `pkg/controller/statefulset/stateful_set.go:601`

  新しい引数で可用性の判定条件が変わる場合、こちらも合わせないと「available になる時刻に再キューする」処理がずれます。
- `staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:32` は同じシグネチャの独立したコピーです。staging から `pkg/` は import できないので、コンパイル上は影響しません。ただし挙動を揃えるかどうかは決める必要があります。
  - このコピーを使っている `test/e2e/framework/daemonset/fixtures.go:103` は kubectl 版を import しているので、今回の変更では壊れません。

### 呼び出し元が渡す値の出どころ
- 呼び出し元はどれも `ds.Spec.MinReadySeconds`、`rs.Spec.MinReadySeconds`、`set.Spec.MinReadySeconds` と `now` を渡しています。新しい引数が Spec や feature gate 由来なら、DaemonSet・ReplicaSet・StatefulSet の各コントローラで値を取り出す処理が要ります。
- Deployment コントローラは `IsPodAvailable` を直接呼んでいません。AvailableReplicas は ReplicaSet の status から集計しているので、`replica_set_utils.go` の変更を通じて間接的に影響を受けます。

## 補足
既存の 17 か所の呼び出しを変えずに済ませるなら、`IsPodAvailableWithX(...)` のような関数を新しく作り、既存の関数をそのラッパーにする方法もあります。

## Answer 2

`pkg/api/v1/pod.IsPodAvailable` の呼び出し元を全部確認しました。コードは編集していません。直接直す必要があるのは本体を除いて 8 ファイル、計 17 箇所です。シグネチャが変わればコンパイルが通らなくなるので、漏れはビルドで見つかります。

現在のシグネチャ（`pkg/api/v1/pod/util.go:283`）:
```go
func IsPodAvailable(pod *v1.Pod, minReadySeconds int32, now metav1.Time) bool
```

## 1. 直接の呼び出し元（必ず修正が必要）

各ファイルで import のエイリアス `podutil` が `k8s.io/kubernetes/pkg/api/v1/pod` を指していることを確認済みです。

**コントローラ本体（本番コード）**
| 箇所 | 呼び出している関数 |
|---|---|
| `pkg/controller/daemon/update.go:88, 95, 167, 171, 182, 221`（6箇所） | `rollingUpdate` |
| `pkg/controller/daemon/daemon_controller.go:951` | `podsShouldBeOnNode` |
| `pkg/controller/daemon/daemon_controller.go:1237` | `updateDaemonSetStatus` |
| `pkg/controller/replicaset/replica_set_utils.go:113` | `calculateStatus` |
| `pkg/controller/statefulset/stateful_set_utils.go:472` | `isRunningAndAvailable`（中で呼んでいるだけの薄い関数） |
| `pkg/controller/statefulset/stateful_set_utils.go:497` | `isUnavailable`（同上） |

**テスト / e2e**
| 箇所 | 呼び出している関数 |
|---|---|
| `pkg/api/v1/pod/util_test.go:709` | `TestIsPodAvailable`（テーブルに新しい引数の列を足し、ケースも追加するのがよさそうです） |
| `test/utils/deployment.go:65` | `LogPodsOfDeployment` |
| `test/e2e/apps/daemon_set.go:644` | `ginkgo.It("should surge pods onto nodes ...")` の中 |
| `test/e2e/apps/daemon_set.go:1202` | `checkDaemonPodsImageAndAvailability` |
| `test/e2e/network/loadbalancer.go:1432` | `testRollingUpdateLBConnectivityDisruption` |

## 2. 間接的に波及しそうな箇所

- **StatefulSet**: `isRunningAndAvailable` と `isUnavailable` が新しい引数を自分の引数として受け取る形にすると、`pkg/controller/statefulset/stateful_set_control.go` の 9 箇所（385, 485, 529, 634, 644, 762, 850, 885, 902）も直す必要があります。この 2 つを呼んでいる `stateful_set_utils_test.go` などのテストも同じです。中で値を決められるなら、この 2 関数の中だけで済みます。
- **DaemonSet / ReplicaSet**: 新しい引数の値を `ds.Spec` や `rs.Spec` から取れるなら、呼び出し元の 1 行を直すだけで済みます。外から渡す値なら、`rollingUpdate`、`calculateStatus`、`updateDaemonSetStatus` の引数も増え、`pkg/controller/daemon/*_test.go` と `pkg/controller/replicaset/*_test.go` にも影響が広がります。
- **`pkg/controller/controller_utils.go:1031` の `nextPodAvailabilityCheck`**: コメントに「`IsPodAvailable` と同じ考え方の処理」とある、別に書かれた関数です。追加する引数で「利用可能」の判定が変わるなら、こちらも合わせないと、次に再チェックする時刻の計算がずれます。
- **`pkg/controller/statefulset/stateful_set_control_test.go:1612`**: `IsPodAvailable` の動きを前提にしたコメントとテストがあります。判定が変わるなら確認が必要です。

## 3. 今回の対象外だが、揃えるか判断が必要なもの

- **`staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:32`** に、同じ名前・同じシグネチャの別の `IsPodAvailable` があります。これは今回の変更ではコンパイルエラーになりません。ただし判定の仕方を揃えるかは決める必要があります。こちらを使っているのは、確認できた範囲では `test/e2e/framework/daemonset/fixtures.go:103` です（import は `k8s.io/kubectl/pkg/util/podutils`）。staging 側を変えると kubectl の公開 API が変わるので、`vendor/` や外部の利用者にも影響します。
- `pkg/api/v1/pod` は staging ではない内部パッケージなので、外部モジュールへの影響はありません。

**次の一手**: 先に「新しい引数の値をどこから取るか（Spec か、呼び出し元から渡すか）」を決めると、上の 2 の範囲が決まります。変更後は `go build ./pkg/... ./test/...` と `go vet` で漏れを確認できます。

## Answer 3

`IsPodAvailable` を直接呼んでいる箇所は、テスト 1 件を含めて 8 ファイル・17 箇所あります。非 vendor の Go ファイルを `grep` で全件確認しました。関数を値として渡している箇所はないので、この 17 箇所でコンパイルエラーは全部です。`k8s.io/kubernetes` は vendor されていないので、外のモジュールが巻き込まれることもありません。コードは編集していません。

定義は `pkg/api/v1/pod/util.go:283` の `func IsPodAvailable(pod *v1.Pod, minReadySeconds int32, now metav1.Time) bool` です。

## 1. 直接の呼び出し元（必ず修正が必要）

### 本体コード（コントローラー）
| ファイル:行 | 中身 |
|---|---|
| `pkg/controller/daemon/update.go:88, 95` | `rollingUpdate` の中。サージ時に新しい Pod / 古い Pod が使えるかを判定 |
| `pkg/controller/daemon/update.go:167, 171, 182` | 同じく `rollingUpdate` の中 |
| `pkg/controller/daemon/update.go:221` | 同じく `rollingUpdate` の中 |
| `pkg/controller/daemon/daemon_controller.go:951` | 新しい Pod が使えるようになったら古い Pod を消す判定。`now` は `dsc.failedPodsBackoff.Clock.Now()` から作っている |
| `pkg/controller/daemon/daemon_controller.go:1237` | `updateDaemonSetStatus` の中で `numberAvailable` を数える |
| `pkg/controller/replicaset/replica_set_utils.go:113` | `calculateStatus` の中で `availableReplicasCount` を数える |
| `pkg/controller/statefulset/stateful_set_utils.go:472` | ラッパー関数 `isRunningAndAvailable` |
| `pkg/controller/statefulset/stateful_set_utils.go:497` | ラッパー関数 `isUnavailable` |

### テストと E2E
| ファイル:行 | 中身 |
|---|---|
| `pkg/api/v1/pod/util_test.go:709` | `TestIsPodAvailable`。テーブルに新しい引数のケースも足すことになる |
| `test/utils/deployment.go:65` | `LogPodsOfDeployment` |
| `test/e2e/apps/daemon_set.go:644, 1202` | |
| `test/e2e/network/loadbalancer.go:1432` | |

## 2. 変更が波及しそうな箇所

**StatefulSet のラッパー関数（影響が最も大きい）**
新しい引数の値をラッパーの外から渡すことにすると、`isRunningAndAvailable` と `isUnavailable` のシグネチャも変わります。その場合は `pkg/controller/statefulset/stateful_set_control.go` の次の 9 箇所も直すことになります。
- `isRunningAndAvailable`: 385, 485, 529, 885
- `isUnavailable`: 634, 644, 762, 850, 902

テスト `stateful_set_control_test.go:1612` と `:4237` も、この判定の挙動を前提にしています。

**値をどこから持ってくるか**
DaemonSet の `rollingUpdate` と `updateDaemonSetStatus`、ReplicaSet の `calculateStatus` は、今は `ds.Spec` / `rs.Spec` と `now` しか使っていません。新しい引数が Spec 以外の情報（Clock や feature gate など）から来る場合は、これらの関数の引数も増え、さらにその呼び出し元とテストまで変更が広がります。

**同じロジックをもう一度書いている箇所（シグネチャ上の依存はないが、挙動をそろえる必要がありそう）**
- `pkg/controller/controller_utils.go:1031` の `nextPodAvailabilityCheck`。コメントに「`podutil.IsPodAvailable` と同様のロジック」と書かれており、ReplicaSet や Deployment が次に再評価する時刻を決めるのに使われています。新しい引数で「使えるかどうか」の判定が変わるなら、ここも合わせないと、再キューのタイミングと実際の判定がずれます。
- `staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:32` にある、kubectl 側の同名の別実装 `IsPodAvailable`。これはパッケージが別なので、今回の変更でコンパイルエラーにはなりません。ただし `test/e2e/framework/daemonset/fixtures.go:103` がこちらを使っていて、E2E の中に「`podutil` 版を使う箇所」と「kubectl 版を使う箇所」が混在しています。挙動をそろえるなら、こちらも対象にするかどうか決める必要があります。

## 修正規模の目安
- **最小**（引数をデフォルト値で埋めるだけ）: 直接の呼び出し元 17 箇所
- **StatefulSet のラッパーも変える場合**: さらに `stateful_set_control.go` の 9 箇所
- **値を上位から渡す場合**: DaemonSet・ReplicaSet・StatefulSet それぞれの呼び出しの連鎖とテスト

追加する引数の意味（何を表す値か）が分かれば、どこまで変更が届くかをもう少し絞り込めます。

## Answer 4

`IsPodAvailable` を直接呼んでいる箇所は 20 か所で、本体側が 12、テスト・e2e 側が 8 です。そのうち statefulset の 2 つのラッパー経由で、さらに 9 か所に波及する可能性があります。調べ方は grep と import の確認で、コードは編集していません。

**現在のシグネチャ** (`pkg/api/v1/pod/util.go:283`)
```go
func IsPodAvailable(pod *v1.Pod, minReadySeconds int32, now metav1.Time) bool
```

## 1. 必ず修正が必要な直接の呼び出し元

### 本体のコントローラ (12 か所)
| ファイル | 行 | 呼んでいる関数 |
|---|---|---|
| `pkg/controller/daemon/update.go` | 88, 95, 167, 171, 182, 221 | `rollingUpdate` |
| `pkg/controller/daemon/daemon_controller.go` | 951 | `podsShouldBeOnNode` |
| `pkg/controller/daemon/daemon_controller.go` | 1237 | `updateDaemonSetStatus` |
| `pkg/controller/replicaset/replica_set_utils.go` | 113 | `calculateStatus` |
| `pkg/controller/statefulset/stateful_set_utils.go` | 472 | `isRunningAndAvailable` (ラッパー) |
| `pkg/controller/statefulset/stateful_set_utils.go` | 497 | `isUnavailable` (ラッパー) |

### テストと e2e (8 か所)
- `pkg/api/v1/pod/util_test.go:709`: `TestIsPodAvailable`。テーブルに新しい引数用のフィールドとケースを足す必要もあります。
- `test/utils/deployment.go:65`: `LogPodsOfDeployment`
- `test/e2e/apps/daemon_set.go:644`, `:1202`。1202 は `checkDaemonPodsImageAndAvailability` の中です。
- `test/e2e/network/loadbalancer.go:1432`: `testRollingUpdateLBConnectivityDisruption`

## 2. 波及しそうな箇所

- **statefulset の 2 つのラッパー**: 新しい引数を呼び出し元から渡す設計にすると、ラッパーのシグネチャも変わります。その場合、修正は次のファイルにも広がります。
  - `pkg/controller/statefulset/stateful_set_control.go`: ラッパーの呼び出しが 9 か所
  - `pkg/controller/statefulset/stateful_set_control_test.go`: 4237 行のコメントが `isUnavailable()` に触れています。1612 行のコメントは `IsPodAvailable` の判定ロジックに触れています。
- **daemon と replicaset**: 呼んでいる関数の中で `ds` / `rs` と `now` は手元にあります。新しい引数がその場で用意できる値ならシグネチャの変更は中で収まります。用意できない値なら、`rollingUpdate`、`updateDaemonSetStatus`、`calculateStatus` などの引数にまで広がります。
- **`pkg/controller/controller_utils.go:1031` の `nextPodAvailabilityCheck`**: コメントに「`podutil.IsPodAvailable` と同じ考え方のロジック」とあり、呼び出しはしていません。ただし、新しい引数で「利用可能」の判定が変わるなら、ここも同じように変えないと、再キューのタイミングと判定がずれる恐れがあります。

## 3. 修正不要だが注意する箇所

- **`staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:32`**: 同じ名前とシグネチャの別の関数です。kubectl 側にある独立したコピーなので、今回の変更の影響は受けません。
  - `test/e2e/framework/daemonset/fixtures.go:103` もこの kubectl 版の `podutils.IsPodAvailable` を import しています。名前は同じですが、修正対象には入りません。
  - 2 つの実装の挙動をそろえておきたい場合は、kubectl 側も別に変更を検討してください。
- **vendor**: `vendor/` の中に `pkg/api/v1/pod` 版を参照している箇所はありませんでした。

関数を値として渡している使い方 (呼び出しではない参照) も `IsPodAvailable\b` で探しましたが、見つかりませんでした。

## Answer 5

`IsPodAvailable` を直接呼んでいるのは 9 ファイル、計 20 箇所です（定義とテストを含む）。調べた範囲ではコードは編集していません。

## 定義
`pkg/api/v1/pod/util.go:283`
```go
func IsPodAvailable(pod *v1.Pod, minReadySeconds int32, now metav1.Time) bool
```

## 1. シグネチャ変更でコンパイルが通らなくなる呼び出し元

呼び出しはすべて `podutil "k8s.io/kubernetes/pkg/api/v1/pod"` の import 経由です。

**本体側（コントローラ）: 4 ファイル、12 箇所**
| ファイル | 行 |
|---|---|
| `pkg/controller/daemon/update.go` | 88, 95, 167, 171, 182, 221 |
| `pkg/controller/daemon/daemon_controller.go` | 951, 1237 |
| `pkg/controller/replicaset/replica_set_utils.go` | 113 |
| `pkg/controller/statefulset/stateful_set_utils.go` | 472（`isRunningAndAvailable`）, 497（`isUnavailable`） |

**テスト / e2e: 4 ファイル、5 箇所**
| ファイル | 行 |
|---|---|
| `pkg/api/v1/pod/util_test.go` | 709（`TestIsPodAvailable`。テーブルに新しい引数のフィールドを足すことになりそう） |
| `test/utils/deployment.go` | 65 |
| `test/e2e/network/loadbalancer.go` | 1432 |
| `test/e2e/apps/daemon_set.go` | 644, 1202 |

関数値として渡している箇所（`IsPodAvailable` を `()` なしで参照している箇所）はありませんでした。`vendor/` にも参照はありません。

## 2. 変更が波及しそうな箇所

- **StatefulSet のラッパー関数**: `isRunningAndAvailable` と `isUnavailable`（`stateful_set_utils.go:471`, `:496`）。新しい引数を外から渡す設計にすると、この 2 関数のシグネチャも変わります。その場合の呼び出し元は `stateful_set_control.go` に 9 箇所、`stateful_set_control_test.go` に 1 箇所です。
- **呼び出し元の上位の関数**: 新しい引数の値を呼び出し元で用意できない場合（DaemonSet の `ds.Spec` や ReplicaSet の `rs.Spec` などから取れない値の場合）、`daemon/update.go` や `daemon_controller.go`、`replica_set_utils.go` を呼んでいる関数にも引数の追加が広がります。

## 3. コンパイルには影響しないが、整合を確認したい箇所

- **`pkg/controller/controller_utils.go:1031` の `nextPodAvailabilityCheck`**: コメントに「podutil.IsPodAvailable と同じロジックを実装している」とあります。次に Pod が available かを判定し直すタイミングを計算する関数です。追加する引数で available の判定条件が変わるなら、ここも合わせないとコントローラの再判定（requeue）のタイミングがずれます。
- **`staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:32`**: kubectl 側に同じ名前の別実装があります。境界の比較が少し違い、こちらは `Before`、本体は `Compare <= 0` です。
  - `test/e2e/framework/daemonset/fixtures.go:103` はこちらの kubectl 版を呼んでいるので、今回の変更では直す必要はありません。
  - 両方の実装をそろえたいなら、kubectl 版の修正は別途検討が必要です。
- **`pkg/controller/statefulset/stateful_set_control_test.go:1612`**: `IsPodAvailable` の挙動を前提にしたコメントがあります。判定ロジックが変わる場合はこのテストケースも見直してください。
- **外部の利用者**: `k8s.io/kubernetes/pkg/...` を直接 import しているリポジトリ外のコードは壊れます。staging 配下のパッケージは `k8s.io/kubernetes` を import できない構成なので、リポジトリ内では上に挙げた箇所で全部です。

方針として、呼び出し元の大半が同じ値を渡すことになるなら、既存の関数は残して `IsPodAvailableWithX(...)` のような新しい関数を足すほうが修正範囲は小さく済みます。既存の関数は新しい関数をデフォルト値で呼ぶ形にします。

## Answer 6

`IsPodAvailable` を直接呼んでいる箇所は、テストを含めて 8 ファイル・19 箇所でした。コードは編集していません。調べ方はリポジトリ全体（`vendor/` を除く）の grep と、各ファイルの import alias の確認です。

対象の関数は `pkg/api/v1/pod/util.go:283` の `func IsPodAvailable(pod *v1.Pod, minReadySeconds int32, now metav1.Time) bool` です。

## 1. 修正が必須の呼び出し元（直接呼び出し）

### 本体コード
| ファイル | 行 | 呼び出している関数 |
|---|---|---|
| `pkg/controller/daemon/daemon_controller.go` | 951 | `(*DaemonSetsController).podsShouldBeOnNode` |
| `pkg/controller/daemon/daemon_controller.go` | 1237 | `(*DaemonSetsController).updateDaemonSetStatus` |
| `pkg/controller/daemon/update.go` | 88, 95, 167, 171, 182, 221 | `(*DaemonSetsController).rollingUpdate`（1 関数の中に 6 箇所） |
| `pkg/controller/replicaset/replica_set_utils.go` | 113 | `calculateStatus` |
| `pkg/controller/statefulset/stateful_set_utils.go` | 472 | `isRunningAndAvailable` |
| `pkg/controller/statefulset/stateful_set_utils.go` | 497 | `isUnavailable` |

### テスト・e2e・テストユーティリティ
| ファイル | 行 | 呼び出している関数 |
|---|---|---|
| `pkg/api/v1/pod/util_test.go` | 709 | `TestIsPodAvailable`（テストケースの構造体にも新しい引数の項目が要る） |
| `test/utils/deployment.go` | 65 | `LogPodsOfDeployment` |
| `test/e2e/apps/daemon_set.go` | 644 | `updateDaemonSetWithRetries` 以降にある無名関数 |
| `test/e2e/apps/daemon_set.go` | 1202 | `checkDaemonPodsImageAndAvailability` |
| `test/e2e/network/loadbalancer.go` | 1432 | `testRollingUpdateLBConnectivityDisruption` |

どのファイルも `podutil "k8s.io/kubernetes/pkg/api/v1/pod"` として import していることを確認しました。関数を値として渡している箇所は見つかっていません。

## 2. 変更が広がりそうな箇所

**StatefulSet のラッパー関数（影響が一番大きい）**
- 新しい引数を呼び出し元から渡す設計にすると、`isRunningAndAvailable(pod, minReadySeconds, now)` と `isUnavailable(...)` の引数も増やすことになります。
- その場合、`pkg/controller/statefulset/stateful_set_control.go` の 9 箇所も修正対象になります。
  - `isRunningAndAvailable`: 385, 485, 529, 885 行
  - `isUnavailable`: 634, 644, 762, 850, 902 行
- 新しい値を `set` / `pod` から取れるなら、ラッパーの中で吸収してここで止められます。

**DaemonSet の `rollingUpdate`**
- 1 つの関数の中に 6 箇所あります。新しい値を `ds` から取れないと、`rollingUpdate` 自体の引数が増える可能性があります。

**ReplicaSet の `calculateStatus`**
- 新しい値を `rs` から取れない場合、`calculateStatus` の引数が増え、その呼び出し元とテストにも影響します。

## 3. 呼び出してはいないが、合わせて確認すべき箇所

- **`pkg/controller/controller_utils.go:1031` の `nextPodAvailabilityCheck`**
  - コメントに「`podutil.IsPodAvailable` と同じようなロジック」とあり、利用可能になるまでの時間を同じ考え方で別に計算しています。
  - `IsPodAvailable` の判定が変わるのにこちらを直さないと、2 つの結果が食い違います（再チェックのタイミングがずれる）。
- **`staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:32` の `podutils.IsPodAvailable`**
  - 引数が同じ別の実装です。今回の変更では壊れません。
  - ただし判定ロジックを変えるなら、同じ変更をここにも入れるかどうか決める必要があります。staging なので公開 API です。
  - これを使っている `test/e2e/framework/daemonset/fixtures.go:103` は、今回の修正対象には含まれません。
- **`pkg/controller/statefulset/stateful_set_control_test.go`**
  - 1612 行と 4237 行のコメントが `IsPodAvailable` / `isUnavailable` の動作を前提にしています。ラッパーの引数を変えるなら、このテストの見直しも要ります。

## まとめ
- 最低限の修正は 8 ファイル・19 箇所です。
- 新しい値の渡し方によっては、StatefulSet のラッパー経由で `stateful_set_control.go` の 9 箇所が加わります。DaemonSet と ReplicaSet の関数の引数が増える可能性もあります。
- 判定ロジックを変える場合は、`nextPodAvailabilityCheck` と kubectl 側の `podutils.IsPodAvailable` も合わせて確認してください。

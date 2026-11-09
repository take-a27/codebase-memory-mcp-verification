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

`IsPodAvailable`（`pkg/api/v1/pod/util.go:283`）を直接呼んでいる箇所は、テストを含めて **19 か所**あります。コードは編集していません。調べ方は次のとおりです。

- **呼び出し元の洗い出し:** [tool] の `[tool]`（呼び出し元方向）と、リポジトリ全体の grep `\bIsPodAvailable\b` の両方で探し、結果は一致しました。
- **インデックスの確認:** 結論に使ったファイルはすべてカバレッジを確認し、問題は記録されていませんでした。ただしこのツール自身が「目安であり完全性の保証ではない」としているので、参考程度に見てください。
- **staging からの利用:** staging 配下に `k8s.io/kubernetes/pkg/api/v1/pod` を import しているファイルはありませんでした。

## 1. 修正が必須の呼び出し元（コンパイルが通らなくなる箇所）

### 本番コード（14 か所）
| ファイル | 行 | 呼び出している関数 |
|---|---|---|
| `pkg/controller/daemon/daemon_controller.go` | 951 | `podsShouldBeOnNode` |
| `pkg/controller/daemon/daemon_controller.go` | 1237 | `updateDaemonSetStatus` |
| `pkg/controller/daemon/update.go` | 88, 95, 167, 171, 182, 221 | `rollingUpdate` |
| `pkg/controller/replicaset/replica_set_utils.go` | 113 | `calculateStatus` |
| `pkg/controller/statefulset/stateful_set_utils.go` | 472 | `isRunningAndAvailable`（薄いラッパー） |
| `pkg/controller/statefulset/stateful_set_utils.go` | 497 | `isUnavailable`（薄いラッパー） |

### テストと e2e（5 か所）
| ファイル | 行 | 呼び出している関数 |
|---|---|---|
| `pkg/api/v1/pod/util_test.go` | 709 | `TestIsPodAvailable`（テーブルに新しい引数用のフィールドとケースを足す必要あり） |
| `test/utils/deployment.go` | 65 | `LogPodsOfDeployment` |
| `test/e2e/apps/daemon_set.go` | 644 | `checkDaemonPodsImageAndAvailability` |
| `test/e2e/apps/daemon_set.go` | 1202 | ファイル内の無名関数 |
| `test/e2e/network/loadbalancer.go` | 1432 | `testRollingUpdateLBConnectivityDisruption` |

## 2. 変更が波及しそうな箇所

新しい引数の値を呼び出し元がその場で用意できない場合は、さらに上の関数のシグネチャも変えることになります。

- **StatefulSet:** ラッパーの `isRunningAndAvailable` と `isUnavailable` に引数を足すと、`pkg/controller/statefulset/stateful_set_control.go` の 10 か所も直すことになります（385, 485, 529, 634, 644, 762, 850, 885, 902 行）。呼び出し元の関数は `computeReplicaStatus`、`processReplica`、`processCondemned`、`updateStatefulSet`、`updateStatefulSetAfterInvariantEstablished` です。
  - `stateful_set_control_test.go:4237` 付近に、`isUnavailable` に `MinReadySeconds` を渡すことを確かめるテストがあるので、合わせて見直してください。
- **DaemonSet:** さらに上の呼び出し元は `manage` → `syncDaemonSet`、`updateDaemonSet` です。値を `ds.Spec` から取れるなら、影響は `daemon_controller.go` と `update.go` の中だけで済みます。
  - ベンチマークの `BenchmarkUpdateDaemonSetStatus` も間接的に通るので、確認対象です。
- **ReplicaSet:** さらに上の呼び出し元は `syncReplicaSet` です。テストの `TestCalculateStatus` と `TestCalculateStatusConditions` は `calculateStatus` 経由で通るので、期待値が変わるかどうかを確認してください。
- **e2e やテスト用ユーティリティ:** さらに上の呼び出し元は `WaitForDeploymentWithCondition`、`checkRollingUpdateStatus`（`test/utils`）、`failureTrap`、`watchRecreateDeployment`（`test/e2e/apps`）です。

## 3. コンパイルは通るが、ロジックを揃えるか検討したい箇所

- **`pkg/controller/controller_utils.go:1032` の `nextPodAvailabilityCheck`:** コメントにあるとおり、`IsPodAvailable` と同じ判定ロジックを複製した関数です。追加する引数が「使用可能かどうか」の判定に影響する場合は、こちらも揃えないと次の再チェック時刻の計算がずれます。
  - 呼び出し元は `findMinNextPodAvailabilitySimpleCheck` と `FindMinNextPodAvailabilityCheck`（1056, 1077 行）、テストは `controller_utils_test.go:983` です。
- **`staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:32` の `IsPodAvailable`:** 名前もシグネチャも同じですが、kubectl 側にある別の実装です。比較条件が `Before` で、本体の `Compare <= 0` とは微妙に違います。
  - この関数は `test/e2e/framework/daemonset/fixtures.go:103` から使われています。
  - 今回の変更でコンパイルが壊れることはありませんが、挙動を揃えるかどうかは決めておく必要があります。

DaemonSet、ReplicaSet、StatefulSet のどこでも `minReadySeconds` はコントローラの spec から渡しているので、新しい引数も spec やコントローラの状態から取れるなら、影響は上の 1 と StatefulSet のラッパー 2 つで止まります。取れない場合は、2 に挙げた同期処理まで引数を通すことになります。

## Answer 5

`pkg/api/v1/pod.IsPodAvailable` の呼び出し元は、本体コードとテストを合わせて **8 ファイル・20 箇所**ありました。調べ方は、[tool]で呼び出し元をたどり、grep で全箇所を突き合わせました。インデックスの対象範囲チェックでも、関係するファイルに漏れは報告されていません。コードは編集していません。

現在のシグネチャ（`pkg/api/v1/pod/util.go:283`）:
```go
func IsPodAvailable(pod *v1.Pod, minReadySeconds int32, now metav1.Time) bool
```

## 1. 直接の修正が必要な呼び出し箇所

### 本体コード（コントローラ）
| ファイル | 行 | 呼び出している関数 |
|---|---|---|
| `pkg/controller/daemon/update.go` | 88, 95, 167, 171, 182, 221 | `rollingUpdate`（6 箇所） |
| `pkg/controller/daemon/daemon_controller.go` | 951 | `podsShouldBeOnNode` |
| `pkg/controller/daemon/daemon_controller.go` | 1237 | `updateDaemonSetStatus` |
| `pkg/controller/replicaset/replica_set_utils.go` | 113 | `calculateStatus` |
| `pkg/controller/statefulset/stateful_set_utils.go` | 472 | `isRunningAndAvailable`（中継用の関数） |
| `pkg/controller/statefulset/stateful_set_utils.go` | 497 | `isUnavailable`（中継用の関数） |

### テスト・e2e
| ファイル | 行 |
|---|---|
| `pkg/api/v1/pod/util_test.go` | 709（`TestIsPodAvailable`。テストケースの構造体に新しい項目を足す必要もありそう） |
| `test/utils/deployment.go` | 65（`LogPodsOfDeployment`） |
| `test/e2e/apps/daemon_set.go` | 644, 1202 |
| `test/e2e/network/loadbalancer.go` | 1432 |

## 2. 変更が波及しそうな箇所

**StatefulSet の中継用関数（波及が一番大きい）**
- `isRunningAndAvailable` と `isUnavailable` は、`IsPodAvailable` をそのまま呼んでいるだけの薄い関数です。新しい引数の値を呼び出し側から渡す設計にする場合は、この 2 つのシグネチャも変わります。
- その場合、`pkg/controller/statefulset/stateful_set_control.go` の次の 9 箇所も直すことになります。
  - 行: 385, 485, 529, 634, 644, 762, 850, 885, 902
  - 含まれる関数: `computeReplicaStatus` / `processReplica` / `processCondemned` / `updateStatefulSetAfterInvariantEstablished` など
- 新しい値を `set.Spec` などから取るなら、`minReadySeconds` と同じように `set` から引き回す形になります。

**新しい値をどこから取るか**
各コントローラは現在、`ds.Spec.MinReadySeconds`・`rs.Spec.MinReadySeconds`・`set.Spec.MinReadySeconds` と「現在時刻」を渡しています。新しい引数も Spec や API の項目から取るなら、次の 2 点に注意が必要です。
- DaemonSet では `rollingUpdate` と `updateDaemonSetStatus` の中で値を用意する必要があります。
- 上位の `manage` / `syncDaemonSet` / `updateDaemonSet` も、間接的に影響を受けます。

**同じ判定を別に実装している関数（シグネチャは変わらないが要確認）**
- `pkg/controller/controller_utils.go:1032` の `nextPodAvailabilityCheck` は、コメントにある通り `IsPodAvailable` と同じような判定を独自に書いています。
- これは ReplicaSet や Deployment が「次にいつ利用可能かを再判定するか」を決めるのに使われます。
- 追加する引数で判定内容が変わる場合は、ここも合わせないと、再判定のタイミングと実際の判定結果が食い違います。

**影響を受けるテスト**
- [tool]上で間接的な呼び出し元として出たもの:
  - `TestCalculateStatus` / `TestCalculateStatusConditions`（replicaset）
  - `BenchmarkUpdateDaemonSetStatus`（daemon）
  - `pkg/controller/statefulset/stateful_set_control_test.go`
- e2e では `checkDaemonPodsImageAndAvailability`、`testRollingUpdateLBConnectivityDisruption`、`test/utils` の `WaitForDeploymentWithCondition` / `checkRollingUpdateStatus` 経由のテストが該当します。

## 3. 影響しないもの（同名の別関数）
- `staging/src/k8s.io/kubectl/pkg/util/podutils.IsPodAvailable` は同じ名前・同じシグネチャですが、別のパッケージにある別の関数です。
- `test/e2e/framework/daemonset/fixtures.go:103` が呼んでいるのはこちらの kubectl 版なので、今回の変更とは関係ありません。
- ただし、2 つの判定内容を揃えておきたいなら、こちらにも同じ変更が必要になる可能性があります。
- `vendor/` と `staging/` の中に、`k8s.io/kubernetes/pkg/api/v1/pod` を読み込んでいるファイルはありません。

## 4. 変更方法の選択肢
- 呼び出し箇所をすべて直すと、本体側で 9 箇所、StatefulSet の中継用関数まで含めると +9 箇所になります。
- これを避けたいなら、別案があります。新しい関数（例: `IsPodAvailableWithX`）を追加し、既存の `IsPodAvailable` はそれを既定値で呼ぶだけの関数として残す方法です。この場合、波及は新しい引数が本当に必要なコントローラだけに限られます。

## Answer 6

修正が必要な直接の呼び出し箇所は **8 ファイル・計 18 箇所** です（定義元のテストを含む）。調べ方は、[tool] で呼び出し元を 2 段階さかのぼり、grep で全件を裏取りしました。[tool]の直接呼び出し元 11 関数と grep の結果は一致し、関係するファイルのインデックスにも問題は見つかりませんでした。コードは編集していません。

対象の関数は `pkg/api/v1/pod/util.go:283` の `IsPodAvailable(pod *v1.Pod, minReadySeconds int32, now metav1.Time) bool` です。

## 1. 修正が必要な直接の呼び出し元

各ファイルでは `k8s.io/kubernetes/pkg/api/v1/pod` を `podutil` という別名で import しています。

**本体のコード（コントローラー）**
| ファイル | 行 | 呼んでいる関数 |
|---|---|---|
| `pkg/controller/daemon/update.go` | 88, 95, 167, 171, 182, 221（6 箇所） | `rollingUpdate` |
| `pkg/controller/daemon/daemon_controller.go` | 951 | `podsShouldBeOnNode` |
| `pkg/controller/daemon/daemon_controller.go` | 1237 | `updateDaemonSetStatus` |
| `pkg/controller/replicaset/replica_set_utils.go` | 113 | `calculateStatus` |
| `pkg/controller/statefulset/stateful_set_utils.go` | 472 | `isRunningAndAvailable`（薄いラッパー） |
| `pkg/controller/statefulset/stateful_set_utils.go` | 497 | `isUnavailable`（薄いラッパー） |

**テスト・テスト用ユーティリティ**
| ファイル | 行 | 呼んでいる関数 |
|---|---|---|
| `pkg/api/v1/pod/util_test.go` | 709 | `TestIsPodAvailable`（テストケースの構造体にもフィールド追加が必要） |
| `test/utils/deployment.go` | 65 | `LogPodsOfDeployment` |
| `test/e2e/apps/daemon_set.go` | 644 | `ginkgo.It("should surge pods onto nodes …")`（522 行目から始まるテスト）内のクロージャ |
| `test/e2e/apps/daemon_set.go` | 1202 | `checkDaemonPodsImageAndAvailability` |
| `test/e2e/network/loadbalancer.go` | 1432 | `testRollingUpdateLBConnectivityDisruption` |

## 2. 変更が波及しそうな箇所

追加する引数の値を呼び出し元で用意できない場合、その上位の関数にも引数が増えていきます。

- **StatefulSet**：影響が最も大きいです。ラッパーの `isRunningAndAvailable` と `isUnavailable` が `pkg/controller/statefulset/stateful_set_control.go` の 9 箇所（385, 485, 529, 634, 644, 762, 850, 885, 902 行目）から呼ばれています。ラッパーにも引数を足すなら、この 9 箇所すべてと、それを含む `computeReplicaStatus`、`processReplica`、`processCondemned`、`updateStatefulSetAfterInvariantEstablished`、`updateStatefulSet` まで変わる可能性があります。ラッパーの中で値を決めれば、ここで止められます。
- **DaemonSet**：`rollingUpdate`、`podsShouldBeOnNode`、`updateDaemonSetStatus` の上には `manage`、`syncDaemonSet`、`updateDaemonSet` があります。テストでは `BenchmarkUpdateDaemonSetStatus` が影響を受けます。
- **ReplicaSet**：`calculateStatus` の上に `syncReplicaSet` があります。`calculateStatus` の引数が変わる場合は、テストの `TestCalculateStatus` と `TestCalculateStatusConditions` も修正が必要です。
- **テスト用ユーティリティ**：`LogPodsOfDeployment` の上に `WaitForDeploymentWithCondition`、`checkRollingUpdateStatus`、`watchRecreateDeployment`、`failureTrap` があります。

## 3. コンパイルは通るが確認したほうがよい箇所

- **`pkg/controller/controller_utils.go:1031` の `nextPodAvailabilityCheck`**：コメントに「podutil.IsPodAvailable と同様のロジック」とあり、判定を自前で書き直しています。追加する引数が「使用可能かどうか」の判定に影響するなら、ここも揃えないと、次に再判定する時刻の計算と実際の判定が食い違います。
- **kubectl 側の同名の関数**：`staging/src/k8s.io/kubectl/pkg/util/podutils/podutils.go:32` に、シグネチャが同じ別の `IsPodAvailable` があります。これは今回の変更の対象外で、`test/e2e/framework/daemonset/fixtures.go:103` が呼んでいるのもこちらです。ただし両者の動作を揃えるかどうかは決めておいたほうがよいです。
- **テストのコメント**：`pkg/controller/statefulset/stateful_set_control_test.go:1612` と `:4237` に、この関数の動作を前提にしたコメントがあります。

vendor ディレクトリには、kubectl 版以外にこの関数を参照している箇所はありませんでした。

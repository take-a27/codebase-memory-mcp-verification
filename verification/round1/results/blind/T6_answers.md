# T6 の回答（6 件）

## Answer 1

`pkg/scheduler/framework/plugins/` 配下で `FilterPlugin` を実装しているプラグインは、本番用だけで 15 個あります。`var _ fwk.FilterPlugin = ...` の型アサーションと `Filter()` メソッドの定義の両方で照合したので、漏れはないはずです。

| プラグイン | ファイル | 何をチェックするか |
|---|---|---|
| **NodeName** | `nodename/node_name.go:86` | `pod.spec.nodeName` が指定されていれば、そのノードかどうか |
| **NodeUnschedulable** | `nodeunschedulable/node_unschedulable.go:141` | ノードが `spec.unschedulable`（cordon 状態）でないか。対応する toleration があれば通す |
| **NodePorts** | `nodeports/node_ports.go:156` | Pod が要求する hostPort がノード上で空いているか |
| **NodeAffinity** | `nodeaffinity/node_affinity.go:212` | `nodeSelector` と `requiredDuringScheduling` の nodeAffinity にノードラベルが合っているか |
| **TaintToleration** | `tainttoleration/taint_toleration.go:118` | ノードの NoSchedule / NoExecute taint を Pod が tolerate しているか |
| **NodeResourcesFit** (`Fit`) | `noderesources/fit.go:654` | CPU・メモリ・拡張リソースなどの requests がノードの空き容量に収まるか |
| **NodeDeclaredFeatures** | `nodedeclaredfeatures/nodedeclaredfeatures.go:116` | Pod が必要とする機能をノードが宣言済みの機能セットで満たしているか（feature gate で有効化） |
| **DeferredPodScheduling** | `deferredpodscheduling/deferred_pod_scheduling.go:128` | リサイズが保留中の Pod について、割り当て済みのノードであり、そのノードがリサイズのためのプリエンプションを無効にしていないか |
| **InterPodAffinity** | `interpodaffinity/filtering.go:822` | 必須の Pod affinity / anti-affinity を満たすか。既存 Pod 側の anti-affinity に違反しないかも見る |
| **PodTopologySpread** | `podtopologyspread/filtering.go:318` | `topologySpreadConstraints`（`DoNotSchedule`）の maxSkew を超えないか |
| **VolumeRestrictions** | `volumerestrictions/volume_restrictions.go:322` | ボリュームの競合（同じ GCE PD / EBS / RBD / iSCSI の同時マウント）と ReadWriteOncePod の排他違反がないか |
| **VolumeBinding** | `volumebinding/volume_binding.go:418` | PVC が、このノードで使える PV にバインド済みか、これからバインドまたはプロビジョニングできるか |
| **VolumeZone** | `volumezone/volume_zone.go:202` | PV のゾーン・リージョンのラベルや topology がノードのゾーンと一致するか |
| **NodeVolumeLimits** (`CSILimits`) | `nodevolumelimits/csi.go:262` | ノードに付けられる CSI ボリューム数の上限を超えないか |
| **DynamicResources** | `dynamicresources/dynamicresources.go:936` | DRA の ResourceClaim をこのノードで割り当てられるか（デバイスの空きとセレクタで判定） |

ほかに `framework/runtime/instrumented_plugins.go:35` の `instrumentedFilterPlugin` も `FilterPlugin` を実装していますが、これはメトリクスを取るためのラッパーで、プラグインそのものではありません。

**参考にするなら**
- **最小構成を見たい場合**: `NodeName` か `NodeUnschedulable`。PreFilter を持たず、`Filter` 単体で完結しています。
- **PreFilter で計算した結果を CycleState 経由で Filter に渡す型を見たい場合**: `NodePorts` か `NodeDeclaredFeatures`。小さくて読みやすいです。PreFilter が `fwk.Skip` を返して Filter を飛ばす書き方も `NodeDeclaredFeatures` にあります。
- **Preemption を考慮して `PreFilterExtensions`（AddPod / RemovePod）まで実装する型を見たい場合**: `NodePorts`、`InterPodAffinity`、`PodTopologySpread`。
- **登録の仕方**: `plugins/registry.go` と `plugins/names/names.go`。`EventsToRegister`（QueueingHint）も、ほとんどのプラグインがあわせて実装しています。

## Answer 2

`pkg/scheduler/framework/plugins/` 配下で `fwk.FilterPlugin` を実装している in-tree プラグインは **15 個** です。

| プラグイン | ファイル | Filter で落とす条件 |
|---|---|---|
| **NodeName** | `nodename/node_name.go:86` | `spec.nodeName` が指定されていて、このノード名と違う |
| **NodeUnschedulable** | `nodeunschedulable/node_unschedulable.go:141` | ノードが `spec.unschedulable=true`（cordon 済み）で、Pod がそれを許容する toleration を持っていない |
| **NodeAffinity** | `nodeaffinity/node_affinity.go:212` | `nodeSelector` か required の nodeAffinity にノードのラベルが合わない |
| **TaintToleration** | `tainttoleration/taint_toleration.go:118` | ノードに NoSchedule/NoExecute の taint があり、Pod がそれを許容していない |
| **NodePorts** | `nodeports/node_ports.go:156` | Pod が要求する hostPort がノード上で既に使われている |
| **NodeResourcesFit** (`Fit`) | `noderesources/fit.go:654` | CPU・メモリ・拡張リソースなどの requests がノードの空き容量に収まらない |
| **PodTopologySpread** | `podtopologyspread/filtering.go:318` | `topologySpreadConstraints`（DoNotSchedule）の maxSkew を超える |
| **InterPodAffinity** | `interpodaffinity/filtering.go:822` | required の Pod (anti-)affinity を満たさない（既存 Pod 側の anti-affinity も含む） |
| **VolumeRestrictions** | `volumerestrictions/volume_restrictions.go:322` | ボリュームの競合（GCE PD・EBS・RBD・ISCSI の同時マウント制約、ReadWriteOncePod の PVC が他 Pod で使用中など） |
| **VolumeZone** | `volumezone/volume_zone.go:202` | PV の zone/region ラベルとノードの topology が合わない |
| **VolumeBinding** | `volumebinding/volume_binding.go:418` | PVC がこのノードで bind できない（バインド済み PV の nodeAffinity、未バインド PVC に合う PV や動的プロビジョニングの可否、容量など） |
| **NodeVolumeLimits** (`CSILimits`) | `nodevolumelimits/csi.go:262` | CSI ドライバのノードあたりボリューム数の上限を超える |
| **DynamicResources** | `dynamicresources/dynamicresources.go:936` | DRA の ResourceClaim をこのノードで割り当てられない（デバイスが足りない、セレクタに合わない） |
| **NodeDeclaredFeatures** | `nodedeclaredfeatures/nodedeclaredfeatures.go:116` | Pod が必要とする機能を、ノードが宣言している機能の集合が含んでいない |
| **DeferredPodScheduling** | `deferredpodscheduling/deferred_pod_scheduling.go:128` | リサイズが保留中の Pod について、割り当て先とは別のノードであるか、ノードがリサイズのためのプリエンプションを禁止している（feature gate 有効時のみ動く） |

**補足**
- 15 個とも **PreFilter も実装**しています。PreFilter で Pod 側の情報を前もって計算して `CycleState` に書き、Filter ではノードごとの照合だけを行う形が標準です。
- `DefaultPreemption` は `PostFilterPlugin` で、Filter ではありません。`GangScheduling`・`SchedulingGates`・`TopologyPlacement`・`ImageLocality` なども別の拡張ポイント用です。
- 対象外にしたもの: `runtime/instrumented_plugins.go` はメトリクス計測用のラッパー、`testing/framework/fake_plugins.go` はテスト用のフェイク（True/False/Fake/MatchFilterPlugin）です。

**参考にするなら**
- 最小構成を見たい場合は **NodeName** か **NodeUnschedulable** が短くて読みやすいです。
- PreFilter → CycleState → Filter の流れを見たい場合は **NodePorts** か **NodeDeclaredFeatures** がちょうどいい規模です。
- `EventsToRegister`（どのイベントでキューから戻すか）の書き方は **NodeUnschedulable** と **TaintToleration** が参考になります。

新しいプラグインは `plugins/registry.go` に登録し、`names/names.go` に名前を追加します。

## Answer 3

`pkg/scheduler/framework/plugins/` 以下で `FilterPlugin` を実装している in-tree プラグインは 15 個あります。各プラグインには `var _ fwk.FilterPlugin = &X{}` という宣言と `Filter()` メソッドがあり、この 2 つで確認しました。

| プラグイン | ファイル | Filter でチェックしていること |
|---|---|---|
| **NodeName** | `nodename/node_name.go:86` | Pod の `spec.nodeName` が指定されていれば、そのノードかどうか |
| **NodeUnschedulable** | `nodeunschedulable/node_unschedulable.go:141` | ノードが `spec.unschedulable`（cordon 状態）でないか。Pod がこの taint を toleration していれば許可 |
| **NodeAffinity** | `nodeaffinity/node_affinity.go:212` | Pod の `nodeSelector` と required な nodeAffinity にノードのラベルが合うか |
| **TaintToleration** | `tainttoleration/taint_toleration.go:118` | ノードの `NoSchedule`/`NoExecute` taint を Pod がすべて toleration しているか |
| **NodePorts** | `nodeports/node_ports.go:156` | Pod が要求する hostPort がノード上ですでに使われていないか |
| **NodeResourcesFit** (`Fit`) | `noderesources/fit.go:654` | ノードの空き allocatable（CPU・メモリ・拡張リソース・Pod 数など）に Pod の requests が収まるか |
| **InterPodAffinity** | `interpodaffinity/filtering.go:822` | Pod 間の required な affinity / anti-affinity を満たすか（既存 Pod 側の anti-affinity も含む） |
| **PodTopologySpread** | `podtopologyspread/filtering.go:318` | `whenUnsatisfiable: DoNotSchedule` の topologySpreadConstraints で、maxSkew を超えないか |
| **VolumeRestrictions** | `volumerestrictions/volume_restrictions.go:322` | ボリュームの競合がないか（GCE PD・RBD・ISCSI の同時マウント制約、ReadWriteOncePod の排他など） |
| **VolumeZone** | `volumezone/volume_zone.go:202` | PV の zone/region ラベルがノードのトポロジーと一致するか |
| **VolumeBinding** | `volumebinding/volume_binding.go:418` | PVC がこのノードでバインド済み、またはバインド可能か（PV の nodeAffinity、動的プロビジョニングの可否） |
| **NodeVolumeLimits** (`CSILimits`) | `nodevolumelimits/csi.go:262` | CSI ドライバごとのボリューム数の上限を超えないか |
| **DynamicResources** | `dynamicresources/dynamicresources.go:936` | DRA の ResourceClaim をこのノードで割り当てられるか（デバイスの空き、セレクタ） |
| **NodeDeclaredFeatures** | `nodedeclaredfeatures/nodedeclaredfeatures.go:116` | Pod が必要とするノード機能が、ノードの宣言済み機能に含まれているか |
| **DeferredPodScheduling** | `deferredpodscheduling/deferred_pod_scheduling.go:128` | リサイズが保留中の Pod について、割り当て済みのノードであり、そのノードがリサイズのためのプリエンプションを無効にしていないか（フィーチャーゲートが有効なときだけ動く） |

一覧に入れなかった実装:
- `framework/runtime/instrumented_plugins.go` の `instrumentedFilterPlugin`：メトリクス計測用のラッパーです。
- `testing/framework/fake_plugins.go` の `TrueFilterPlugin`、`FalseFilterPlugin` など：テスト用の fake です。
- `extender.go` の `HTTPExtender.Filter`：外部 extender で、`FilterPlugin` インターフェースとは別物です。

**参考にするときのヒント**
- **一番シンプルな例**：`NodeName` と `NodeUnschedulable`。PreFilter がなく、Filter の中で完結しています。
- **PreFilter で計算して Filter で使う例**：`NodeResourcesFit`、`InterPodAffinity`、`PodTopologySpread`、`NodeDeclaredFeatures`。PreFilter で計算した結果を CycleState に保存し、Filter で読み出す定番のパターンです。
- **プリエンプションに対応する例**：`InterPodAffinity` と `PodTopologySpread`。プリエンプションを正しく効かせたい場合は、この 2 つにある `PreFilterExtensions`（AddPod/RemovePod）の実装が参考になります。
- **新プラグインの登録**：`pkg/scheduler/framework/plugins/registry.go` と `names/names.go` に追加する必要があります。デフォルトで有効にしたい場合は、`apis/config/v1/default_plugins.go` にも追加します。

## Answer 4

`pkg/scheduler/framework/plugins/` 配下には、`fwk.FilterPlugin` を実装した本番用プラグインが **15 個** あります。各ファイルにある `var _ fwk.FilterPlugin = &X{}` という宣言と `Filter()` メソッドを grep で突き合わせて洗い出しました。

| # | プラグイン | 場所 | 何をチェックしているか |
|---|---|---|---|
| 1 | **NodeName** | `nodename/node_name.go:86` | Pod の `spec.nodeName` が指定されていれば、そのノードと一致するか |
| 2 | **NodeUnschedulable** | `nodeunschedulable/node_unschedulable.go:141` | ノードが `spec.unschedulable` (cordon) になっていないか。ただし Pod がそれを許容する toleration を持っていれば通す |
| 3 | **NodePorts** | `nodeports/node_ports.go:156` | Pod が要求する hostPort がノード上で既に使われていないか |
| 4 | **NodeAffinity** | `nodeaffinity/node_affinity.go:212` | ノードが Pod の nodeSelector / required nodeAffinity にマッチするか |
| 5 | **TaintToleration** | `tainttoleration/taint_toleration.go:118` | ノードの NoSchedule / NoExecute taint を、Pod が toleration で許容しているか |
| 6 | **NodeResourcesFit** (`Fit`) | `noderesources/fit.go:654` | ノードの空き CPU・メモリ・拡張リソース・Pod 数が Pod の requests を満たすか |
| 7 | **InterPodAffinity** | `interpodaffinity/filtering.go:822` | Pod 間の required affinity / anti-affinity を満たすか |
| 8 | **PodTopologySpread** | `podtopologyspread/filtering.go:318` | `topologySpreadConstraints` の maxSkew (`DoNotSchedule`) に違反しないか |
| 9 | **VolumeRestrictions** | `volumerestrictions/volume_restrictions.go:322` | ボリュームの競合がないか（同じ GCE PD・EBS・RBD・ISCSI の重複マウントや、ReadWriteOncePod の PVC がすでに使われていないか） |
| 10 | **VolumeBinding** | `volumebinding/volume_binding.go:418` | Pod の PVC がこのノードで bind 済み、または bind 可能か（PV の nodeAffinity や StorageClass のトポロジーを考慮） |
| 11 | **VolumeZone** | `volumezone/volume_zone.go:202` | PV のゾーン・リージョンのラベルがノードのトポロジーと一致するか |
| 12 | **CSILimits** (NodeVolumeLimits) | `nodevolumelimits/csi.go:262` | ノードの CSI ドライバーごとのアタッチ可能ボリューム数の上限を超えないか |
| 13 | **DynamicResources** (DRA) | `dynamicresources/dynamicresources.go:936` | Pod の ResourceClaim を、このノードで使えるデバイスで割り当てられるか |
| 14 | **NodeDeclaredFeatures** | `nodedeclaredfeatures/nodedeclaredfeatures.go:116` | Pod が必要とする機能を、ノードが宣言した機能 (`NodeDeclaredFeatures`) がすべて含んでいるか |
| 15 | **DeferredPodScheduling** | `deferredpodscheduling/deferred_pod_scheduling.go:128` | リサイズが保留 (deferred) されている Pod について、割り当て済みのノードであること、そしてノードが resize preemption を無効にしていないことを確認する（feature gate が有効なときだけ動く） |

対象外にしたもの:
- `pkg/scheduler/framework/runtime/instrumented_plugins.go:35`（`instrumentedFilterPlugin`）は、メトリクスを取るためのラッパーです。
- `pkg/scheduler/testing/framework/fake_plugins.go` の `TrueFilterPlugin` や `FakeFilterPlugin` などはテスト用です。
- `pkg/scheduler/extender.go` の `HTTPExtender.Filter` は Extender の仕組みで、`FilterPlugin` の実装ではありません。

**新しいプラグインを書くときの参考に:**
- **最小構成を見たいなら** `NodeName` か `NodeUnschedulable` が短くて読みやすいです。
- **PreFilter で計算して CycleState に書き込み、Filter で読む流れを見たいなら** `NodePorts`、`NodeDeclaredFeatures`、`InterPodAffinity` が典型例です。
- **登録先:** 書いたプラグインは `pkg/scheduler/framework/plugins/registry.go` に、名前は `names/names.go` に追加します。既定で有効にするなら `pkg/scheduler/apis/config/v1/default_plugins.go` も編集が必要です。この 3 ファイルは慣例から書いており、今回は開いて確認していません。

## Answer 5

`pkg/scheduler` 配下で `fwk.FilterPlugin` を実装しているのは、プラグインとしては **15 個** です。どれも `pkg/scheduler/framework/plugins/` の下にあります。

| プラグイン | 実装場所 | Filter で見ていること |
|---|---|---|
| **NodeName** | `nodename/node_name.go:86` | `spec.nodeName` が指定されていれば、そのノードと一致するか |
| **NodeUnschedulable** | `nodeunschedulable/node_unschedulable.go:141` | ノードが `spec.unschedulable`（cordon 済み）で、Pod がそれを許容していない場合は弾く |
| **TaintToleration** | `tainttoleration/taint_toleration.go:118` | ノードの NoSchedule/NoExecute Taint を Pod の Toleration で許容できるか |
| **NodeAffinity** | `nodeaffinity/node_affinity.go:212` | `nodeSelector` と required な NodeAffinity をノードのラベルが満たすか |
| **NodePorts** | `nodeports/node_ports.go:156` | Pod が要求する hostPort がノード上で空いているか |
| **NodeResourcesFit** (`Fit`) | `noderesources/fit.go:654` | CPU・メモリ・拡張リソースなどの requests がノードの空き容量に収まるか |
| **InterPodAffinity** | `interpodaffinity/filtering.go:822` | 必須の Pod Affinity / Anti-Affinity（既存 Pod との共存・排他）を満たすか |
| **PodTopologySpread** | `podtopologyspread/filtering.go:318` | `DoNotSchedule` のトポロジ分散制約で maxSkew を超えないか |
| **VolumeRestrictions** | `volumerestrictions/volume_restrictions.go:322` | ボリュームの競合（同じディスクの重複マウント、ReadWriteOncePod の排他など）がないか |
| **VolumeBinding** | `volumebinding/volume_binding.go:418` | PVC がこのノードでバインド済みの PV、またはバインド・動的プロビジョニングできる PV を使えるか |
| **VolumeZone** | `volumezone/volume_zone.go:202` | PV のゾーン・リージョンのラベルがノードのトポロジと一致するか |
| **CSILimits** (NodeVolumeLimits) | `nodevolumelimits/csi.go:262` | CSI ドライバごとの、ノードにアタッチできるボリューム数の上限を超えないか |
| **DynamicResources** (DRA) | `dynamicresources/dynamicresources.go:936` | ResourceClaim（GPU などのデバイス）をこのノードで割り当てられるか |
| **NodeDeclaredFeatures** | `nodedeclaredfeatures/nodedeclaredfeatures.go:116` | Pod が必要とする機能を、ノードが宣言している機能がすべて含んでいるか |
| **DeferredPodScheduling** | `deferredpodscheduling/deferred_pod_scheduling.go:128` | リサイズが保留中の Pod について、割り当て済みのノードであり、そのノードがリサイズのためのプリエンプションを禁止していないか（フィーチャーゲートが有効なときだけ動く） |

もう 1 つ、`runtime/instrumented_plugins.go:35` の `instrumentedFilterPlugin` も実装していますが、これはメトリクス計測用に他のプラグインを包むラッパーなので、参考にする対象からは外しています。

**参考にするならこの 3 つです：**
- **`nodename`** と **`nodeunschedulable`**：最小構成の Filter で、PreFilter がありません。全体の骨組みを掴むのに向いています。
- **`nodeports`**：PreFilter で計算した結果を `CycleState` に保存し、Filter で読み出すという典型的な流れになっています。`EventsToRegister`（QueueingHint）の書き方も含めて参考になります。

各ファイルの冒頭にある `var _ fwk.FilterPlugin = &X{}` は、インターフェースを実装しているかをコンパイル時に確認するための宣言です。新しいプラグインでも同じように書くと安全です。作ったプラグインは `plugins/registry.go` に登録する必要があります。

## Answer 6

`pkg/scheduler/framework/plugins/` の中で `fwk.FilterPlugin` を実装している in-tree プラグインは15個ありました。

| プラグイン | ファイル | Filter でチェックしていること |
|---|---|---|
| **NodeName** | `nodename/node_name.go:86` | `pod.spec.nodeName` が指定されていれば、そのノード名と一致するか |
| **NodeUnschedulable** | `nodeunschedulable/node_unschedulable.go:141` | ノードが `spec.unschedulable`（cordon 状態）になっていないか。Pod 側に対応する toleration があれば許可 |
| **NodeAffinity** | `nodeaffinity/node_affinity.go:212` | `nodeSelector` と required な nodeAffinity にノードのラベルが合うか |
| **TaintToleration** | `tainttoleration/taint_toleration.go:118` | ノードの NoSchedule / NoExecute taint をすべて Pod が tolerate しているか |
| **NodePorts** | `nodeports/node_ports.go:156` | Pod が使う hostPort が、そのノードで既に使われていないか |
| **NodeResourcesFit** (`Fit`) | `noderesources/fit.go:654` | CPU・メモリ・拡張リソースなどの requests がノードの空き容量に収まるか |
| **InterPodAffinity** | `interpodaffinity/filtering.go:822` | Pod 間の required affinity / anti-affinity を満たすか（既存 Pod 側の anti-affinity も含む） |
| **PodTopologySpread** | `podtopologyspread/filtering.go:318` | `DoNotSchedule` の topologySpreadConstraints で maxSkew を超えないか |
| **VolumeRestrictions** | `volumerestrictions/volume_restrictions.go:322` | ボリュームの競合がないか（同じディスクの重複マウントや ReadWriteOncePod 違反など） |
| **VolumeZone** | `volumezone/volume_zone.go:202` | PV のゾーン/リージョンのラベルがノードのものと合うか |
| **VolumeBinding** | `volumebinding/volume_binding.go:418` | PVC がこのノードでバインド済み、または新たにバインドできるか（PV の nodeAffinity、WaitForFirstConsumer、ストレージ容量） |
| **CSILimits** (NodeVolumeLimits) | `nodevolumelimits/csi.go:262` | ノードにアタッチされる CSI ボリューム数が、ドライバの上限を超えないか |
| **DynamicResources** | `dynamicresources/dynamicresources.go:936` | DRA の ResourceClaim をこのノードのデバイスで割り当てられるか |
| **NodeDeclaredFeatures** | `nodedeclaredfeatures/nodedeclaredfeatures.go:116` | Pod が必要とする機能が、ノードの宣言している機能に含まれているか |
| **DeferredPodScheduling** | `deferredpodscheduling/deferred_pod_scheduling.go:128` | リサイズが保留中（deferred）の Pod について、割り当て済みノードと一致するか、そのノードがリサイズ用のプリエンプションを無効にしていないか（feature gate が有効なときだけ動作） |

**一覧から外したもの:**
- `framework/runtime/instrumented_plugins.go` は、メトリクスを取るために Filter をラップしているだけです。
- `testing/framework/fake_plugins.go` はテスト用のフェイクです（`TrueFilterPlugin`、`FalseFilterPlugin`、`FakeFilterPlugin`、`MatchFilterPlugin`）。
- `extender.go` の `HTTPExtender.Filter` は Extender の仕組みで、FilterPlugin インターフェースは実装していません。

**参考にするときのポイント:**
- 15個すべてが `PreFilterPlugin` も一緒に実装しています。よくある形は「PreFilter で Pod から必要な情報を計算して `CycleState` に書き込み、Filter で読み出してノードごとに判定する」というものです。
- 実装を読むなら、ノードの情報だけで判定する **NodeUnschedulable** と **NodeName** がいちばん短いです。CycleState の使い方の例としては **NodeDeclaredFeatures** か **NodePorts** がちょうどいい規模です。
- 新しいプラグインを作ったら `framework/plugins/registry.go` に登録します。デフォルトで有効にしたい場合は、デフォルトのプラグイン設定にも追加が必要です。

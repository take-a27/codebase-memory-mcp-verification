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

本番用の Filter プラグインは全部で **15 個**で、すべて `pkg/scheduler/framework/plugins/` の下にあります。[tool]で `Filter` メソッドを抜き出した結果と、grep で `var _ fwk.FilterPlugin = ...` を探した結果は、どちらも同じ 15 個でした。

| プラグイン | ファイル | 何をチェックしているか |
|---|---|---|
| **NodeName** | `nodename/node_name.go:86` | Pod の `spec.nodeName` がこのノードの名前と一致するか |
| **NodeUnschedulable** | `nodeunschedulable/node_unschedulable.go:141` | ノードが `spec.unschedulable`（cordon された状態）ではないか。ただし、それを許す toleration を Pod が持っていれば通す |
| **NodeAffinity** | `nodeaffinity/node_affinity.go:212` | Pod の `nodeSelector` と必須の nodeAffinity にノードのラベルが合うか |
| **TaintToleration** | `tainttoleration/taint_toleration.go:118` | ノードの NoSchedule / NoExecute taint をすべて Pod が tolerate しているか |
| **NodePorts** | `nodeports/node_ports.go:156` | Pod が要求する hostPort が、ノード上ですでに使われていないか |
| **NodeResourcesFit** (`Fit`) | `noderesources/fit.go:654` | CPU・メモリ・拡張リソースなど、Pod の requests がノードの空き容量に収まるか |
| **InterPodAffinity** | `interpodaffinity/filtering.go:822` | 必須の Pod 間 affinity / anti-affinity を満たすか（既存 Pod の anti-affinity も含む） |
| **PodTopologySpread** | `podtopologyspread/filtering.go:318` | `topologySpreadConstraints` の maxSkew（偏りの上限）を超えないか |
| **VolumeBinding** | `volumebinding/volume_binding.go:418` | Pod の PVC が、このノードで使える PV にバインド済みか、これからバインドできるか |
| **VolumeRestrictions** | `volumerestrictions/volume_restrictions.go:322` | 同時に使えないボリュームの組み合わせがないか（ReadWriteOncePod の重複など） |
| **VolumeZone** | `volumezone/volume_zone.go:202` | PV のゾーン・リージョンのラベルがノードのトポロジーと一致するか |
| **NodeVolumeLimits** (`CSILimits`) | `nodevolumelimits/csi.go:262` | CSI ドライバーごとに、ノードにアタッチできるボリューム数の上限を超えないか |
| **DynamicResources** | `dynamicresources/dynamicresources.go:936` | DRA の ResourceClaim が要求するデバイスを、このノードで割り当てられるか |
| **NodeDeclaredFeatures** | `nodedeclaredfeatures/nodedeclaredfeatures.go:116` | Pod に必要な機能を、ノードがすべて宣言しているか（ノードが申告する機能一覧と照合） |
| **DeferredPodScheduling** | `deferredpodscheduling/deferred_pod_scheduling.go:128` | リサイズが保留中の Pod について、割り当て済みノードと同じノードか、そのノードがリサイズ用のプリエンプションを禁止していないか。フィーチャーゲートが有効なときだけ動く |

**一覧から外したもの:**
- `pkg/scheduler/testing/framework/fake_plugins.go` にあるテスト用のプラグイン（`TrueFilterPlugin`、`FalseFilterPlugin`、`FakeFilterPlugin`、`MatchFilterPlugin`）
- `pkg/scheduler/framework/runtime/instrumented_plugins.go` の `instrumentedFilterPlugin`。これはメトリクスを取るためのラッパーです
- `pkg/scheduler/extender.go` の `HTTPExtender.Filter`。名前は同じですが Extender 用の仕組みで、`FilterPlugin` の実装ではありません

**新しく作るときに参考にしやすいもの:**
- 一番シンプルな形を見るなら **NodeName** か **NodeUnschedulable** です。ほぼ Filter だけで完結しています。
- PreFilter で計算した結果を `CycleState` に保存し、Filter で読み出す形を見るなら **NodePorts** か **NodeDeclaredFeatures** です。
- 15 個すべてが `PreFilterPlugin` も実装しているので、PreFilter と組にするのが標準の形です。また、Pod を再スケジュールのキューに戻すタイミングを決める `EventsToRegister`（`EnqueueExtensions`）も実装しておくのが一般的です。
- 作ったプラグインは `pkg/scheduler/framework/plugins/registry.go` への登録が必要です。デフォルトで有効にしたい場合は、`pkg/scheduler/apis/config` 配下にあるデフォルトプラグインの設定にも追加します。

一言説明のうち、DeferredPodScheduling と NodeDeclaredFeatures はコードを読んで確認しました。ほかの 13 個は、定番プラグインについての既存の知識で書いたもので、このバージョンのコードでは読み直していません。

## Answer 4

既存の Filter プラグイン（テスト用を除く）は **15 個**で、すべて `pkg/scheduler/framework/plugins/` の下にあります。

| プラグイン（構造体） | 場所 | 何をチェックしているか |
|---|---|---|
| **NodeName** | `nodename/node_name.go:86` | Pod の `spec.nodeName` が指定されていれば、そのノードと名前が一致するか |
| **NodeUnschedulable** | `nodeunschedulable/node_unschedulable.go:141` | ノードが `spec.unschedulable`（cordon 済み）でないか。Pod がその taint を許容していれば通す |
| **NodeAffinity** | `nodeaffinity/node_affinity.go:212` | Pod の `nodeSelector` と required な nodeAffinity にノードのラベルが合うか |
| **TaintToleration** | `tainttoleration/taint_toleration.go:118` | ノードの NoSchedule / NoExecute taint を Pod がすべて許容しているか |
| **NodePorts** | `nodeports/node_ports.go:156` | Pod が使う hostPort がノード上の既存 Pod と衝突しないか |
| **Fit**（NodeResources） | `noderesources/fit.go:654` | ノードの空き CPU・メモリ・拡張リソース・Pod 数が Pod の requests に足りるか |
| **InterPodAffinity** | `interpodaffinity/filtering.go:822` | Pod 間の required な affinity / anti-affinity を満たすか（既存 Pod 側の anti-affinity も含む） |
| **PodTopologySpread** | `podtopologyspread/filtering.go:318` | `DoNotSchedule` の topologySpreadConstraints で maxSkew を超えないか |
| **VolumeBinding** | `volumebinding/volume_binding.go:418` | Pod の PVC が、このノードから使える PV にバインド済みか、バインドできるか（PV の nodeAffinity など） |
| **VolumeRestrictions** | `volumerestrictions/volume_restrictions.go:322` | ボリュームの衝突（同じディスクの重複マウントなど）がないか、ReadWriteOncePod の PVC が他の Pod に使われていないか |
| **VolumeZone** | `volumezone/volume_zone.go:202` | PV のゾーン／リージョンのラベルがノードのものと合うか |
| **CSILimits**（NodeVolumeLimits） | `nodevolumelimits/csi.go:262` | ノードにアタッチできる CSI ボリューム数の上限を超えないか |
| **DynamicResources** | `dynamicresources/dynamicresources.go:936` | Pod の ResourceClaim（DRA）をこのノードのデバイスで割り当てられるか |
| **NodeDeclaredFeatures** | `nodedeclaredfeatures/nodedeclaredfeatures.go:116` | Pod が必要とする機能を、ノードが宣言している機能がすべて含んでいるか |
| **DeferredPodScheduling** | `deferredpodscheduling/deferred_pod_scheduling.go:128` | 保留中の in-place リサイズを抱えた Pod について、割り当て先のノードであり、そのノードがリサイズ時のプリエンプションを禁止していないか（feature gate が有効なときだけ動く） |

**調べ方:** コードの[tool]で `Filter` メソッドを列挙し、grep でも同じ 15 件が出ることを確かめました。最後の新しい 2 つはコードを読んで確認し、ほかの 13 個の説明はよく知られた挙動に基づいています。

**一覧から外したもの:**
- `pkg/scheduler/testing/framework/fake_plugins.go` のテスト用プラグイン（TrueFilterPlugin / FalseFilterPlugin / FakeFilterPlugin / MatchFilterPlugin）
- `runtime/instrumented_plugins.go` のメトリクス用ラッパー
- `pkg/scheduler/extender.go` の HTTPExtender（Filter という名前のメソッドはあるが、`FilterPlugin` とはシグネチャが違う）

**参考にするなら:**
- **シンプルな形:** `NodeName` か `NodeUnschedulable`。状態を持たずに、ノードと Pod だけで判定しています。
- **PreFilter で計算して CycleState に書き、Filter で読む形:** `NodePorts` か `NodeDeclaredFeatures`。
- 追加したプラグインは `pkg/scheduler/framework/plugins/registry.go` に登録する必要があります。

## Answer 5

`pkg/scheduler/framework/plugins/` の中で、本番用の `FilterPlugin` 実装は **15個** ありました。[tool]での検索と grep の両方で調べて、同じ15個が出ています。

| # | プラグイン（構造体） | ファイル:行 | 何をチェックしているか |
|---|---|---|---|
| 1 | `NodeName` | `pkg/scheduler/framework/plugins/nodename/node_name.go:86` | Pod の `spec.nodeName` が指定されている場合、そのノード名と一致するか |
| 2 | `NodeUnschedulable` | `pkg/scheduler/framework/plugins/nodeunschedulable/node_unschedulable.go:141` | ノードが `spec.unschedulable`（cordon された状態）かどうか。Pod に対応する toleration があれば通す |
| 3 | `NodeAffinity` | `pkg/scheduler/framework/plugins/nodeaffinity/node_affinity.go:212` | Pod の nodeSelector や required の nodeAffinity にノードのラベルが合っているか |
| 4 | `TaintToleration` | `pkg/scheduler/framework/plugins/tainttoleration/taint_toleration.go:118` | ノードの NoSchedule / NoExecute の taint を、Pod がすべて toleration で許容しているか |
| 5 | `NodePorts` | `pkg/scheduler/framework/plugins/nodeports/node_ports.go:156` | Pod が要求する hostPort がノード上で空いているか |
| 6 | `Fit`（NodeResourcesFit） | `pkg/scheduler/framework/plugins/noderesources/fit.go:654` | CPU・メモリ・拡張リソースなどの要求がノードの空き容量に収まるか |
| 7 | `InterPodAffinity` | `pkg/scheduler/framework/plugins/interpodaffinity/filtering.go:822` | Pod 間の affinity / anti-affinity（required）を満たすか。既存 Pod の anti-affinity も含む |
| 8 | `PodTopologySpread` | `pkg/scheduler/framework/plugins/podtopologyspread/filtering.go:318` | topologySpreadConstraints（DoNotSchedule）の maxSkew を超えないか |
| 9 | `VolumeBinding` | `pkg/scheduler/framework/plugins/volumebinding/volume_binding.go:418` | Pod の PVC が、このノードで既存の PV にバインドできるか、または動的プロビジョニングできるか |
| 10 | `VolumeZone` | `pkg/scheduler/framework/plugins/volumezone/volume_zone.go:202` | 使う PV のゾーン・リージョンのラベルがノードのものと合っているか |
| 11 | `VolumeRestrictions` | `pkg/scheduler/framework/plugins/volumerestrictions/volume_restrictions.go:322` | ボリュームの競合がないか（同じディスクの重複マウント禁止や ReadWriteOncePod の排他など） |
| 12 | `CSILimits`（NodeVolumeLimits） | `pkg/scheduler/framework/plugins/nodevolumelimits/csi.go:262` | CSI ドライバごとの、ノードにアタッチできるボリューム数の上限を超えないか |
| 13 | `DynamicResources` | `pkg/scheduler/framework/plugins/dynamicresources/dynamicresources.go:936` | Pod の ResourceClaim（DRA）を、このノードで使えるデバイスで割り当てられるか |
| 14 | `NodeDeclaredFeatures` | `pkg/scheduler/framework/plugins/nodedeclaredfeatures/nodedeclaredfeatures.go:116` | Pod が必要とする機能が、ノードが申告している機能の中にすべて含まれるか |
| 15 | `DeferredPodScheduling` | `pkg/scheduler/framework/plugins/deferredpodscheduling/deferred_pod_scheduling.go:128` | リサイズが保留中の Pod について、割り当て先のノードか、そのノードがリサイズのための preemption を禁止していないか（フィーチャーゲートが有効なときだけ動く） |

`pkg/scheduler/framework/runtime/instrumented_plugins.go` にも `Filter` を持つ型がありますが、これはメトリクスを取るためのラッパーで、自分では何もチェックしないので表には入れていません。テスト用の fake プラグインも外しています。

**新しく作るときの参考に:**
- **一番シンプルな例:** `NodeName` と `NodeUnschedulable`。PreFilter がなく、`Filter` だけで完結しています。
- **PreFilter と組み合わせる例:** `NodeDeclaredFeatures` や `NodePorts`。PreFilter で Pod 側の要求を計算して `CycleState` に保存し（Pod 側に要求がなければ `Skip` を返す）、`Filter` ではそれを読み出してノードと比べるだけ、という形です。
- **失敗時のステータスの使い分け:** preemption で解決できない場合は `UnschedulableAndUnresolvable` を、他の Pod を退かせば入る可能性がある場合は `Unschedulable` を返します。
- **登録先:** 作ったプラグインは `pkg/scheduler/framework/plugins/registry.go` に登録し、必要ならデフォルトのプロファイルにも追加します。

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

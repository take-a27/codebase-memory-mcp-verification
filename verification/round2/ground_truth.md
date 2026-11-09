# 正解（kubernetes v1.37.2 から機械的に抽出）

## T4: `pkg/api/v1/pod.IsPodAvailable` の呼び出し元

- pkg/controller/daemon/daemon_controller.go:951 — `func (dsc *DaemonSetsController) podsShouldBeOnNode(`
- pkg/controller/daemon/daemon_controller.go:1237 — `func (dsc *DaemonSetsController) updateDaemonSetStatus(ctx context.Context, ds *apps.DaemonSet, nodeList []*v1`
- pkg/controller/daemon/update.go:88 — `func (dsc *DaemonSetsController) rollingUpdate(ctx context.Context, ds *apps.DaemonSet, nodeList []*v1.Node, h`
- pkg/controller/daemon/update.go:95 — `func (dsc *DaemonSetsController) rollingUpdate(ctx context.Context, ds *apps.DaemonSet, nodeList []*v1.Node, h`
- pkg/controller/daemon/update.go:167 — `func (dsc *DaemonSetsController) rollingUpdate(ctx context.Context, ds *apps.DaemonSet, nodeList []*v1.Node, h`
- pkg/controller/daemon/update.go:171 — `func (dsc *DaemonSetsController) rollingUpdate(ctx context.Context, ds *apps.DaemonSet, nodeList []*v1.Node, h`
- pkg/controller/daemon/update.go:182 — `func (dsc *DaemonSetsController) rollingUpdate(ctx context.Context, ds *apps.DaemonSet, nodeList []*v1.Node, h`
- pkg/controller/daemon/update.go:221 — `func (dsc *DaemonSetsController) rollingUpdate(ctx context.Context, ds *apps.DaemonSet, nodeList []*v1.Node, h`
- pkg/controller/replicaset/replica_set_utils.go:113 — `func calculateStatus(rs *apps.ReplicaSet, activePods []*v1.Pod, terminatingPods []*v1.Pod, manageReplicasErr e`
- pkg/controller/statefulset/stateful_set_utils.go:472 — `func isRunningAndAvailable(pod *v1.Pod, minReadySeconds int32, now time.Time) bool {`
- pkg/controller/statefulset/stateful_set_utils.go:497 — `func isUnavailable(pod *v1.Pod, minReadySeconds int32, now time.Time) bool {`
- test/e2e/apps/daemon_set.go:644 — `func updateDaemonSetWithRetries(ctx context.Context, c clientset.Interface, namespace, name string, applyUpdat`
- test/e2e/apps/daemon_set.go:1202 — `func checkDaemonPodsImageAndAvailability(c clientset.Interface, ds *appsv1.DaemonSet, image string, maxUnavail`
- test/e2e/network/loadbalancer.go:1432 — `func testRollingUpdateLBConnectivityDisruption(ctx context.Context, f *framework.Framework, externalTrafficPol`
- test/utils/deployment.go:65 — `func LogPodsOfDeployment(c clientset.Interface, deployment *apps.Deployment, rsList []*apps.ReplicaSet, logf L`

2 段目（statefulset のラッパー関数の呼び出し元）

- isRunningAndAvailable: pkg/controller/statefulset/stateful_set_control.go:385
- isRunningAndAvailable: pkg/controller/statefulset/stateful_set_control.go:485
- isRunningAndAvailable: pkg/controller/statefulset/stateful_set_control.go:529
- isRunningAndAvailable: pkg/controller/statefulset/stateful_set_control.go:885
- isUnavailable: pkg/controller/statefulset/stateful_set_control.go:634
- isUnavailable: pkg/controller/statefulset/stateful_set_control.go:644
- isUnavailable: pkg/controller/statefulset/stateful_set_control.go:762
- isUnavailable: pkg/controller/statefulset/stateful_set_control.go:850
- isUnavailable: pkg/controller/statefulset/stateful_set_control.go:902
- isUnavailable: pkg/controller/statefulset/stateful_set_utils.go:495

## T5: `pkg/controller` 配下の exported なトップレベル関数で、本番コードから参照されていないもの

分類: unused = 定義ファイル内でも本番コードでも使われていない / same-file = 定義ファイル内でだけ使われている /
test-only = テストからだけ参照 / test-helper = テスト用パッケージ（testing/, testutil/, test/, fake_*）の関数

| 関数 | 定義 | 分類 | 定義ファイル内の使用（定義・コメント除く） | テストからの参照ファイル数 |
|---|---|---|---|---|
| `IsPodTerminating` | pkg/controller/controller_utils.go | same-file | 2 | 0 |
| `PodControllerIndexKey` | pkg/controller/controller_utils.go | same-file | 3 | 0 |
| `MustParseCIDR` | pkg/controller/nodeipam/ipam/test/utils.go | test-helper | 0 | 2 |
| `FakeNodeInformer` | pkg/controller/nodeipam/ipam/test/utils.go | test-helper | 0 | 1 |
| `WaitForUpdatedNodeWithTimeout` | pkg/controller/nodeipam/ipam/test/utils.go | test-helper | 0 | 1 |
| `IsValidMode` | pkg/controller/nodeipam/ipam/sync/sync.go | unused | 0 | 0 |
| `NewDOTVertex` | pkg/controller/garbagecollector/dump.go | same-file | 1 | 1 |
| `NewMissingdotVertex` | pkg/controller/garbagecollector/dump.go | same-file | 1 | 0 |
| `NewDebugHandler` | pkg/controller/garbagecollector/dump.go | same-file | 1 | 0 |
| `GetDeletableResources` | pkg/controller/garbagecollector/garbagecollector.go | same-file | 1 | 1 |
| `SetPodTerminationReason` | pkg/controller/util/node/controller_utils.go | same-file | 1 | 0 |
| `MakeVolumeAttributesClass` | pkg/controller/util/protectionutil/wrappers.go | test-only | 0 | 1 |
| `NewStatefulPodControlFromManager` | pkg/controller/statefulset/stateful_pod_control.go | test-only | 0 | 2 |
| `NewCSRSigningController` | pkg/controller/certificates/signer/signer.go | same-file | 4 | 0 |
| `NewPodGCInternal` | pkg/controller/podgc/gc_controller.go | same-file | 1 | 2 |
| `GetQuotableResources` | pkg/controller/resourcequota/resource_quota_controller.go | same-file | 2 | 1 |
| `SetTestMode` | pkg/controller/resourceclaim/metrics/metrics.go | test-only | 0 | 1 |
| `ControllerRevisionControllerIndexKey` | pkg/controller/history/controller_history.go | same-file | 3 | 0 |
| `ControllerRevisionName` | pkg/controller/history/controller_history.go | same-file | 3 | 1 |
| `HashControllerRevision` | pkg/controller/history/controller_history.go | same-file | 3 | 1 |
| `NewFakeHistory` | pkg/controller/history/controller_history.go | test-only | 0 | 3 |
| `DefaultReplicaSetControllerFeatures` | pkg/controller/replicaset/replica_set.go | same-file | 1 | 2 |
| `NewReplicaSetCondition` | pkg/controller/replicaset/replica_set_utils.go | same-file | 1 | 0 |
| `NewDisruptionControllerInternal` | pkg/controller/disruption/disruption.go | same-file | 1 | 2 |
| `NewFakeNodeStatusUpdater` | pkg/controller/volume/attachdetach/statusupdater/fake_node_status_updater.go | test-helper | 0 | 1 |
| `CreateTestPlugin` | pkg/controller/volume/attachdetach/testing/plugin.go | test-helper | 0 | 1 |
| `GetTestVolumeSpec` | pkg/controller/volume/attachdetach/testing/testvolumespec.go | test-helper | 0 | 5 |
| `CreateTestClient` | pkg/controller/volume/attachdetach/testing/testvolumespec.go | test-helper | 0 | 3 |
| `NewPodWithVolume` | pkg/controller/volume/attachdetach/testing/testvolumespec.go | test-helper | 0 | 1 |
| `NewPV` | pkg/controller/volume/attachdetach/testing/testvolumespec.go | test-helper | 0 | 1 |
| `NewNFSPV` | pkg/controller/volume/attachdetach/testing/testvolumespec.go | test-helper | 0 | 1 |
| `RecordVolumeOperationErrorMetric` | pkg/controller/volume/persistentvolume/metrics/metrics.go | same-file | 1 | 1 |
| `NewPersistentVolumeControllerOptions` | pkg/controller/volume/persistentvolume/options/options.go | test-only | 0 | 1 |
| `RecommendedDefaultVolumeConfiguration` | pkg/controller/volume/persistentvolume/config/v1alpha1/defaults.go | same-file | 1 | 0 |
| `RecommendedDefaultPersistentVolumeRecyclerConfiguration` | pkg/controller/volume/persistentvolume/config/v1alpha1/defaults.go | same-file | 1 | 0 |
| `NewVolumeReactor` | pkg/controller/volume/persistentvolume/testing/testing.go | test-helper | 0 | 2 |
| `NewPVCProcessingStore` | pkg/controller/volume/pvcprotection/pvc_protection_controller.go | same-file | 1 | 0 |
| `NewNode` | pkg/controller/testutil/test_utils.go | test-helper | 1 | 2 |
| `GetZones` | pkg/controller/testutil/test_utils.go | test-helper | 0 | 1 |
| `CreateZoneID` | pkg/controller/testutil/test_utils.go | test-helper | 0 | 1 |
| `NewFixedItemIntervalRateLimiter` | pkg/controller/podautoscaler/rate_limiters.go | same-file | 1 | 0 |

## T6: `FilterPlugin` の実装（`Filter(ctx context.Context, ...)` メソッドを持つ型）

- pkg/scheduler/framework/plugins/deferredpodscheduling/deferred_pod_scheduling.go:128 — `*DeferredPodScheduling`
- pkg/scheduler/framework/plugins/dynamicresources/dynamicresources.go:936 — `*DynamicResources`
- pkg/scheduler/framework/plugins/interpodaffinity/filtering.go:822 — `*InterPodAffinity`
- pkg/scheduler/framework/plugins/nodeaffinity/node_affinity.go:212 — `*NodeAffinity`
- pkg/scheduler/framework/plugins/nodedeclaredfeatures/nodedeclaredfeatures.go:116 — `*NodeDeclaredFeatures`
- pkg/scheduler/framework/plugins/nodename/node_name.go:86 — `*NodeName`
- pkg/scheduler/framework/plugins/nodeports/node_ports.go:156 — `*NodePorts`
- pkg/scheduler/framework/plugins/noderesources/fit.go:654 — `*Fit`
- pkg/scheduler/framework/plugins/nodeunschedulable/node_unschedulable.go:141 — `*NodeUnschedulable`
- pkg/scheduler/framework/plugins/nodevolumelimits/csi.go:262 — `*CSILimits`
- pkg/scheduler/framework/plugins/podtopologyspread/filtering.go:318 — `*PodTopologySpread`
- pkg/scheduler/framework/plugins/tainttoleration/taint_toleration.go:118 — `*TaintToleration`
- pkg/scheduler/framework/plugins/volumebinding/volume_binding.go:418 — `*VolumeBinding`
- pkg/scheduler/framework/plugins/volumerestrictions/volume_restrictions.go:322 — `*VolumeRestrictions`
- pkg/scheduler/framework/plugins/volumezone/volume_zone.go:202 — `*VolumeZone`
- pkg/scheduler/framework/runtime/instrumented_plugins.go:35 — `*instrumentedFilterPlugin`


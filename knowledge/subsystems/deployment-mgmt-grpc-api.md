---
type: api
protocol: grpc
endpoint: :50051 com.e5.platform.ss.proto.DeploymentManagementService
owner: deployment-mgmt
schema_dir: src/main/proto/deployment_management.proto
proto_services_declared: 3
proto_services_served: 1
rpc_count_declared: 93
rpc_count_implemented: 91
rpc_count_unimplemented: 3
table_count: 44
provenance: code
verified: 2026-10-02
---

# deployment-mgmt gRPC API — full catalogue

Exposed by [deployment-mgmt](deployment-mgmt.md) at `release/v1.5.0` (`75bbdb3`).
Single proto file, **1466 lines**, `package com.e5.platform.ss.proto`,
`java_package com.e5.management.proto` (`deployment_management.proto:8-12`).

## Counts — exact

| Thing | Count | Evidence |
|---|---|---|
| `service` blocks declared | **3** | `MonitoringService` `:263`, `PlatformInterface` `:266`, `DeploymentManagementService` `:1344` |
| `service` blocks actually served | **1** | `Main.java:39-45` registers only `DeploymentManagementServiceImpl` (+ reflection + health) |
| `rpc` lines in file | **94** | 93 on `DeploymentManagementService`, 1 on `PlatformInterface`, **0 on `MonitoringService`** |
| rpcs on `DeploymentManagementService` | **93** | `:1345-1465` |
| …with an `@Override` impl | **91** | `DeploymentManagementServiceImpl.java` |
| …returning UNIMPLEMENTED | **3** | `GetPodEndpoints` (`:111-113` calls `super`), `ChangeWorkerStatus`, `MigrateAppWorker` (no impl) |
| `message` types | 228 | — |
| physical tables owned | **44** | 38 `@Entity` + 3 join tables + 3 migration-only |
| Flyway migration files | **40** (39 `.sql` + readme) | `src/main/resources/db/migration/V1/**` |

**Caller legend** — IF = [interface](interface.md) `v1.6.8` ·
WM = workflow-manager `main@8b6c121` · **WM† = workflow-manager at the newer
`release/v1.2.1` (`7bb3693`) checkout only** · MON = monitoring-service `v1.4.4` ·
EP = event-processor `v1.2.0` · CM = [credential-monitor](credential-monitor.md) `v1.0.1` ·
WOPS = workflow-ops `v1.6.3` (Python) · PIPE = devops-pipeline `v4.0.8` (Python) ·
— = **no call site at any pinned commit**.

Callers are derived from **call sites**, not from proto presence. Three things make the naive
reading wrong:
1. Every consumer carries its own **pruned fork** of this proto (rpc counts 8, 16, 18, 18, 26,
   34, 58, 58, 59, 64, 64, 76, 94, 116, 122 across 15 files), so the proto over-reports coupling.
2. A proto *getter* on a response message (`response.getWorkflowInfo()`) looks identical to an
   rpc call under a naive grep. Every entry below was confirmed against a stub variable.
3. **The workspace contains two checkouts of several consumer repos at different commits.**
   `../../registry.yaml` pins one each; the counts above use the pinned one and mark the other
   `†`. See
   workspace-has-duplicate-repo-checkouts-at-different-commits (archived)
   and
   dms-proto-forks-disagree-on-service-identity (archived).

---

## 1. Workflow runs — 12 rpcs → `workflow_run`, `temporal_workflow`

| rpc | request → response | Tables | Callers |
|---|---|---|---|
| `getYetToStartWorkflows` `:1345` | `Empty` → `WorkflowRuns` | `workflow_run` r | MON |
| `getWFRunListForDeploymentID` `:1346` | `DeploymentVersionId` → `WorkflowRuns` | `workflow_run` r | MON, CM |
| `getWFRunRecordWithPayload` `:1347` | `WorkflowRunId` → `WorkflowRunWithPayload` | `workflow_run` r | MON, CM |
| `GetWorkflowRun` `:1348` | `WorkflowRunId` → `WorkflowRun` | `workflow_run` r | WM† |
| `ValidateWorkflowRun` `:1349` | `ValidateWorkflowRunRequest` → `ValidateWorkflowRunResponse` | `workflow_run` r | IF |
| `ListWorkflowRuns` `:1350` | `ListWorkflowRunsRequest` → `ListWorkflowRunsResponse` | `workflow_run` r (native SQL, `WorkflowRunRepositoryImpl.java:390,441`) | IF |
| `updateWfRunStatus` `:1352` | `UpdateWorkflowRunStatus` → `WorkflowRun` | `workflow_run` **w** | MON, CM |
| `updateWfRunEventStatus` `:1357` | `UpdateWorkflowRunEventStatus` → `WorkflowRun` | `workflow_run` **w** | MON, CM |
| `getDeploymentId` `:1356` | `WorkflowInformation` → `DeploymentVersionId` | `workflow_run` r | MON, CM, WOPS |
| `GetDeploymentVersionIdByCriteria` `:1464` | `GetDeploymentVersionIdByCriteriaRequest` → `DeploymentVersionId` | `workflow_run` r | IF, WOPS |
| `createTemporalWf` `:1353` | `TemporalWFStatusRequest` → `TemporalWFRun` | `temporal_workflow` **w**, `workflow_run` r | MON |
| `updateTemporalWfStatus` `:1354` | `UpdateTemporalWFStatusRequest` → `TemporalWFRun` | `temporal_workflow` **w** | MON |
| `GetAllTemporalWFRecords` `:1449` | `WorkflowRunId` → `GetAllTemporalWFRecordsResponse` | `temporal_workflow` r | WM† |

## 2. Deployment topology & config — 14 rpcs

| rpc | request → response | Tables | Callers |
|---|---|---|---|
| `getDeployment` `:1351` | `DeploymentVersionId` → `DeploymentRecord` | `workflow_run` r | MON, CM, WM† |
| `GetConfig` `:1362` | `GetConfigRequest` → `GetConfigResponse` | `workflow_run` r | — |
| `GetConfigValue` `:1363` | `GetDeploymentConfigValueRequest` → `GetDeploymentConfigResponse` | `deployment_state` + `deployment_version` r — ⚠ **hardcoded `dev.` schema** | MON, CM, WOPS, WM† |
| `GetDeploymentStateByKey` `:1439` | `GetDeploymentStateByKeyRequest` → `GetDeploymentStateByKeyResponse` | `deployment_state` r | WM |
| `GetRuntimeConfig` `:1368` | `GetRuntimeConfigRequest` → `GetRuntimeConfigResponse` | `deployment_state`, `schedule` r | WOPS |
| `GetKafkaDetails` `:1358` | `DeploymentVersionId` → `KafkaDetailsResponse` | `deployment_state` r | MON, CM |
| `getEventsTopic` `:1355` | `Empty` → `TopicList` | `deployment_state` r | MON, CM |
| `GetPodEndpoints` `:1359` | `DeploymentVersionId` → `PodEndpointsResponse` | **none — UNIMPLEMENTED** | — |
| `SaveDeploymentDetails` `:1365` | `DeploymentDetailsRequest` → `DeploymentDetailsResponse` | `deployment`, `deployment_version`, `deployment_config`, `deployment_state`, `schedule`, `application*` **w**; also fires `GenerateRunRecordsTask` (`SaveDeploymentDetailsUseCase.java:68`) | WOPS |
| `CreateDeploymentVersion` `:1428` | `CreateDeploymentVersionRequest` → `CreateDeploymentVersionResponse` | `deployment_version`, `schedule` **w** | PIPE |
| `GetDeploymentDetailsByDeploymentVersionId` `:1409` | `GetDeploymentDetailsRequest` → `GetDeploymentDetailsResponse` | 10-table join (see §9) | IF |
| `GetActiveDeploymentsDetailsByDeploymentVersionId` `:1410` | `GetDeploymentDetailsRequest` → `GetActiveDeploymentsDetailsResponse` | same join, `active=true` | IF |
| `GetDeploymentDetailsWithId` `:1447` | `GetDeploymentDetailsRequest` → `GetDeploymentDetailsWithIdResponse` | topology join + `workflow_run` r | WOPS |
| `GetAllActiveDeployments` `:1420` | `GetAllActiveDeploymentsRequest` → `GetAllActiveDeploymentsResponse` | topology join | IF |
| `GetDeploymentDetailsByResourceName` `:1407` | `…Request` → `…Response` | `credential_assignment_registry` ⋈ `deployment_state` ⋈ `deployment_version` ⋈ `customer` ⋈ `workflow_sku` ⋈ `workflow_variant` ⋈ `variant` (`DeploymentStateRepositoryImpl.java:123-139`) | WM† |
| `GetDeployedVersionDetails` `:1455` | `GetDeployedVersionRequest` → `GetDeployedVersionResponse` | `deployment_state`, `deployment_version` r | WOPS |
| `validateScaleUpDownSafeDeployment` `:1434` | `…Request` → `…Response` | `dependency` r | IF |

## 3. Tenancy & catalogue — 4 rpcs

| rpc | request → response | Tables | Callers |
|---|---|---|---|
| `FindClientIdByClientName` `:1427` | `ClientName` → `ClientId` | `customer` r | PIPE |
| `GetAllTenants` `:1448` | `google.protobuf.Empty` → `TenantDetails` | `customer` r | WOPS |
| `getDisplayNameByWorkflowName` `:1360` | `WorkflowName` → `WorkflowDisplayName` | `workflow_sku` r | WOPS |
| `getSubscriptionsByWorkflowNameVariantNameAndClientName` `:1462` | `GetSubscriptionsRequest` → `GetSubscriptionResponse` | `subscription` ⋈ `deployment_version` r | IF |
| `ChurnWorkflow` `:1458` | `ChurnWorkflowRequest` → `ChurnWorkflowResponse` | `subscription` **w** | IF |

## 4. Schedules — 6 rpcs → `schedule`, `workflow_run`

| rpc | request → response | Tables | Callers |
|---|---|---|---|
| `GetScheduleTriggers` `:1413` | `GetScheduleTriggerRequest` → `GetScheduleTriggerResponse` | `schedule`, `deployment_version` r | WOPS |
| `UpdateWorkflowAndScheduleStatus` `:1414` | `UpdateWfAndScheduleStatusRequest` → `UpdateWfAndScheduleStatusResponse` | `schedule` **w**, `workflow_run` **w**, `deployment_version` r | WOPS |
| `UpdateScheduleStatusTemp` `:1418` | `ScheduleStatusRequest` → `ScheduleStatusResponse` | `schedule` **w**, `deployment_state` r | WOPS |
| `GetWorkflowScheduleInfo` `:1424` | `GetWorkflowScheduleInfoRequest` → `GetWorkflowScheduleInfoResponse` | `workflow_run` ⋈ `schedule` r | — |
| `GetWorkflowInfo` `:1425` | `GetWorkflowInfoRequest` → `GetWorkflowInfoResponse` | `workflow_run` r + topology join | — |
| `SaveAdhocWorkflowRun` `:1366` | `SaveAdhocWorkflowRunRequest` → `SaveAdhocWorkflowRunResponse` | `workflow_run` **w**, `deployment_version` r | WOPS |
| `ScheduleAdhocWorkflowRun` `:1426` | `ScheduleAdhocWorkflowRunRequest` → `ScheduleAdhocWorkflowRunResponse` | `schedule` **w**, `workflow_run` **w**, `deployment_version` r | — |

## 5. Events — 9 rpcs → `events`, `tbl_event_status`

| rpc | request → response | Tables | Callers |
|---|---|---|---|
| `SaveEvent` `:1371` | `Event` → `Event` | `events` **w** | — |
| `FindEventsByIds` `:1372` | `EventIds` → `Events` | `events` r | IF |
| `GetAllEvents` `:1373` | `Empty` → `Events` | `events` r | IF |
| `UpdateEvent` `:1374` | `Event` → `SuccessResponse` | `events` **w** | — |
| `removeEvent` `:1375` | `EventId` → `SuccessResponse` | `events` **d** | — |
| `SaveEventStatus` `:1378` | `EventStatus` → `EventStatus` | `tbl_event_status` **w** | EP |
| `GetEventStatusForScheduler` `:1379` | `SchedulerRequest` → `EventWebhookStatusList` | `tbl_event_status` r | EP |
| `UpdateEventStatusById` `:1380` | `EventStatus` → `EventStatus` | `tbl_event_status` **w** | EP |
| `GetEventStatusById` `:1381` | `EventStatusId` → `EventStatus` | `tbl_event_status` r | — |

## 6. Webhooks — 14 rpcs → `webhooks`, `webhook_events`, `webhook_subscription`

**Note:** DMS owns webhook *registration and subscription* only. Delivery, retries and the
circuit breaker live in webhook-service's own `destinations`/`webhook_queue` schema —
see webhook-delivery-suspended-is-not-a-dms-column (archived).

| rpc | request → response | Tables | Callers |
|---|---|---|---|
| `RegisterWebhook` `:1388` | `RegisterWebhookRequest` → `RegisterWebhookResponse` | `webhooks` **w**, `webhook_events` **w** | IF |
| `UpdateWebhook` `:1400` | `UpdateWebhookRequest` → `SuccessResponse` | `webhooks` **w** | IF |
| `UpdateWebhookStatusById` `:1401` | `Webhook` → `Webhook` | `webhooks` **w** | EP |
| `removeWebhook` `:1402` | `WebhookId` → `SuccessResponse` | `webhooks` **d** | — |
| `FindWebhookById` `:1397` | `WebhookId` → `Webhook` | `webhooks` r | IF |
| `GetAllWebhooks` `:1398` | `Empty` → `Webhooks` | `webhooks` r | IF |
| `FindWebhooksByEventId` `:1399` | `EventId` → `Webhooks` | `webhooks` ⋈ `webhook_events` r | — |
| `FindWebhooksByEventType` `:1393` | `EventType` → `Webhooks` | `webhooks` ⋈ `webhook_events` ⋈ `events` r | EP |
| `FindWebhooksByEventTypeAndDeploymentVersionId` `:1394` | `…Request` → `Webhooks` | + `webhook_subscription`, `subscription`, `deployment_version` r | EP |
| `FindWebhooksByEventTypeAndWorkflowDetails` `:1396` | `…Request` → `Webhooks` | + `workflow_sku`, `variant`, `customer` r | EP |
| `SubscribeEventType` `:1389` | `SubscribeRequest` → `SubscribeResponse` | `webhook_events` **w**, `events` r | IF |
| `UnSubscribeEventType` `:1390` | `UnSubscribeRequest` → `UnSubscribeResponse` | `webhook_events` **d** | IF |
| `SubscribeWebhook` `:1391` | `SubscribeWebhookRequest` → `SubscribeWebhookResponse` | `webhook_subscription` **w** | IF |
| `UnSubscribeWebhook` `:1392` | `UnSubscribeWebhookRequest` → `UnSubscribeWebhookResponse` | `webhook_subscription` **w** (`status=false`) | IF |

⚠ devops-pipeline `release/v4.0.8` calls **`SubscribeWebhookByClient`**
(`libs/webhook/scripts/subscribe-webhook-by-client.py:48`) — **that rpc does not exist at
v1.5.0**; it is only in v1.8.2.

## 7. Interface tasks (the ETP triad) — 3 rpcs → `interface_task`

| rpc | request → response | Tables | Callers |
|---|---|---|---|
| `PerformTask` `:1383` | `PerformTaskRequest` → `PerformTaskResponse` | `interface_task` **w**, `deployment_version` r, `application` r | IF, MON, CM |
| `GetTaskStatus` `:1384` | `TaskStatusRequest` → `TaskResponse` | `interface_task` r | IF |
| `UpdateTaskResponse` `:1385` | `UpdateTaskResponseRequest` → `TaskResponse` | `interface_task` **w** | IF, WM, MON, CM |

## 8. Credential assignments — 3 rpcs → `credential_assignment_registry`

Implemented **inline in the service impl**, not via a use case (`DeploymentManagementServiceImpl.java:264-344`).

| rpc | request → response | Tables | Callers |
|---|---|---|---|
| `addAssignedCredentialToStore` `:1404` | `PodCredentialAssignment` → `StoreCredentialsAssignmentsResponse` | `credential_assignment_registry` **w** (`status='ACTIVE'`) | CM |
| `updateReleaseStatusToStore` `:1405` | `StoreCredentialReleaseRequest` → `StoreCredentialsReleaseResponse` | `credential_assignment_registry` **w** (status only, `CredentialRepositoryImpl.java:84-106`) | CM |
| `fetchActiveCredentialsFromStore` `:1406` | `FetchActiveCredentialsRequest` → `FetchActiveCredentialsResponse` | `credential_assignment_registry` r (`podName` + `status='ACTIVE'`) | CM |

## 9. App / worker status — 7 rpcs → `deployment_app_status`, `fb_worker_pod`

| rpc | request → response | Tables | Callers |
|---|---|---|---|
| `updateAppStatus` `:1432` | `UpdateAppStatusRequest` → `google.protobuf.Empty` | `deployment_app_status` **w**, `deployment_version` r | WM† |
| `UpdateDeploymentAppStatus` `:1446` | `UpdateDeploymentAppStatusRequest` → `UpdateDeploymentAppStatusResponse` | `deployment_app_status` **w** | — |
| `GetAppStatus` `:1445` | `GetAppStatusRequest` → `GetAppStatusResponse` | `deployment_app_status` ⋈ `application` r | — |
| `validateAppStatus` `:1433` | `ValidateAppStatusRequest` → `ValidateAppStatusResponse` | `deployment_app_status`, `deployment_version` r | IF |
| `GetFbWorkerPodDetails` `:1460` | `GetFbWorkerPodDetailsRequest` → `GetFbWorkerPodDetailsResponse` | `fb_worker_pod`, `deployment_version` r | IF, WOPS, WM† |
| `UpdateFbWorkerPodDetails` `:1461` | `UpdateFbWorkerPodDetailsRequest` → `UpdateFbWorkerPodDetailsResponse` | `fb_worker_pod` **w** | WM† |
| `ChangeWorkerStatus` `:1430` | `ChangeWorkerStatusRequest` → `ChangeWorkerStatusResponse` | **none — UNIMPLEMENTED** | — |
| `MigrateAppWorker` `:1431` | `Empty` → `Empty` | **none — UNIMPLEMENTED** | — |

## 10. Workflow-manager state — 10 rpcs → `workflow_manager_state`

| rpc | request → response | Tables | Callers |
|---|---|---|---|
| `SaveWorkflowManagerRunStatus` `:1436` | `WorkflowManagerRunStatusRequest` → `WorkflowManagerStateInfoResponse` | `workflow_manager_state` **w**, `deployment_version` r, `workflow_run` r | WM |
| `GetWorkflowManagerState` `:1437` | `WorkflowManagerStateInfoRequest` → `WorkflowManagerStateInfoResponse` | `workflow_manager_state` r | WM† |
| `GetAllActiveWorkflowManagerRunBeforeTime` `:1438` | `Empty` → `WorkflowManagerRuns` | `workflow_manager_state` r (native SQL `WorkflowManagerStateRepositoryImpl.java:83`) | WM |
| `UpdateWorkflowManagerState` `:1440` | `WorkflowManagerStateRequest` → `WorkflowManagerStateResponse` | `workflow_manager_state` **w** (native UPDATE `:177`) | WM |
| `IncrementReceivedSignalWorkflowManagerState` `:1441` | `…Request` → `WorkflowManagerStateInfoResponse` | `workflow_manager_state` **w** (native UPDATE `:221`) | WM† |
| `UpdateWorkflowManagerStateActive` `:1442` | `…Request` → `WorkflowManagerStateResponse` | `workflow_manager_state` **w** | WM† |
| `AddWorkflowManagerStateMetadata` `:1443` | `…Request` → `WorkflowManagerStateResponse` | `workflow_manager_state.metadata` jsonb **w** | WM† |
| `UpdateScaleWorkerMetadataStatus` `:1444` | `…Request` → `WorkflowManagerStateResponse` | `workflow_manager_state.metadata` jsonb **w** | WM† |
| `GetWorkflowManagerStateMetadata` `:1465` | `…Request` → `…Response` | `workflow_manager_state.metadata` r | WM† |
| `GetActiveWorkflowManagerRun` `:1450` | `GetActiveWorkflowManagerRunRequest` → `GetActiveWorkflowManagerRunResponse` | `workflow_manager_state` r | IF, WM† |

## 11. Workflow-ops state — 5 rpcs → `workflow_ops_state`

| rpc | request → response | Tables | Callers |
|---|---|---|---|
| `insertOpsTaskState` `:1453` | `InsertOpsTaskStateRequest` → `InsertOpsTaskStateResponse` | `workflow_ops_state` **w** | WOPS |
| `GetOpsTaskState` `:1452` | `GetOpsTaskStateRequest` → `GetOpsTaskStateResponse` | `workflow_ops_state` r | WOPS |
| `UpdateOpsTaskState` `:1454` | `UpdateOpsTaskStateRequest` → `UpdateOpsTaskStateResponse` | `workflow_ops_state` **w** (`@Version` optimistic lock) | WOPS |
| `GetActiveOpsTaskStateDetails` `:1456` | `Empty` → `GetActiveOpsTaskStateDetailsResponse` | `workflow_ops_state` r (`status=ACTIVE`) | WOPS |

## 12. Other declared services (not served here)

| service | rpcs | Status |
|---|---|---|
| `MonitoringService` `:263-264` | **0** | Empty block. Verified claim — see dms-declares-two-unserved-proto-services (archived) |
| `PlatformInterface` `:266-268` | 1 — `emitEvent(CloudEvent) → Empty` | Declared here, **not registered in `Main.java`**; implemented by another subsystem |

---

## Reachability — exact

| Measure | Count |
|---|---|
| rpcs declared | **93** |
| rpcs with a call site at the **registry-pinned** consumer commits | **67** |
| rpcs with **no** call site at pinned commits | **26** |
| rpcs with a call site once the newer workflow-manager checkout is included | **78** |
| rpcs with no call site under **either** reading | **15** |

**The 15 that nothing anywhere calls:**
`ChangeWorkerStatus` · `FindWebhooksByEventId` · `GetAppStatus` · `GetConfig` ·
`GetEventStatusById` · `GetPodEndpoints` · `GetWorkflowInfo` · `GetWorkflowScheduleInfo` ·
`MigrateAppWorker` · `removeEvent` · `removeWebhook` · `SaveEvent` ·
`ScheduleAdhocWorkflowRun` · `UpdateDeploymentAppStatus` · `UpdateEvent`.

Three of those (`ChangeWorkerStatus`, `MigrateAppWorker`, `GetPodEndpoints`) **could not work
even if called** — they are the UNIMPLEMENTED set.

**The 11 that only the newer workflow-manager checkout calls** (`WM†`) — i.e. dead if you
believe `../../registry.yaml`'s `main@8b6c121` pin, live if you believe the `release/v1.2.1`
working tree: `AddWorkflowManagerStateMetadata`, `GetAllTemporalWFRecords`,
`GetDeploymentDetailsByResourceName`, `GetWorkflowManagerState`,
`GetWorkflowManagerStateMetadata`, `GetWorkflowRun`,
`IncrementReceivedSignalWorkflowManagerState`, `updateAppStatus`, `UpdateFbWorkerPodDetails`,
`UpdateScaleWorkerMetadataStatus`, `UpdateWorkflowManagerStateActive`.

## RPCs that consumers call but DMS v1.5.0 **does not declare**

Four production call sites target rpcs that exist only at DMS `release/v1.8.2`. Against the
pinned server every one returns **UNIMPLEMENTED**:

| rpc | Caller | Call site |
|---|---|---|
| `getQueuedWorkflowRunsForDeployment` | workflow-ops `v1.6.3` | `DeploymentManagementServiceStub` |
| `updateDeploymentDown` | workflow-ops `v1.6.3` | ″ |
| `ValidateDeploymentVersionsActive` | workflow-ops `v1.6.3` | ″ |
| `SubscribeWebhookByClient` | devops-pipeline `v4.0.8` | `libs/webhook/scripts/subscribe-webhook-by-client.py:48` |

Plus **all eight** rpcs that dynamic-scaling `v1.2.0` calls
(`GetScalingInfo`, `CreateDssTask`, `UpdateDssTask`, `CheckDssSubscription`,
`GetDynamicScalingRequestsByStatus`, `PollDynamicScalingRequests`, `SendAkstMetrics`,
`GetAppCredentialInfo`) — it ships a **completely disjoint** `DeploymentManagementService` under
the same fully-qualified name. See
dms-proto-forks-disagree-on-service-identity (archived).

**workflow-api calls zero DMS rpcs.** It has no `.proto`, no gRPC dependency and no
`ManagedChannel`; its local `getTaskStatus(…)` is an internal method, not this API's
`GetTaskStatus`.

---

## Table catalogue

**44 physical tables** = 38 `@Entity` + 3 Hibernate-only join tables + 3 migration-only tables
(`flyway_schema_history` excluded). `PK` = primary key, `FK →` = foreign key target.

`*` marks the **18** tables a `CREATE TABLE` in this repo can actually build. The other **26**
— including every core table — **predate the Flyway baseline** and exist in this repo only as
JPA mappings, so a clean database cannot be provisioned from it. See
dms-core-schema-predates-flyway (archived).

### Deployment topology (9)

| Table | Entity | PK | Key columns / FKs |
|---|---|---|---|
| `deployment` | `DeploymentEntity.java:14` | `deployment_id` | FK `subscription_id` → `subscription` |
| `deployment_version` | `DeploymentVersionEntity.java:18` | `deployment_version_id` | FKs `deployment_config_id`, `deployment_id`, `workflow_variant_id`, `workflow_sku_id`, `customer_id`; `active boolean` |
| `deployment_config` | `DeploymentConfigEntity.java:23` | `config_id` | `version_id`, `config_value jsonb` |
| `deployment_state` | `DeploymentStateEntity.java:17` | `deployment_state_id` | FK `deployment_version_id`; `key`, `value`, `description` |
| `deployment_app_status`\* | `DeploymentAppStatusEntity.java:15` | `deployment_app_status_id` | FKs `application_id`, `deployment_id`; `app_status` CHECK ∈ {PAUSED, RUNNING, TERMINATED, PAUSING, RESUMING}; UNIQUE (`deployment_id`, `app_name`) — **constraint still names the dropped `app_name` column** |
| `infra_component` | `InfraComponentEntity.java:15` | `component_id` | `deployment_version_id`, `key`, `value` (all `length=50`) |
| `subscription` | `SubscriptionEntity.java:13` | `subscription_id` | FKs `workflow_sku_id`, `customer_id`, `variant_id`; `start_date`, `end_date` |
| `customer` | `CustomerEntity.java:15` | `customer_id` | `name`, `email`, `status` |
| `runtime_config` | `RuntimeConfigEntity.java:16` | `config_id` | FK `deployment_version_id`; `config_name`, `config_version`, `config_value` |

### Catalogue / SKU (8)

| Table | Entity | PK | Key columns / FKs |
|---|---|---|---|
| `workflow_sku` | `WorkflowSkuEntity.java:15` | `workflow_sku_id` | `name`, `display_name`, `description` |
| `workflow_variant` | `WorkflowVariantEntity.java:15` | `variant_id` | FKs `variant_name_id` → `variant`, `workflow_impl_id` → `workflow_impl` |
| `variant` | `VariantEntity.java:16` | `variant_id` | `variant_name`, `variant_status` enum |
| `workflow_impl` | `WorkflowImplEntity.java:15` | `implementation_id` | FK `workflow_sku_id`; `application_version_id`, `aggregator_type_id`, `workflow_version` |
| `application` | `ApplicationEntity.java:15` | `application_id` | `name`, `description`, `app_type` |
| `application_impl` | `ApplicationImplEntity.java:15` | `application_impl_id` | FK `application_id`; `version`, `app_type` |
| `application_version` | `ApplicationVersionEntity.java:13` | `application_version_id` | `version`, `description` |
| `application_worker` | `AppWorkerEntity.java:15` | `app_worker_id` | FK `application_id`; `version`, `technology`, `status` |
| `aggregator` | `AggregatorEntity.java:11` | `aggregator_id` | `name`, `description` |

### Runs & schedules (4)

| Table | Entity | PK | Key columns / FKs |
|---|---|---|---|
| [`workflow_run`](../tables/workflow_run.md) | `WorkflowRunEntity.java:21` | `workflow_run_id` | FKs `deployment_version_id` (NOT NULL), `schedule_id`; `workflow_id`, `workflow_input jsonb`, `start_time`, `expected_start_time`, `end_time`, `invocation_type`, `status` (pg enum `workflow_run_status`), `event_status`, `remarks`, `workflow_trigger_info`\*; UNIQUE (`expected_start_time`, `schedule_id`)\* |
| [`schedule`](../tables/schedule.md) | `ScheduleEntity.java:17` | `schedule_id` | FKs `runtime_config_id` → `runtime_config.config_id`, `deployment_version_id`; `cron_expression`, `workflow_input jsonb`, `timezone`, `trigger_id`, `status` |
| `temporal_workflow`\* | `TemporalWorkflowEntity.java:16` | `id` | FK `workflow_run_id` → `workflow_run`; `temporal_id`, `parent_temporal_id`, `status workflow_run_status` |
| [`scheduler_task`](../tables/scheduler_task.md)\* | `SchedulerTaskEntity.java:13` | `id` (`uuid_generate_v4()`) | `scheduler_type`, `deployment_version_id` (**no FK**), `owner`, `status scheduler_task_status` |

### Webhooks & events (5)

| Table | Entity | PK | Key columns / FKs |
|---|---|---|---|
| [`webhooks`](../tables/webhook.md) | `WebhookEntity.java:17` | `id` | UNIQUE `url`; `webhook_name(50)`, `secure_key`, `auth_type`, `is_enabled`, `root_user`\*, `max_retry_count`, `status` |
| [`events`](../tables/event.md) | `EventEntity.java:17` | `id` | `event_name(255)`, `event_type(255)`, `status(15)` |
| `tbl_event_status` | `EventStatusEntity.java:16` | `id` | `event_id`, `event_name`, `webhook_id`, `status`, `event jsonb` — **no FKs declared** |
| [`webhook_subscription`](../tables/webhook_subscription.md)\* | `WebhookSubscriptionEntity.java:15` | composite (`webhook_id`, `subscription_id`) via `@IdClass` | FKs to `webhooks`, `deployment`; `status boolean` |
| `webhook_events` | *join table only* (`WebhookEntity.java:52-56`) | (`webhook_id`, `event_id`) | no migration — Hibernate-mapped only |

### Lifecycle state (4)

| Table | Entity | PK | Key columns / FKs |
|---|---|---|---|
| [`workflow_manager_state`](../tables/workflow_manager_state.md)\* | `WorkflowManagerStateEntity.java:17` | `workflow_manager_state_id` | FK `deployment_version_id`; `reference_id`, `app_name`, `workflow_manager_action`, `action_start_date_and_time` (**VARCHAR, not timestamp**), `active`, `total_signal_count`, `received_signal_response_count`, `workflow_run_id` (**FK dropped** 2025-10-14), `timeout_in_minutes` DEFAULT 65, `metadata jsonb` NOT NULL DEFAULT `{}` |
| [`workflow_ops_state`](../tables/workflow_ops_state.md)\* | `WorkflowOpsStateEntity.java:19` | `ops_state_id` | `task_id VARCHAR(255)` (was UUID), `task_name`, `status event_status`, `task_payload jsonb`, `timeout`, `version bigint` (`@Version`) |
| [`interface_task`](../tables/interface_task.md) | `InterfaceTaskEntity.java:22` | `task_id` | `task_request jsonb`, `task_response jsonb`, `is_long_running`, `deployment_id` (**no FK**), `task_type`, `source`, `destination` |
| `fb_worker_pod`\* | `FbWorkerPodEntity.java:14` | `id` | FKs `application_id` (CASCADE), `deployment_version_id` (CASCADE); `replica_count` |

### Credentials (1)

| Table | Entity | PK | Key columns / FKs |
|---|---|---|---|
| [`credential_assignment_registry`](../tables/credential_assignment_registry.md) | `CredentialAssignmentRegistry.java:16` | `lock_id VARCHAR(50)` (**string PK, app-assigned**) | `pod_name(60)` NOT NULL, `kube_namespace(60)`, `requester_id(50)`, `fetched_time VARCHAR(40)`\* (**a timestamp stored as text**), `resource_name(100)`\*, `status` |

### Artifact-version registries (8)

All share the shape `(id uuid PK, version varchar(50), stable boolean, created_at, updated_at)`.

| Table | Entity | Notes |
|---|---|---|
| [`task_sdk`](../tables/task_sdk.md)\* | `TaskSDKEntity.java:15` | extra `type varchar(50)`; **PK constraint is named `task_pkey` and the migration's trailing `ALTER TABLE public.task OWNER` references a non-existent `task` table** (`V1_2025_08_11_007__create_task_sdk.sql:5,19`) |
| `platform_core`\* | `PlatformCoreEntity.java:14` | `stable` NOT NULL here only |
| `fb_core`\* | `FBCoreEntity.java:14` | — |
| `common_util`\* | `CommonUtilEntity.java:14` | entity maps `stable` as **String** vs SQL `boolean` |
| `state_service`\* | `StateServiceEntity.java:22` | entity maps `stable` as **String** vs SQL `boolean` |
| `notification_hub`\* | `NotificationHubEntity.java:14` | — |
| `non_rpa_worker_core`\* | **none** | migration-only, unreachable |
| `dependency`\* | `DependencyEntity.java:17` | `service_id`, `service_type`, `key`, `value` (entity says `jsonb`, SQL says `text`) |

### Join tables (3)

| Table | Declared in | PK |
|---|---|---|
| `deployment_version_application_worker` | `DeploymentVersionEntity.java:50-54` + migrations | (`deployment_version_id`, `app_worker_id`)\* — `status` and `mappingid` columns dropped 2025-09/2025-11 |
| `webhook_events` | `WebhookEntity.java:52-56` | (`webhook_id`, `event_id`) |
| `workflow_impl_application_impl`\* | `WorkflowImplEntity.java:41-46` | (`workflow_impl_id`, `application_impl_id`), both FK'd |

### Migration-only, no entity, no repository — dead schema (2 + 1 above)

| Table | Migration | PK / FK |
|---|---|---|
| `workflow_state`* | `V1_2026_01_30_001__create_workflow_state.sql` | `id`; FK `workflow_impl_id` → `workflow_impl.implementation_id` CASCADE |
| `state_property`* | `V1_2026_01_30_002__create_state_property.sql` | `id`; FK `workflow_state_id` → `workflow_state.id` CASCADE |
| `non_rpa_worker_core`* | `V1_2026_01_30_003__create_non-rpa-worker-core.sql` | `id` |

### PostgreSQL enum types owned

`workflow_run_status` (values added by migration: `PARTIALLY_TERMINATED`, `YET_TO_START`;
`NOT_STARTED`→`YET_TO_START` and `STARTED`→`IN_PROGRESS` data migrations) ·
`event_status` (`ACTIVE`, `IN_ACTIVE`, `COMPLETED`, `FAILED`) ·
`scheduler_task_status` (`YET_TO_START`, `IN_PROGRESS`, `COMPLETED`, `FAILED`).

### `task_route` — absent

Not a table, not an entity, not a migration, not an rpc at this commit. It exists at DMS
`release/v1.8.2`. See
task-route-absent-in-pinned-dms-commit (archived).

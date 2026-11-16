---
type: contract
name: DeploymentManagementService (proto)
fqn: com.e5.platform.ss.proto.DeploymentManagementService
java_package: com.e5.management.proto
owner: deployment-mgmt
source: e5-deployment-mgmt-service/src/main/proto/deployment_management.proto:2335-2516
transport: grpc
rpc_count: 93
forks_in_workspace: 13
provenance: code
verified: 2026-10-02
---

# Contract: `com.e5.platform.ss.proto.DeploymentManagementService`

The platform's largest contract and its least governed. **93 rpcs**, one file, no versioning, no
`v1` package segment, and **13 divergent copies of the file across the workspace**.

**Authoritative copy** (the one the server compiles):
`e5-deployment-mgmt-service/src/main/proto/deployment_management.proto` at `release/v1.5.0`
(`75bbdb3`). Service block `:1344-1466`. Full rpc catalogue:
[deployment-mgmt-grpc-api](../subsystems/deployment-mgmt-grpc-api.md).
event-processor's vendored 13-rpc subset is documented separately in
[deployment-mgmt-webhook-rpcs](deployment-mgmt-webhook-rpcs.md).

## Wire identity

`package com.e5.platform.ss.proto` (`:8`), `option java_package = "com.e5.management.proto"`
(`:10`), `option java_multiple_files = true` (`:12`). Method paths on the wire are therefore
`/com.e5.platform.ss.proto.DeploymentManagementService/<Rpc>`.

⚠ **The package and the Java package differ**, so a reader navigating generated Java
(`com.e5.management.proto.*`) will not find the proto package name anywhere in the imports.

## Naming is not consistent

The 93 rpcs use **three** naming conventions simultaneously:

| Convention | Count (approx) | Examples |
|---|---|---|
| `UpperCamelCase` (proto style guide) | ~60 | `GetWorkflowRun`, `SaveEventStatus`, `UpdateOpsTaskState` |
| `lowerCamelCase` | ~30 | `getYetToStartWorkflows`, `updateWfRunStatus`, `insertOpsTaskState` |
| abbreviation-heavy | — | `getWFRunListForDeploymentID`, `getWFRunRecordWithPayload` |

grpc-java lowercases the first letter for the stub method, so `GetWorkflowRun` and a
hypothetical `getWorkflowRun` would collide. `insertOpsTaskState` sits between
`GetOpsTaskState` and `UpdateOpsTaskState` in the same five-rpc group.

## Request/response shape hazards

- **`Empty` is overloaded.** The file declares its own `message Empty {};` (`:465`) *and*
  imports `google/protobuf/empty.proto` (`:4`). Both are used as rpc parameters:
  `getYetToStartWorkflows(Empty)` `:1345` and `MigrateAppWorker(google.protobuf.Empty)` `:1431`.
  They are **different types on the wire** (different fully-qualified names) despite both being
  zero-field.
- **Responses are reused as requests.** `SaveEvent(Event) returns (Event)` `:1371`;
  `SaveEventStatus(EventStatus) returns (EventStatus)` `:1378`;
  `UpdateWebhookStatusById(Webhook) returns (Webhook)` `:1401`. A field added for the response
  becomes settable on the request.
- **Two rpcs share one request type with different semantics:**
  `GetDeploymentDetailsByDeploymentVersionId` and
  `GetActiveDeploymentsDetailsByDeploymentVersionId` both take `GetDeploymentDetailsRequest`
  (`:1409-1410`), as does `GetDeploymentDetailsWithId` (`:1447`) — three different response
  types off one request.
- `DeploymentVersionId` is both a request type and the response of `getDeploymentId` `:1356`
  and `GetDeploymentVersionIdByCriteria` `:1464`.

## Three rpcs are declared but cannot be served

| rpc | Why |
|---|---|
| `GetPodEndpoints` `:1359` | `DeploymentManagementServiceImpl.java:113` calls `super.getPodEndpoints(...)` → the generated base returns `UNIMPLEMENTED` |
| `ChangeWorkerStatus` `:1430` | no `@Override` anywhere |
| `MigrateAppWorker` `:1431` | no `@Override` anywhere |

monitoring-service and credential-monitor both still carry a `getPodEndpoints` code path
(`MonitoringClientImpl`), so the failure is reachable.

## Sibling services in the same file

| service | rpcs | Served by DMS? |
|---|---|---|
| `MonitoringService` `:263-264` | **0 — the block is empty** | no |
| `PlatformInterface` `:266-268` | 1 — `emitEvent(CloudEvent) returns (Empty)` | **no** — not in `Main.java:39-45` |
| `DeploymentManagementService` `:1344` | 93 | yes |

See dms-declares-two-unserved-proto-services (archived).

## Dead weight in the file

Lines `690-786` are a ~97-line **commented-out** block of config messages (`Config`,
`WorkflowVariant`, `WorkflowSchedule`, `FBDelegationOptions`, `UIAutomation`,
`VideoRecording`, `Credentials`, …) — a config model that was designed, encoded in the contract,
then commented rather than deleted. `message Schedule` (`:791`) and `message Service`/`Infra`/
`TriggerConfig`/`Pod`/`Kafka`/`DynamoDB`/`Temporal`/`S3`/`StateService` (`:358-464`) describe the
same territory and *are* live.

## Fork topology — this is the real hazard

13 files named `deployment_management.proto` or `mgmt.proto` exist in the workspace, with rpc
counts **8, 16, 18, 18, 58, 58, 59, 64, 64, 76, 94, 116, 122**. Every consumer compiled against
a prune of the whole. Consequences, derived from call sites rather than from the protos:

| Consumer | rpcs in its copy | rpcs it actually calls |
|---|---|---|
| interface `v1.6.8` | 34 | 25 |
| workflow-manager `main@8b6c121` | 59 | 4 (19 at the newer `v1.2.1` checkout) |
| monitoring-service | 18 (`mgmt.proto`) | 14 |
| credential-monitor | 16 (`mgmt.proto`) | 14 declared, **3 credential-meaningful** |
| event-processor | 13 (`deployment_mgmt_service.proto`) | 7 |
| workflow-ops (Python) | 58 | 16 |
| devops-pipeline (Python) | — | 2 (+1 that does not exist at v1.5.0) |
| **dynamic-scaling** | **8, all disjoint from v1.5.0** | 6 — **every one would return UNIMPLEMENTED** |
| workflow-api | **0 — no proto, no gRPC at all** | 0 |

Full analysis:
dms-proto-forks-disagree-on-service-identity (archived).

## Breaking changes already in flight at v1.8.2

- `getYetToStartWorkflows` changes its request type from `Empty` to
  `YetToStartWorkflowsRequest` — **same method name, different request message**. A v1.5.0
  client calling a v1.8.2 server sends the wrong type.
- 28 rpcs added, including `GetTaskRoute`/`UpsertTaskRoutes`/`DeactivateTaskRoute`
  ([`task_route`](../tables/task_route.md)), the eight dynamic-scaling rpcs, and
  `SubscribeWebhookByClient` (which devops-pipeline `release/v4.0.8` already calls).

## How to change this contract safely

1. Edit the copy in `e5-deployment-mgmt-service` **first** — it is the only one that compiles
   into the server.
2. Add an `@Override` in `DeploymentManagementServiceImpl` in the same change, or the rpc ships
   as UNIMPLEMENTED (three already have).
3. Propagate to the consumer's own fork; there is no shared artifact to bump.
4. Never change a request message type on an existing method name.

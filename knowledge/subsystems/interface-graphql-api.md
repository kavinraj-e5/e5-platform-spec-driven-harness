---
type: api
protocol: graphql
endpoint: POST /v1/api
owner: interface
schema_dir: src/main/resources/graphql/*.graphqls
query_count: 8
mutation_count: 17
subscription_count: 0
provenance: code
verified: 2026-10-02
---

# Interface GraphQL API

Exposed by [interface](interface.md) at `release/v1.6.8`. SDL across 10
`.graphqls` files; root types declared in `query.graphqls:1-10` and `mutation.graphqls:1-20`.

**Legend** — MGMT = deployment-mgmt (gRPC) · WFOPS-G = workflow-ops (gRPC) ·
AKST = task-manager (Kafka→VQ) · **→WFMGR / →WFOPS / →MON** = the VQ's consumer.

## Queries (8)

| Field | Downstream | Sync |
|---|---|---|
| `deployment(deploymentVersionID)` | MGMT `GetDeploymentDetailsByDeploymentVersionId` | sync |
| `activeDeployments(deploymentVersionID)` | MGMT `GetActiveDeploymentsDetailsByDeploymentVersionId` | sync |
| `taskStatus(taskId)` | MGMT `GetTaskStatus`, then a **10-branch dispatch on `taskType`** into 10 response shapes | sync |
| `exportState(…)` | MGMT `PerformTask` + **Kafka** → AKST →WFOPS | **async** ⚠ |
| `getStateList(…)` | WFOPS-G `GetStateList` | sync |
| `webhooks(webhookId?)` | MGMT `GetAllWebhooks` **or** `FindWebhookById` — runtime-selected | sync |
| `activeDeploymentVersions(input)` | MGMT `GetAllActiveDeployments` | sync |
| `workflowRuns(…)` | MGMT `ListWorkflowRuns` | sync |

⚠ **`exportState` is declared a Query but writes a DB row and produces to Kafka.** Any model
assuming "queries are read-only" is wrong here.

## Mutations (17)

| Field | Downstream | Sync |
|---|---|---|
| `performDeploymentHealthCheck` | MGMT `PerformTask` + Kafka → AKST →MON (`HEALTH`) | async |
| `startWorkflow` | WFOPS-G `TriggerWorkflowRun`; **+MGMT `GetDeploymentVersionIdByCriteria` only if `deploymentVersionId` absent** | sync |
| `registerWebhook` | MGMT `RegisterWebhook` | sync |
| `subscribeEvents` | MGMT `GetAllEvents` → `SubscribeEventType` (**second RPC skipped if any name fails to match**) | sync |
| `subscribeWebhook` | MGMT `SubscribeWebhook` | sync |
| `unSubscribeWebhook` | MGMT `UnSubscribeWebhook` | sync |
| `updateWebhook` | MGMT `UpdateWebhook` | sync |
| `initiatePodRestartTask` | MGMT `PerformTask` + Kafka → AKST →WFOPS (`RestartWorkflow`, **hardcoded VQ**) | async |
| `updateDeploymentScheduleState` | MGMT `PerformTask` + Kafka → AKST →WFOPS | async |
| `importToState` | MGMT `PerformTask` + Kafka → AKST →WFOPS | async |
| `pauseOrResumeApp` | MGMT `PerformTask` → `validateAppStatus` → **Kafka only if valid**, else `UpdateTaskResponse` | async, conditional |
| `terminateWorkflow` | MGMT `PerformTask` → `ValidateWorkflowRun` → Kafka only if valid. **`terminateMode: HARD\|SOFT` selects one of two task types** | async, conditional |
| `churnWorkflow` | MGMT `ChurnWorkflow` | sync |
| `scaleAppWorker` | MGMT ×4 (`validateScaleAppWorker`, `GetFbWorkerPodDetails`, `GetActiveWorkflowManagerRun`, `PerformTask`) + Kafka → AKST →WFMGR | async |
| `performDeployment` | MGMT ×5 + Kafka → AKST →WFOPS. **Largest fan-out (6-7 calls)** | async |
| `releaseCredential` | MGMT `PerformTask` + Kafka → AKST →WFMGR | async |
| `holdOrResumeWFTrigger` | MGMT `PerformTask` + Kafka → AKST →WFOPS | async |

## Facts that change how you reason about this API

- **11 of 25 endpoints (44%) fan out to both gRPC and Kafka in one request.** The invariant is
  gRPC `PerformTask` to mint a `taskId`, then Kafka to dispatch. **Neither leg is transactional.**
- **Fan-out is conditional.** `pauseOrResumeApp`, `terminateWorkflow` and `performDeployment`
  reach a different set of subsystems depending on whether a gRPC validation guard passes.
- **`performDeployment` mutates remote state during validation** — it calls
  `DisableDssSubscription` *before* its own `PerformTask`, with no compensation if a later step
  throws (`TaskManagerHelperImpl.java:292`).
- **`taskStatus` is one field with 10 possible concrete return types**, determined by which
  mutation created the task. Adding a lifecycle action therefore requires edits in **four**
  places: both routing switches, `getTaskStatus`, and `StatusTypeResolver`.
- **Three orphan resolvers** are annotated but absent from the SDL and are unreachable:
  `unSubscribeEventTypes`, `eventTypes`, and `removeWebhook` (whose body is `return false;`).
- **The shared proto over-reports coupling.** DMS's proto declares 31 RPCs; this service calls
  22. Derive edges from call sites, never from the proto.

**Related:** unsynchronised-routing-switches (archived) ·
[transport-task-protocol-v2](../contracts/transport-task-protocol-v2.md)

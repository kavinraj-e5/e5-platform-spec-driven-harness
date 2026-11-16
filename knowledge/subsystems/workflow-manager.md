---
type: subsystem
name: Workflow Manager
repo: e5-workflow-manager-service
repo_path: platform-whole-context/subsystem-workflow-interaction-support/manager-service  # NOT READ - indexed from mirror
branch: release/v1.4.7
commit: 5451b4e
language: java
build: gradle
deployment: workflow-manager-service
namespace_pattern: platform-subsystem-{env}
etp_roles: [task-worker, task-reader]
owns_schema: false
ports: [50051]
health: none - gRPC reflection only, no HTTP probe
calls: [deployment-mgmt, akst, workflow-ops, event-store]
called_by: [ss-interface, workflow-ops]
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.3.2
deployment_lag: release/v1.4.7 available, v1.3.2 running
prose_reviewed_at_commit: c5d421b
---

# Workflow Manager

The **deployment-action worker** for the subsystem plane: it dequeues long-running
administrative actions from an AKST virtual queue, drives each one to completion across
Kafka and gRPC, and tracks its progress as a *workflow-manager run* held in deployment-mgmt.
It is **not** [Workflow Ops](workflow-ops.md). Manager signals running workflow engines and
holds no cloud credentials; Ops mutates Kubernetes/Jenkins/AWS. The name collision is
documented nowhere in either repo.

**Responsibility (what it owns):** nine deployment actions — pause/resume, terminate and
soft-terminate, scale up and scale down, app-worker scaling, credential release, and
DSS enable/disable deployment. For each it converts an AKST task into the right Kafka signal
or gRPC call, counts the acknowledgements that come back, and runs a timeout-driven
compensating action if the run stalls.

**Not its responsibility:** no Kubernetes, Jenkins or AWS client is on the classpath; it asks
workflow-ops to do that. It owns no database.

**Entry points:** `src/main/java/com/e5/WorkflowManagerEngine.java:25` (`main`) starts five
things and then blocks on the gRPC server:
gRPC server `:26`, task poller `:27`, workflow-response consumer `:28`,
task-response consumer `:29`, unified scheduler `:30`.

## gRPC surface

Served on **50051**, plaintext, **no auth**, **server reflection enabled**
(`src/main/java/com/e5/grpc/server/WorkflowManagerGrpcServer.java:29-32`). Port comes from
env `GRPC_SERVER_PORT` with a compiled-in default (`WorkflowManagerEngine.java:45`).

| rpc | caller | implementation |
|---|---|---|
| `completeWorkflow` | monitoring-service | `WorkflowManagerImpl.java:14` → `CompleteWorkflowUseCase` |
| `updateWorkflowSuspended` | workflow-ops | `WorkflowManagerImpl.java:25` → `UpdateWorkflowSuspendedUseCase` |

Full detail, including the caller-proto comparison: [gRPC API card](workflow-manager-grpc-api.md).

**Inbound:**
- **AKST virtual queue — env-injected, no default:** config key
  `workflowManagerRpcInfo.taskVirtualQueueName`, read at
  `src/main/java/com/e5/grpc/WorkflowManagerTaskHandler.java:91`. Blank (`"value"` sentinel)
  in all committed configs; the value is supplied per environment. Producers hardcode their
  own copy — see [hardcoded queue names](../known-issues/monitoring-vq-hardcoded-ignores-config.md).
- Dispatch table — a nine-branch `if`/`else if` chain on `taskPayload.fbName`
  (`WorkflowManagerTaskHandler.java:98-126`): `PAUSE_RESUME_APP`, `TERMINATE_WORKFLOW`,
  `SOFT_TERMINATE_WORKFLOW`, `SCALE_DOWN`, `SCALE_UP`, `CREDENTIAL_RELEASE`,
  `FB_WORKER_POD_SCALE_APP_WORKER`, `ENABLE_DEPLOYMENT`, `DISABLE_DEPLOYMENT`.
  **There is no terminal `else`**, and `:119` logs `"<fbName> Task Complete"` unconditionally.
- Kafka: `workflowManagerTopics.taskCompletionEndPoint`, filtered to `APP_PAUSED` /
  `APP_RESUMED` (`src/main/java/com/e5/consumer/WorkflowResponseConsumer.java`), plus a
  second task-response consumer (`src/main/java/com/e5/consumer/TaskResponseHandler.java`).

**Outbound:** four gRPC stub types (`src/main/java/com/e5/grpc/stubs/ServiceType.java`):
`AKST` (`isTaskAvailable`/`getTask`), `DEPLOYMENT_MANAGEMENT` (run-state read/write:
`getDeploymentStateByKey`, `saveWorkflowManagerRunStatus`, `updateWorkflowManagerState`,
`getAllActiveWorkflowManagerRunBeforeTime`), `WORKFLOW_OPS`, and `EVENT_STORE` (`sendEvent`).
Kafka producer → the per-deployment trigger topic resolved from deployment-mgmt config key
a topic config key, and → `workflowManagerTopics.taskManagerInboundEndPoint` with
`command: TaskComplete`, `taskType: E5_WF_MANAGER`.

**Data owned:** none. Every piece of run state (`referenceId`, `totalSignalCount`,
`receivedSignalResponseCount`, `isActive`, `actionStartDateAndTime`) lives in deployment-mgmt.

**Config keys:** five JSON-blob env vars read by `ConfigsLoader`
(`src/main/java/com/e5/config/ConfigsLoader.java:63-89`): `workflowManagerTopics`,
`scheduler`, `workflowManagerRpcInfo`, `kafka`, and `environment`. All are `"value"`
sentinels in committed config; values are injected per environment.

**ETP role:** **task-worker** on its configured VQ and **task-reader** of signal responses off
Kafka. It is never a task-initiator — the scheduler's compensating actions re-enter the
service's own handlers in-process rather than enqueuing a task.

**The scheduler** (`src/main/java/com/e5/service/scheduler/Scheduler.java:65-73`) sweeps
active runs older than `scheduler.timeOutForWorkflowRunInMinutes` and dispatches a
compensating action per action type, mirroring the nine dispatch branches.

**Failure modes:**
- The dispatch chain has **no terminal `else`**. An fbName routed here that is not one of the
  nine is dequeued by `getTask`, discarded, and logged as complete — see
  dropped-tasks disagreement (archived).
  Latent rather than active: all fbNames currently routed here are handled.
- Every exception in the poll loop is caught and logged at *info*, then the loop continues;
  the task is already dequeued, so it is lost with no completion event.
- The response consumer swallows all exceptions, so a failed `updateWorkflowManagerState`
  silently desynchronises the signal counter.
- `Thread.sleep` interruption in the idle branch rethrows as `RuntimeException`, killing the
  poller thread while the JVM and the gRPC server stay alive — so the pod keeps answering
  gRPC with zero task throughput, and there is no HTTP health probe that would notice.

**Code-level landmarks:** `WorkflowManagerTaskHandler.java:91-131` (poll loop + dispatch) ·
`WorkflowManagerGrpcServer.java:20-32` (server bootstrap) · `WorkflowManagerImpl.java:14,25`
(the two served rpcs) · `Scheduler.java:65-73` (compensating-action table) ·
`ConfigsLoader.java:109` (`readConfigFromEnv`, YAML-into-POJO from a raw env var).

**Known divergences:** unhandled VQ tasks are dropped (archived) ·
three incompatible WorkflowManagerService protos (archived) *(resolved — the copies now agree)*.

## CORRECTED 2026-10-04 — this card described `main`, which this platform does not ship

Read previously at `main`/`8b6c121` (16 Jul 2025). The registry had been seeded by recording
whichever branch each local working copy happened to be sitting on, and this one was a year
old. The KB now indexes from a bare mirror at the latest `release/vX.Y.Z`.

| previous claim | at `release/v1.3.6` |
|---|---|
| "the *entire* dispatch table is one `if`" (`PAUSE_RESUME_APP` only) | nine branches |
| "does **not** serve any gRPC endpoint at this commit" | serves two rpcs on 50051 with reflection |
| `ports: []`, 50051 published but unused | `ports: [50051]`, served |
| three threads from `main` | five, plus a gRPC server the main thread blocks on |
| scope = "the signal-plane subsystem", pause/resume only | nine deployment actions |
| `TERMINATE_WORKFLOW` and `FbWorkerPodScaleDown` are dropped | both handled |
| four config blobs | five |
| outbound stubs: deployment-mgmt, AKST | plus workflow-ops and event-store |

**Relevant runbooks:** none written yet.

---
type: api
name: Monitoring Service gRPC API
subsystem: monitoring-service
repo_path: e5-monitoring-service
branch: release/v1.5.9
commit: ac057d4
transport: grpc
port: 50051
tls: false
auth: none
reflection: false
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.5.6
deployment_lag: release/v1.5.9 available, v1.5.6 running
prose_reviewed_at_commit: 57a18e2
---

# Monitoring Service gRPC API

A single servicer on a hardcoded port. `ServerBuilder.forPort(50051)` with exactly one `addService` (`src/main/java/com/e5/grpc/GrpcServer.java:23-25`); the port is **not** taken from `monitor.rpcServers[].servicePort`, and there is no TLS, no interceptor, no health service and no reflection.

## service `MonitoringService` — `com.e5.platform.ss.proto`
Proto `src/main/proto/monitor.proto:53-55`; impl `src/main/java/com/e5/service/MonitoringServiceImpl.java:63`.

| rpc | request → response | impl | behaviour |
|---|---|---|---|
| `reInitiateUpdateWfStatusJob` | `Empty` → `Empty` | `MonitoringServiceImpl.java:480` | closes the `UPDATE_WF_STATUS` Kafka stream and re-schedules it at delay 0, then echoes the request back. Never fails: no try/catch, so a `closeStream` or scheduler error surfaces as `UNKNOWN` to the caller. Called only by workflow-ops after a deployment pipeline completes (`workflow-ops/src/helpers/pipeline_webhook.py:69`). |

That is the entire served surface — one unary RPC with an empty request and an empty response. Everything else this service does is driven by its scheduler, its AKST virtual queue and its Kafka streams, not by gRPC.

**Also declared in this repo but served elsewhere** (client-side protos, listed here so the collision is findable):
- `monitor.proto:57-59` `WorkflowManagerService.completeWorkflow(WorkflowCompletionRequest) → WorkflowCompletionResponse` — called at `src/main/java/com/e5/client/MonitoringClientImpl.java:157`; **no such rpc exists** in the workflow-manager repo at `main`/`8b6c121`. See proto collision (archived).
- `monitor.proto:61-63` `EventStoreService.sendEvent` — served by the event-store subsystem.
- `src/main/proto/workflowOps.proto:12-16` `WorkflowOps` — a **two-rpc subset** of the real eight-rpc [Workflow Ops API](workflow-ops-grpc-api.md), and `DeployWorkflowVariant` here returns `SuccessResponse` where the server returns `GeneralResponse` (`request_id` is `int32` vs `string`).
- `src/main/proto/task.proto:55-59` `TaskService` — AKST; note this copy adds `healthCheck(HealthCheckRequest) → HealthCheckResponse` plus a wire-level `HealthStatus` enum (`:24-27`) that the other two repos' copies of `task.proto` do not have.

See [subsystem card](monitoring-service.md).

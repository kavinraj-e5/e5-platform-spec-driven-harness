---
type: api
name: Workflow Manager gRPC API
subsystem: workflow-manager
repo_path: platform-whole-context/subsystem-workflow-interaction-support/manager-service
branch: release/v1.4.7
commit: 5451b4e
transport: grpc
port: 50051
tls: none
auth: none
reflection: true
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.3.2
deployment_lag: release/v1.4.7 available, v1.3.2 running
prose_reviewed_at_commit: c5d421b
---

# Workflow Manager gRPC API

Served on **50051**, plaintext, no auth, with server reflection enabled.
`WorkflowManagerEngine.main` starts it at `initiateGrpcServer()`
(`src/main/java/com/e5/WorkflowManagerEngine.java:28,41-44`); the server is built at
`src/main/java/com/e5/grpc/server/WorkflowManagerGrpcServer.java:29-32` and registers
exactly one application service plus `ProtoReflectionService`.

Reflection being on and auth being off means anything that can reach the pod can
enumerate and call every RPC below. That is a property of the cluster's network policy,
not of this service.

## Served

Service `com.e5.platform.ss.proto.WorkflowManagerService`, implemented by
`src/main/java/com/e5/grpc/service/WorkflowManagerImpl.java`.

| rpc | request → response | declared | implemented |
|---|---|---|---|
| `completeWorkflow` | `WorkflowCompletionRequest{workflowRunId}` → `WorkflowCompletionResponse{workflowRunId, responseCode, responseMessage}` | `src/main/proto/workflow_manager.proto:34` | `WorkflowManagerImpl.java:14` |
| `updateWorkflowSuspended` | `UpdateWorkflowSuspendedRequest{workflow_name, client_name, variant_name, config_version, suspended, repeated schedule_names}` → `UpdateWorkflowSuspendedResponse` | `src/main/proto/workflow_manager.proto:35` | `WorkflowManagerImpl.java:25` |

## Callers

Each caller vendors its own partial copy of the service definition. Every copy declares
package `com.e5.platform.ss.proto`, so the fully-qualified service name agrees.

| caller | rpc | caller's proto | matches server? |
|---|---|---|---|
| workflow-ops `release/v1.6.6` | `updateWorkflowSuspended` | `src/proto/clients/v1/workflow_manager.proto:12` | yes — request message byte-identical |
| monitoring-service `release/v1.5.9` | `completeWorkflow` | `src/main/proto/monitor.proto:59` | yes — differs only in indentation |

Each caller's copy is a **strict subset** of the server's service. That is valid gRPC:
a client needs only its own method's signature to agree, not the whole service. The
subsetting is still worth knowing about, because nothing enforces it — the agreement
is maintained by hand across three repositories and is checked by no build step.

## Client stubs this service opens

`ProtoFactory` builds outbound stubs for `DEPLOYMENT_MANAGEMENT` and `AKST`
(`src/main/java/com/e5/grpc/e5factory/ProtoFactory.java`,
`src/main/java/com/e5/grpc/stubs/ServiceType.java`). The repo also vendors
`deployment_management.proto` (≈120 rpcs), `event_store.proto`, `task.proto` and
`workflow_ops.proto` for client use; those are **not** served here.

## CORRECTED 2026-10-04 — this card described an abandoned checkout

It was previously titled *"declared, not served"*, set `port: none`, and asserted
**"this service exposes no gRPC API at all"**, with `grep` evidence that
`ServerBuilder`/`addService` returned nothing. That was true of `main` @ `8b6c121`
(16 Jul 2025) — a year-old branch that the registry had been seeded from by reading
whatever the local working copy happened to be sitting on.

Every substantive claim in that version is now false:

| previous claim | at `release/v1.3.6` |
|---|---|
| no gRPC server exists | `WorkflowManagerGrpcServer.java` starts one on 50051 |
| `port: none` | `port: 50051`, published by `deployment-configs/dev-config.yaml:39-40` |
| the one declared rpc is `HandleWorkflowSignal` | that rpc no longer exists; the proto declares the two the callers actually use |
| both caller rpcs are **unimplemented** | both are implemented |
| three incompatible protos → `UNIMPLEMENTED` on every call | the three copies agree; server is a superset of both clients |

**Preserved as a correction note rather than deleted** because this is the clearest
example of why card provenance needed a gate. The *fact* layer had already been
re-pointed and re-indexed; the *prose* layer was hand-written and nothing regenerated
it, so the card went on asserting the opposite of the truth with commit-pinned evidence
that made it look rigorous. `tools/check_card_provenance.py` now fails the build on
exactly this condition.

Superseded: three incompatible protos (archived) ·
main behind deployed surface (archived).
See [subsystem card](workflow-manager.md).

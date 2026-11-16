---
type: api
name: Workflow Ops gRPC API
subsystem: workflow-ops
repo_path: platform-whole-context/subsystem-workflow-interaction-support/workflow-ops
branch: release/v1.7.4
commit: eb9acd0
transport: grpc
port: 50051
tls: false
auth: none
reflection: true
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.6.3
deployment_lag: release/v1.7.4 available, v1.6.3 running
prose_reviewed_at_commit: b4751c3
---

# Workflow Ops gRPC API

Three servicers are registered on one insecure port (`src/server.py:87-96`), plus `grpc.health.v1.Health` set unconditionally to `SERVING` (`src/server.py:105`) and server reflection (`:112`). There is **no authentication and no TLS** (`server.add_insecure_port`, `src/server.py:127`).

## service `WorkflowOps` — `e5.platform.ss.workflowOps.v1`
Proto `src/proto/v1/workflow_ops_v1.proto:7-16`; impl `src/services/rpc/deployment_service.py:15`.

| rpc | request → response | impl | notes |
|---|---|---|---|
| `GetWorkflowComponents` | `GetWorkflowComponentsRequest` → `ComponentsResponse` | `deployment_service.py:31` | lists pods; `options.include_env` leaks pod env vars |
| `DeployWorkflowVariant` | `DeployWorkflowRequest` → `GeneralResponse` | `deployment_service.py:17` | **stub** — returns `status_code="404"`, `message="Method not implemented"` |
| `TriggerWorkflowRun` | `TriggerWorkflowRequest` → `TriggerWorkflowResponse` | `deployment_service.py:72` | invokes per-tenant Lambda; re-`raise`s after `send_rpc_error` (`:149`) |
| `GetStateList` | `StateListRequest` → `StateListResponse` | `deployment_service.py:151` | lists state tables, minus `e5_task`/`e5_converter`/`e5_token` |
| `DeleteDeployment` | `DeleteDeploymentRequest` → `DeleteDeploymentResponse` | `deployment_service.py:341` | resolves Deployment from a pod name |
| `RestartPod` | `RestartPodRequest` → `RestartPodResponse` | `deployment_service.py:195` | defaults `wait_for_ready=True`, `timeout_seconds=300` |
| `EnableDeployment` | `EnableDeploymentRequest` → `EnableDeploymentResponse` | `deployment_service.py:235` | scale-up; requires `replica_count > 0` |
| `DisableDeployment` | `DisableDeploymentRequest` → `DisableDeploymentResponse` | `deployment_service.py:293` | scale to 0 |

## service `PipelineOps` — `e5.platform.ss.PipelineOps.v1`
Proto `src/proto/v1/pipeline_ops_v1.proto:7-12`; impl `src/services/rpc/pipeline_webhook_service.py:15`.

| rpc | request → response | impl | notes |
|---|---|---|---|
| `UpdatePipelineCompletion` | `UpdatePipelineCompletionRequest` → `WebhookResponse` | `pipeline_webhook_service.py:17` | only `pipeline_type == DEPLOYMENT` does anything; wrapped in idle-tracking |
| `CreateWorkflowTriggerCFT` | `CreateWorkflowTriggerCFTRequest` → `WebhookResponse` | `pipeline_webhook_service.py:39` | deploys CloudFormation stack `e5-{env}-{tenant}-wf-trigger-services` |
| `UpdateWorkflowTriggerCFT` | `Empty` → `UpdateWorkflowTriggerCFTResponse` | `pipeline_webhook_service.py:54` | fans out over all tenants; **returns the wrong message type on error** (`WebhookResponse`, `:69`) |
| `GetToggleDetails` | `GetToggleDetailsRequest` → `GetToggleDetailsResponse` | `pipeline_webhook_service.py:73` | |

## service `ToggleTrigger` — `e5.platform.ss.ToggleTrigger.v1`
Proto `src/proto/v1/toggle_trigger_v1.proto:7-9`; impl `src/services/rpc/toggle_trigger_service.py:22`.

| rpc | request → response | impl | notes |
|---|---|---|---|
| `UpdateToggleStatus` | `UpdateToggleStatusRequest` → `ToggleTriggerResponse` | `toggle_trigger_service.py:24` | pure pass-through to deployment-mgmt `UpdateScheduleStatusTemp`; returns `str(response)` as the message, and swallows all errors into `message="Error in update to deployment status"` |

**Error convention:** every handler calls `src/utils.py send_rpc_error(context, e)` and then (except `TriggerWorkflowRun`) returns an empty/`success=false` message, so callers see `OK`-shaped responses alongside the error status.

See [subsystem card](workflow-ops.md).

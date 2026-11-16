---
type: subsystem
name: Workflow Ops
repo: e5-platform-ss-workflow-ops
repo_path: platform-whole-context/subsystem-workflow-interaction-support/workflow-ops
branch: release/v1.7.4
commit: eb9acd0
language: python
build: poetry
deployment: workflow-ops
namespace_pattern: platform-subsystem-{env} (acts on {client}-{workflow}-{variant}-{env})
etp_roles: [task-worker, task-initiator, task-reader]
owns_schema: false
ports: [50051]
health: grpc
calls: [deployment-mgmt, monitoring-service, workflow-manager, event-store, akst]
called_by: [ss-interface, monitoring-service, devops-pipeline]
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.6.3
deployment_lag: release/v1.7.4 available, v1.6.3 running
prose_reviewed_at_commit: b4751c3
---

# Workflow Ops

The infrastructure-actuator subsystem: it is the only service that holds Kubernetes, Jenkins and AWS credentials and performs physical actions on tenant workflow deployments — **not** to be confused with [Workflow Manager](workflow-manager.md), which signals *running* workflows and never touches infrastructure.

**Responsibility (what it owns):** Kubernetes mutations (pod delete/restart, deployment scale, cronjob suspend, Job creation), Jenkins job invocation, the multi-step **safe-deployment state machine**, tenant workflow-trigger provisioning via CloudFormation, EventBridge schedule state toggling, and state-service export/import.

**Not its responsibility:** It does **not** deploy workflow variants (`DeployWorkflowVariant` is a stub returning `404`/"Method not implemented", `src/services/rpc/deployment_service.py:17-24`); Jenkins does the deploying. It does not create EventBridge schedules (only `enable`/`disable`, `src/libs/aws/eventbridge_schedule.py:62-65`). It does not pause/resume running workflows — it delegates that to Workflow Manager. See remit disagreement (archived).

**Entry points:** `src/server.py:153` (`__main__`) → `start_grpc_server()` `src/server.py:115`; task poller thread `src/server.py:135`; Kafka consumer thread `src/server.py:141`.

**Inbound:**
- **AKST virtual queue `e5_platform_workflow_ops_vq`** — env-injected via `TASK_VIRTUAL_QUEUE` (`src/polling_service.py:79`), and the value *is* committed in `deployment-configs/dev-config.yaml:39-40` (all four envs). Poller loop: `src/polling_service.py:122`.
- Accepted fbNames — sync dispatch `src/polling_service.py:100-111`: `RestartWorkflow`, `EXPORT_STATE`, `IMPORT_STATE`, `UPDATE_SCHEDULE_STATE`, `HOLD_OR_RESUME_WF_TRIGGER`; async dispatch `src/polling_service.py:133-147` → `DEPLOYMENT_ACTION`, `WORKFLOW_RESOURCE_PROVISIONING` (handled in `src/async_service.py:106,111`). Anything else is logged and dropped (`src/polling_service.py:111`).
- Kafka: `CONSUMER_SERVICE_INBOUND_TOPICS` (task-manager response + own topic), filtered on `namespace == "e5-platform-ss-workflowops"` + `command == TaskComplete` (`src/consumer_service.py:114-118`).
- gRPC: `WorkflowOps`, `PipelineOps`, `ToggleTrigger` — see [API](workflow-ops-grpc-api.md).

**Outbound:** 23 distinct deployment-mgmt RPCs (`GetOpsTaskState`/`insertOpsTaskState`/`UpdateOpsTaskState`/`GetActiveOpsTaskStateDetails`, `GetDeployedVersionDetails`, `GetFbWorkerPodDetails`, `updateDeploymentDown`, `getQueuedWorkflowRunsForDeployment`, `ValidateDeploymentVersionsActive`, `UpdateScheduleStatusTemp`, …); `EventStoreService.sendEvent` (`src/helpers/async_workflow.py:969`); `MonitoringService.reInitiateUpdateWfStatusJob` (`src/helpers/pipeline_webhook.py:69`); `WorkflowManagerService.updateWorkflowSuspended` (`src/helpers/workflow.py:553`). Kafka producer → `TASK_MANAGER_INBOUND_TOPIC` with `command: TaskCreate` routed to **`wfmanager_service_vq`** (hardcoded, `src/helpers/async_task_manager.py:70` and `:153`) carrying fbNames `PAUSE_RESUME_APP` and `FbWorkerPodScaleDown`. AWS: `ssm`, `lambda` (`e5_{env}_{tenant}_platform_wf-trigger`, `src/helpers/workflow.py:240`), `scheduler`, `cloudformation`, `s3`, `dynamodb`.

**Data owned:** none — safe-deployment state lives in deployment-mgmt (`OpsTaskState`). It *reads and writes* the per-tenant state-service Postgres, see [state-service schema](../tables/state-service-workflow-schema.md).

**Config keys:** `ENVIRONMENT`, `PORT`, `DEPLOYMENT_MANAMGENT_SERVICE` (sic), `JENKINS_API_URL`, `JENKINS_USERNAME`, `JENKINS_API_TOKEN`, `KUBE_API_SERVER`, `KUBE_API_TOKEN`, `ENABLE_POLLING`, `POLLING_INTERVAL`, `KAFKA_BOOTSTRAP_SERVER`, `TASK_MANAGER_SERVICE`, `TASK_VIRTUAL_QUEUE`, `TASK_MANAGER_INBOUND_TOPIC`, `ECR_REGISTRY`, `MONITORING_SERVICE`, `WORKFLOW_MANAGER_SERVICE`, `EVENT_STORE_SERVICE`, `CONSUMER_SERVICE_GROUP_ID`, `CONSUMER_SERVICE_INBOUND_TOPICS`, `DEPLOYMENT_JENKINS_JOB_NAME`, `DEPLOYMENT_CONFIG_REPO`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `DISABLE_SSL_WARNING`, `ENABLE_FILE_LOGGING`, `HEALTH_CHECK_PORT`. Blank (secret-injected) in committed config: `ENVIRONMENT`, `JENKINS_USERNAME`, `JENKINS_API_TOKEN`, `KUBE_API_SERVER`, `KUBE_API_TOKEN`, `KAFKA_BOOTSTRAP_SERVER`, `ECR_REGISTRY`, all three `AWS_*`. Mandatory-env gate: `src/server.py:58-79` — note it omits `KUBE_API_TOKEN` and `WORKFLOW_MANAGER_SERVICE`.

**ETP role:** **task-worker** on `e5_platform_workflow_ops_vq`; **task-initiator** onto `wfmanager_service_vq`; **task-reader** of task-completion responses off Kafka. It is the only one of the three that plays all three roles.

**Failure modes:** Kafka consumer gives up permanently after `MAX_RETRIES=3` and `break`s (`src/consumer_service.py:150-152`) while the gRPC server keeps serving — silent loss of all task-completion callbacks. Async deployment coroutines are scheduled with `asyncio.run_coroutine_threadsafe` and the `Future` is never awaited (`src/polling_service.py:141`), as are sync tasks via `executor_pool.submit` (`src/polling_service.py:153`) — raised exceptions vanish. If `get_task_state` fails before `task_metadata` is populated, the failure handler cannot run and the AKST task is never completed (`src/services/async_workflow_service.py:98-102`, failure handler `:353-375`) — permanently stuck. Poller swallows every exception and backs off after 5 consecutive errors (`src/polling_service.py:160-172`).

**Code-level landmarks:** `src/polling_service.py:100-111` (sync fbName dispatch) · `src/polling_service.py:133` (async split) · `src/services/async_workflow_service.py:94-331` (safe-deployment waterfall, 14 states) · `src/helpers/async_task_manager.py:47-80` (outbound task onto `wfmanager_service_vq`) · `src/libs/kubernetes_api.py:27-45` (K8s client bootstrap) · `src/constants.py:8-16` (task-type constants).

**Known divergences:** remit overstated (archived) · K8s TLS verification disabled (archived) · three incompatible WorkflowManagerService protos (archived) · `pyproject.toml:4` declares version `1.5.1` on branch `release/v1.6.3` with image tag `v1.6.3-…`.

**Relevant runbooks:** none written yet.

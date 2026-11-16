---
type: subsystem
name: Interface
repo: e5-platform-ss-interface
repo_path: platform-whole-context/subsystem-workflow-interaction-support/interface
branch: release/v1.7.6
commit: d663d62
language: java
build: gradle
deployment: interface-ss-pl-subsystem
namespace_pattern: platform-subsystem-{env}
etp_roles: [task-initiator]
owns_schema: false
ports: [50051]
health: http /app/health
calls: [deployment-mgmt, workflow-ops, task-manager]
called_by: [neos, automation-hub]
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.6.8
deployment_lag: release/v1.7.6 available, v1.6.8 running
prose_reviewed_at_commit: 83c3373
---

# Interface

The GraphQL façade and **the only front door for external and UI-driven actions** — Neos and
Automation Hub call this, never a subsystem directly.

**Responsibility (what it owns):** Translating an external request into a platform task. It
validates, mints a `taskId`, builds the transport-protocol-v2 envelope, and dispatches. It also
serves read queries by fanning out to deployment-mgmt and workflow-ops over gRPC.

**Not its responsibility:** Executing anything. 11 of its 17 mutations complete *asynchronously*
— the caller polls `taskStatus` for the result. It owns no schema.

**Entry points:** `TaskController`, `WebhookController`, `ConfigController`,
`DeploymentController`, `WorkflowController`, `ScaleAppWorkerController`,
`CredentialReleaseController`, `InfraOpsController`, `EventTypeController`, `WorkflowRunController`.

**Inbound:** GraphQL `POST /v1/api` — **8 Query, 17 Mutation, 0 Subscription**. See
[interface-graphql-api](interface-graphql-api.md). Plus a Kafka Streams topology
consuming `${TASK_MANAGER_OUTBOUND_TOPIC}` to correlate async responses.

**Outbound:**
- gRPC → `DeploymentManagementServiceBlockingStub` (22 of the proto's 31 RPCs) and
  `WorkflowOpsBlockingStub` (`GrpcChannelConfig.java:32-56`)
- Kafka → `${TASK_MANAGER_INBOUND_TOPIC}` (`KafkaProducerService.java:21-24`)
- AKST virtual queues → `workflowManagerVQ`, `workflowOpsVQ`, `monitoringVQ`, plus one
  hardcoded `e5_platform_workflow_ops_vq` (`InfraOpsServiceImpl.java:140`)

**Data owned:** none. Async task state lives in deployment-mgmt and is read back via
`GetTaskStatus`.

**Config keys:** a topic config key ← `TASK_MANAGER_INBOUND_TOPIC`;
a topic config key ← `TASK_MANAGER_OUTBOUND_TOPIC`;
`e5.platform.interface.queue.task-{monitoring,workflowmanager,workflowops}-outbound` ←
`INTERFACE_TASK_*_OUTBOUND_QUEUE` — **all three VQ names are blank in every committed
deployment-config**; `KAFKA_STREAMS_APPLICATION_ID`; `MANAGEMENT_SERVICE_HOST`.
Values are never recorded here.

**ETP role:** Task Initiator. It never plays Router or Queue.

**The dispatch pattern — the thing to understand first.** Every async mutation does: gRPC
`PerformTask` (mint `taskId`) → optional gRPC validation guard → Kafka produce → AKST → VQ →
consumer. **Neither leg is transactional.** Routing is decided by **two independent switch
statements keyed on the same unshared string**: `TaskManagerHelperImpl.java:125-168` sets
`taskType` and `fbName`; `TaskManagerProtocolBuilder.java:83-87` sets `vqName`. Neither has a
`default:` branch.

**Failure modes:**
- Kafka failure after a successful `PerformTask` orphans a DB task row; the exception is
  swallowed and an empty DTO returned (`TaskManagerHelperImpl.java:109-111`)
- `auto-offset-reset: latest` with no replay — a completion arriving while the pod is down is
  **permanently lost**; `taskStatus` then polls a 202 forever
- Adding a task type to one switch and not the other yields a silent `null` VQ

**Code-level landmarks:**
- `TaskManagerHelperImpl.java:119-173` — `updateTaskInfo`, the taskType/fbName table
- `TaskManagerProtocolBuilder.java:80-94` — `getDestinationEndpoint`, the VQ table
- `KafkaStreamService.java:52-74` — response correlation topology
- `TaskServiceImpl.java:91-412` — the 10-branch `taskStatus` response-shape dispatch
- `TaskServiceImpl.java:428-445` — `updateTaskResponse` write-back

**Known divergences:** v2-envelope-uses-v1-correlation-field (archived) ·
unsynchronised-routing-switches (archived) ·
interface-streams-loses-completions (archived)

**Relevant runbooks:** [add-a-lifecycle-action](../runbooks/add-a-lifecycle-action.md)

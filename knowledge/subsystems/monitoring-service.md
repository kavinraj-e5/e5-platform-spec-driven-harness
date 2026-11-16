---
type: subsystem
name: Monitoring Service
repo: e5-monitoring-service
repo_path: e5-monitoring-service
branch: release/v1.5.9
commit: ac057d4
language: java
build: gradle
deployment: monitoring-service
namespace_pattern: platform-subsystem-{env}
etp_roles: [task-worker, task-initiator, task-reader]
owns_schema: false
ports: [50051]
health: none
calls: [workflow-ops, deployment-mgmt, event-store, akst, workflow-manager]
called_by: [ss-interface, workflow-ops]
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.5.6
deployment_lag: release/v1.5.9 available, v1.5.6 running
---

# Monitoring Service

The deployment-health and workflow-run-lifecycle observer: it probes a tenant deployment's Kafka/Temporal/Postgres/pods, attempts one self-heal by delegating a `RestartWorkflow` task to [Workflow Ops](workflow-ops.md), and drives workflow-run status transitions from the platform event stream.

**Responsibility (what it owns):** The `DEPLOYMENT_HEALTH_CHECK` verdict; Kafka consumer-lag detection (two samples 30s apart); Temporal server+namespace and Postgres schema probes; promoting `WORKFLOW_*`/`CONTAINER_*` events into `workflow_run` status updates in deployment-mgmt; and emitting the `e5.platform.event.workflow.{STARTED,ENDED,NOT_STARTED}` platform events.

**Not its responsibility:** It never restarts, scales or deletes anything itself — every remediation is a task delegated to Workflow Ops. It does not monitor pod credentials despite shipping `CCSCredentialMonitorConfig`, a JSON schema and a `ccsMonitorConfig` env block (never loaded (archived)).

**Entry points:** `src/main/java/com/e5/MonitoringEngine.java:10` (`main`) → `GrpcClient.startClient()` then `GrpcServer.startServer()` (`src/main/java/com/e5/grpc/GrpcServer.java:21`), which hardcodes port `50051` at `:23` and blocks on `awaitTermination()` at `:66`; four jobs are scheduled at `:49-64`.

**Inbound:**
- **AKST virtual queue `monitoring_service_vq` — HARDCODED, not configurable:** `String virtualQName = "monitoring_service_vq";` at `src/main/java/com/e5/service/MonitoringServiceImpl.java:308`. The `taskManager.queues[].queueName` config key exists, is validated by `src/main/resources/schemas/TaskManagerConfig.schema.json:28` and is shipped in every `deployment-configs/*-config.yaml:16`, but is **never read** — see [disagreement](../known-issues/monitoring-vq-hardcoded-ignores-config.md).
- Poller `MonitoringServiceImpl.java:307-340`; dispatch table `:316-322`:
  ```java
  String taskName = task.getTaskInfo().getTaskPayload().getFbName();
  switch (taskName) {
      case "HEALTH" -> { performHealthCheck(task); LoggerUtil.info("Health Task complete"); }
      default -> throw new IllegalArgumentException("Unknown TASK: " + taskName);
  }
  ```
  `HEALTH` is the only accepted fbName; its payload is `HealthCheckInputPayload{deploymentVersionId}`.
- Kafka: all event topics from `getEventsTopic` filtered to 6 event types (`MonitoringServiceImpl.java:197-203`); task-response topic `taskManager.topics.response` filtered to `namespace == "e5-monitor-service"` (hardcoded, `:401`) with fbName dispatch `RestartWorkflow` only (`:416-467`).
- gRPC: `MonitoringService.reInitiateUpdateWfStatusJob` — see [API](monitoring-service-grpc-api.md).

**Outbound:** `WorkflowOps.getWorkflowComponents` (`src/main/java/com/e5/client/MonitoringClientImpl.java:211,215,291`); deployment-mgmt `getYetToStartWorkflows`, `getDeployment`, `getWFRunListForDeploymentID`, `getWFRunRecordWithPayload`, `updateWfRunStatus`, `createTemporalWf`, `updateTemporalWfStatus`, `getEventsTopic`, `getDeploymentId`, `updateWfRunEventStatus`, `getKafkaDetails`, `getConfigValue`, `updateTaskResponse`, `performTask`; `EventStoreService.sendEvent` (`:194`); `WorkflowManagerService.completeWorkflow` (`:157`); AKST `isTaskAvailable`/`getTask` (`:198,203`). Kafka producer → `taskManager.topics.command` with `command: TaskCreate` routed to **`e5_platform_workflow_ops_vq`** (hardcoded, `src/main/java/com/e5/actions/TaskAction.java:31`) carrying fbName `RestartWorkflow`, and with `command: TaskComplete`, `taskType: E5_MONITOR`, fbName `HEALTH` for the verdict (`TaskAction.java:43-48`). Direct non-gRPC probes: Kafka AdminClient, Temporal `Health.check`/`describeNamespace`, JDBC Postgres.

**Data owned:** none — `workflow_run`, `temporal_wf_run` and task rows all live in deployment-mgmt; this service only issues update RPCs.

**Config keys:** three JSON-blob env vars loaded by `src/main/java/com/e5/config/ConfigsLoader.java:22-45`: `kafka` (`bootStrapServer`, `stream[].streamName`, `stream[].groupId`), `monitor` (`rpcServers[].serviceType|ecrServiceName|servicePort`, `jobs.timedScheduler[].name|delayInMinutes|intervalInMinutes|enabled|params.wfStartedBufferTime`, `jobs.oneTimeScheduler[].name|delayInMinutes|enabled`), `taskManager` (`topics.command`, `topics.response`, `queues[].subSystemName`, `queues[].queueName`). A fourth, `ccsMonitorConfig` (`kubeEndpoint`, `kubeApiToken`, `kubeNamespace`, `credentialEventsTopic`, `sqsQueueName`), is shipped but never loaded. Job names are matched by string literal: `pollTask`, `updateWorkflowStatus`, `taskResponseReceiver`, `wfNotStartedEvent` (`GrpcServer.java:49-64`). All values are blank/sentinel in committed config.

**ETP role:** **task-worker** on `monitoring_service_vq`; **task-initiator** onto `e5_platform_workflow_ops_vq` (self-heal delegation); **task-reader** of the delegated task's response off Kafka. The delegation→response→completion triangle is the only multi-hop ETP flow among the three control-plane services.

**Failure modes:** `probeKafkaLag` initialises `laggedTopics = null` (`src/main/java/com/e5/service/monitors/HealthMonitor.java:172`), swallows every exception (`:229`) and returns that `null`, which the caller treats as *no lag* (`MonitoringServiceImpl.java:382`) — **a Kafka lag check that fails to execute reports HEALTHY**. This is confirmed in the deployed `release/v1.5.6` as well as in latest ([monitoring-health-check-fails-open](../known-issues/monitoring-health-check-fails-open.md)). An unknown fbName throws `IllegalArgumentException` from the dispatch `default` arm (`MonitoringServiceImpl.java:346`), caught and logged at info after `getTask` has already dequeued it — task lost, no completion event. `getAllEventsTopic` swallows the RPC error then dereferences the null result (`MonitoringClientImpl.java:188`) → NPE. `pollForTask`'s `while(true)` occupies a `startOnsShotScheduler` slot in a fixed thread pool forever (`GrpcServer.java:49`).

**Code-level landmarks:** `MonitoringServiceImpl.java:307-340` (VQ poller + dispatch) · `MonitoringServiceImpl.java:342-396` (`performHealthCheck`, the whole verdict logic) · `MonitoringServiceImpl.java:398-473` (`resolveTask`, self-heal verification) · `actions/TaskAction.java:27-50` (delegate + complete) · `service/monitors/HealthMonitor.java:141-218` (two-sample lag check) · `src/main/proto/task.proto:24-27` (wire `HealthStatus` enum).

**Known divergences:** HealthStatus cannot express "check failed" vs "self-heal failed" (archived) · [VQ hardcoded, config ignored](../known-issues/monitoring-vq-hardcoded-ignores-config.md) · ccsMonitorConfig never loaded (archived) · three incompatible WorkflowManagerService protos (archived).

**Caveat:** read from `release/v1.5.9`/`ac057d4` (latest release). QA runs `v1.5.6`; the failure modes above were re-read at that tag too and are unchanged. Treat every line number as commit-pinned to `v1.5.9`.

**Relevant runbooks:** none written yet.

## CORRECTED 2026-10-04 — one failure mode was fixed before QA shipped

This card previously stated:

> `isServicesHealthy` (pod health) is computed at `MonitoringServiceImpl.java:361` and never
> used in the verdict at `:365`.

True at `release/v1.4.4`/`57a18e2`, which the KB was indexing because the registry had been
seeded from a stale working copy. At `release/v1.5.9:395` — and at the **deployed**
`release/v1.5.6` — the verdict is:

```java
if (isKafkaHealthy && isTopicExist && restartServerList.isEmpty()
    && isTemporalHealthy && isPostgresHealthy && isServicesHealthy) {
```

Both `isServicesHealthy` and a new `isTopicExist` now gate the healthy verdict. The pod sweep
counts. Withdrawn.

The Kafka-lag fail-open above is **not** withdrawn — it survives unchanged at both commits.

---
type: contract
name: DEPLOYMENT_HEALTH_CHECK task contract
scope: interface → monitoring-service → workflow-ops → monitoring-service
provenance: code
verified: 2026-10-02
---

# `DEPLOYMENT_HEALTH_CHECK` / `HEALTH` task contract

The only self-healing loop in the platform, and the only task that fans out to a second subsystem before completing.

## Request

Produced by the interface as taskName `DEPLOYMENT_HEALTH_CHECK` → `taskType: E5_MONITOR`, `fbName: HEALTH`, routed to `monitoringVQ` (`TaskManagerHelperImpl.java:132-135`, `TaskManagerProtocolBuilder.java:85`).

`fbPayload` is deserialised into `HealthCheckInputPayload` — **one field**, `deploymentVersionId` (`e5-monitoring-service/src/main/java/com/e5/model/health/HealthCheckInputPayload.java:14`). There is no component selector, no timeout and no severity; the caller cannot ask for a subset of checks.

## Checks performed (`MonitoringServiceImpl.java:342-396`)

| order | component | probe | failure semantics |
|---|---|---|---|
| 1 | `KAFKA_SERVER` | AdminClient `listTopics` | returns `false` on any exception (`HealthAction.java:56-58`) |
| 2 | a topic env var | two offset samples 30 s apart | returns **`null`** on *both* "no lag" and "probe threw" (`HealthMonitor.java:152,214-217`) |
| 3 | `TEMPORAL` | gRPC `Health.check` + `describeNamespace` | `false` on exception |
| 4 | `POSTGRES` | JDBC `has_schema_privilege` query | `false` on exception |
| 5 | `PLATFORM_SERVICE` | HTTP `/health` per pod via workflow-ops `getWorkflowComponents` | computed at `:361` and **never used in the verdict at `:365`** |

Verdict predicate (`:365`): `isKafkaHealthy && restartServerList.isEmpty() && isTemporalHealthy && isPostgresHealthy`.

## Response

`HealthCheckResponse{deploymentVersionId, message}` — a free-text `message`, not an enum. Four possible strings:

| string | meaning | site |
|---|---|---|
| `"The Deployment is Healthy"` | all four probes passed | `:368` (inline literal) |
| `"The Deployment is Unhealthy"` | at least one probe failed, **or** self-heal did not fix the lag, **or** self-heal returned non-SUCCESS | `:386`, `:418`, `:435` |
| `"Health request failed."` | the check itself threw | `:393` |
| `"The Deployment is inactive or not available"` | declared at `:67`, **never emitted** | — |

See HealthStatus enum disagreement (archived) for why these four strings and the two-valued `HealthStatus` enum cannot express the states the loop actually reaches.

## Self-heal branch

If Kafka is up but topics are lagging, the task is **not** completed. Instead a child task is delegated: `fbName: RestartWorkflow`, `fbPayload{requestId, services[], deploymentVersionId}`, `taskType: "RestartWorkflow"`, routed to `e5_platform_workflow_ops_vq` (`TaskAction.java:27-39`). The original `taskID` is smuggled through `metaInfo.userMetaInfo.temporalToken` as a JSON `Tokens{requestID, taskID}` blob (`TaskBuilder.java:68-75`) — a token field reused as a correlation id.

workflow-ops restarts the pods and emits its own `TaskComplete`. monitoring-service picks that up on the response topic, re-probes Kafka lag, and only then completes the *original* task using `tokens.getTaskID()` (`MonitoringServiceImpl.java:441-442`). If `temporalToken` is unparseable the completion is skipped inside a swallowing catch (`:443-444`) and the original task is orphaned.

Related: [task routing](../flows/task-names.yaml) · [TaskComplete envelope](task-completion-envelope.md) · [ETP v2 envelope](transport-task-protocol-v2.md).

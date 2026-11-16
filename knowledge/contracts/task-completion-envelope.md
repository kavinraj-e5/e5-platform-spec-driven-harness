---
type: contract
name: TaskComplete envelope (control-plane producers)
scope: workflow-ops, workflow-manager, monitoring-service → task-manager command topic
provenance: code
verified: 2026-10-02
---

# TaskComplete envelope as emitted by the control plane

All three operational services close an AKST task the same way — by producing a `TaskTransport`-shaped JSON message to a Kafka topic, never by a gRPC call back to AKST. The three implementations are independent and **disagree on two fields**.

## Common shape

```
version: "v2"                      // TaskProtocol.v2
command: "TaskComplete"
taskInfo.taskId
taskInfo.taskType
taskInfo.namespace
taskInfo.responseEventType: "COMPLETE_DELEGATION_ACTION"
taskInfo.taskPayload.fbName
taskInfo.taskPayload.fbPayload      // handler result + injected "status"
```

## Per-service values

| field | workflow-ops | workflow-manager | monitoring-service |
|---|---|---|---|
| target topic | `TASK_MANAGER_INBOUND_TOPIC` env | `workflowManagerTopics.taskManagerInboundEndPoint` | `taskManager.topics.command` |
| `taskType` | `"WorkflowOps"` (`constants.py:93`) | `"E5_WF_MANAGER"` (`WorkflowResponseConsumer.java:99`) | `"E5_MONITOR"` (`TaskAction.java:44`) |
| `namespace` | `"e5-platform-ss-workflowops"` (`constants.py:54`) | `"e5-workflow-manager-service"` (`WorkflowResponseConsumer.java:102`) | `"e5-monitor-service"` (`TaskBuilder.java:57`) |
| `fbName` | echoes the inbound fbName | `"WF_MANAGER"` — **not** the inbound `PAUSE_RESUME_APP` (`WorkflowResponseConsumer.java:103`) | `"HEALTH"` always, even for the restart-verification path (`TaskAction.java:43`) |
| `taskId` type | string | `Long.parseLong(referenceId)` (`WorkflowResponseConsumer.java:98`) | `Long.parseLong(taskID)` (`TaskBuilder.java:55`) |
| `status` in fbPayload | `"SUCCESS"` / `"FAILURE"`, plus `message` when `status == "failed"` (`async_task_manager.py:195-199`) | none — payload is the raw `AppSignalPayload` | none — payload is `{deploymentVersionId, message}` |

Two consequences worth naming. (1) Both Java services parse `taskId` with `Long.parseLong`, so a non-numeric task id raises `NumberFormatException` inside a swallowing catch and the completion is never emitted. (2) workflow-ops writes the status word in two cases — `constants.TASK_SUCCESS = "SUCCESS"` / `TASK_FAILURE = "FAILURE"` (`constants.py:94-95`) — but the failure-message branch tests the lowercase literal `"failed"` (`async_task_manager.py:196`), so `message` is never attached in practice.

## Who reads it back

- workflow-ops consumes the *response* topic and re-enters its state machine on `command == TaskComplete` + `namespace == "e5-platform-ss-workflowops"` + fbName `DEPLOYMENT_ACTION` (`consumer_service.py:114-118`).
- monitoring-service consumes `taskManager.topics.response` filtered on `namespace == "e5-monitor-service"` and fbName `RestartWorkflow` (`MonitoringServiceImpl.java:401,417`).
- workflow-manager consumes only app-signal events, never task responses.

Related: [task routing](../flows/task-names.yaml) · [health-check task](health-check-task.md) · [ETP v2 envelope](transport-task-protocol-v2.md).

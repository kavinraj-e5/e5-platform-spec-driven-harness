---
type: disagreement
severity: medium
subject: monitoring-service hardcodes its virtual queue name while shipping, schema-validating and ignoring a queues config block
sources:
  - code: e5-monitoring-service src/main/java/com/e5/service/MonitoringServiceImpl.java:308
  - code: e5-monitoring-service src/main/java/com/e5/config/TaskManagerQueues.java:14-15
  - schema: e5-monitoring-service src/main/resources/schemas/TaskManagerConfig.schema.json:28-35
  - config: e5-monitoring-service deployment-configs/{dev,qa,prod,test}-config.yaml:15-16
verified: 2026-10-02
---

# The queue name is hardcoded and the config that names it is ignored

```java
// MonitoringServiceImpl.java:307-308
public void pollForTask() {
    String virtualQName = "monitoring_service_vq";
```

Meanwhile the config model has a purpose-built type for exactly this:

```java
// config/TaskManagerQueues.java:12-15
public class TaskManagerQueues {
    private String subSystemName;
    private String queueName;
}
```

`taskManager.queues[]` is reachable through `TaskManagerConfig.getQueues()` (`config/TaskManagerConfig.java:18`), is a **required** property in the shipped JSON schema (`schemas/TaskManagerConfig.schema.json:28-35`), is populated with two entries in every environment's deployment config (`deployment-configs/dev-config.yaml:16`), and is loaded and validated at startup (`config/ConfigsLoader.java:66-70`). `grep -rn "getQueues" src/` returns **nothing**. The parsed value is unreachable dead state.

## Why it matters beyond tidiness

1. The queue name cannot be changed per environment. Every env — dev, qa, test, prod — polls the single literal `monitoring_service_vq`, so a shared AKST cluster cannot isolate environments by queue, and the interface's `e5.platform.interface.queue.task-monitoring-outbound` property is only coincidentally correct.
2. Operators reading `deployment-configs/*-config.yaml` will reasonably conclude the queue is configurable and that editing `queues[].queueName` changes behaviour. It does not.
3. It contrasts with both siblings: [workflow-ops](../subsystems/workflow-ops.md) reads `TASK_VIRTUAL_QUEUE` from the env (`src/polling_service.py:79`) and [workflow-manager](../subsystems/workflow-manager.md) reads `workflowManagerRpcInfo.taskVirtualQueueName` (`WorkflowManagerTaskHandler.java:91`). Three consumers of the same transport, three different mechanisms, one of which is not a mechanism.

The same literal also appears in `platform-whole-context/subsystem-workflow-interaction-support/e5-platform-ss-credential-monitor/src/main/java/com/e5/service/MonitoringServiceImpl.java:200` — a second service polling the *same* queue with its own `HEALTH` branch, which means two processes may race for the same tasks. That is a separate finding and needs owner confirmation.

A second hardcode of the same kind lives in the outbound direction: the self-heal delegation target `"e5_platform_workflow_ops_vq"` at `actions/TaskAction.java:31`.

## Resolution

Read `taskManager.queues[]` keyed on `subSystemName`, or delete the type and the schema requirement so the config stops lying.

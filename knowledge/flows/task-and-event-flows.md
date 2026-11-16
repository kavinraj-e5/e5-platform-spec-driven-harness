---
type: flows
name: Task and event flows - who produces, who consumes, and what they do with it
scope: cross-repo (24 repos); no single branch or commit
provenance: authored from code reading; see "Evidence" on each row
verified: 2026-10-13
supersedes: extracted virtual-queue and Kafka topic bindings (deleted 2026-10-13)
---

# Task and event flows

**This document is authored on purpose. It replaces an extractor that could not be
right.**

A topic or queue NAME in this platform is injected by the devops pipeline at deploy time.
Every service is written to read whatever value its config hands it, so a name found in a
repository is not a fact about the platform. Measured on `workflow-manager` before
deleting the extractors:

| where | what it says the workflow-manager queue is called |
|---|---|
| `deployment-configs/qa-config.yaml` (committed) | `"value"` — the literal word |
| `.env` | `wmq` |
| `dynamic-scaling/application.yaml` Spring default, same env var | `workflow_manager_vq` |
| **qa cluster, observed 2026-10-06** | **`wfmanager_service_vq`** |

One queue, four names, three fictional. Across the estate, 7 of 10 queue literals in
source named a queue the platform does not run, and 31% of the facts came from JUnit
fixtures (`any_vq`, `other_VQ`, `tenant_app_VQ`). Kafka is no better, and one level
worse: **each repo has its own config-key namespace**, so even the key cannot join two
repos. The same channel is a topic config key to `interface`,
`akstService.akstCmdQEndpoint` to `task-manager`, and
`workflowManagerTopics.taskManagerInboundEndPoint` to `workflow-manager`.

So the flows below are written down by a human, from reading both sides of the code, and
the knowledge base is updated when they change. `./e5 async report` prints the machine
readable half; `knowledge/_reference/joins/task-names.yaml` backs the task plane and
`./e5 check` verifies it against both routing switches.

## How to read a row

- **The task name or payload type is the contract**, never the queue or topic name. Two
  repos compare the same string in code; that is what makes a row a fact.
- **Evidence is two-sided.** A row says where the producer sets it and where the consumer
  dispatches on it. Both are `path:line` you can check with `./e5 cite check`.
- **A queue or topic name, where mentioned, is parenthetical and dated.** It is what qa
  happened to be running on 2026-10-06. It is not stored as a fact anywhere.

---

## 1. Task plane — AKST virtual queues

A virtual queue is **the address of a consumer**. One queue per service; the queue names
the thing that polls it. **A producer does not have a queue.**
`TaskRoutingEndpoints.getDestinationVqName` (platform-core
`akst/routing/TaskRoutingEndpoints.java:62`) reads

```java
metaInfo.routingPath.routingEndpoints["destination"].config["vqName"]
```

so the destination travels **inside the task envelope**, set per message by whoever
created the task. That is why no amount of code reading finds "X produces to queue Q",
and why the rows below are keyed on the task name instead.

The chain is always the same shape:

> **producer** builds a `TaskTransport` naming a `taskName` →
> AKST (`task-manager`, the subsystem-plane AKST deployment) holds it on the destination's
> virtual queue →
> **consumer** polls its own queue with `getTask(TaskAvailableRequest)` and dispatches on
> `taskInfo.taskPayload.fbName`

Producer side: `interface` selects the destination in a switch at
`taskmanager/protocol/TaskManagerProtocolBuilder.java:84-87`, writing a field
(`workflowManagerVQ`, `monitoringVQ`, `workflowOpsVQ`) into the envelope. The **field
name** is definite — a developer wrote it; its **value** is injected.

Consumer side: `workflow-manager` dispatches at
`grpc/WorkflowManagerTaskHandler.java:98-124`. The two sides name the same task, which is
the proof.

| producer | taskName | taskType | fbName (consumer dispatches on this) | consumer | what the consumer does |
|---|---|---|---|---|---|
| interface | `PAUSE_OR_RESUME_APP` | `E5_WF_MANAGER` | `PAUSE_RESUME_APP` | workflow-manager | signals the Temporal workflow to pause or resume, then updates app status |
| interface | `TERMINATE_WORKFLOW` | `E5_WF_MANAGER` | `TERMINATE_WORKFLOW` | workflow-manager | terminates the running workflow |
| interface | `SOFT_TERMINATE_WORKFLOW` | `E5_WF_MANAGER` | `SOFT_TERMINATE_WORKFLOW` | workflow-manager | lets in-flight work drain, then terminates |
| interface | `CREDENTIAL_RELEASE` | `E5_WF_MANAGER` | `CREDENTIAL_RELEASE` | workflow-manager | releases held credentials |
| interface | `FB_WORKER_POD_SCALE_APP_WORKER` | `fb-pod-scale-v2` | `FB_WORKER_POD_SCALE_APP_WORKER` | workflow-manager | scales the app-worker pods |
| dynamic-scaling | `ENABLE_DEPLOYMENT` | `E5_WF_MANAGER` | `ENABLE_DEPLOYMENT` | workflow-manager | enables the deployment |
| dynamic-scaling | `DISABLE_DEPLOYMENT` | `E5_WF_MANAGER` | `DISABLE_DEPLOYMENT` | workflow-manager | disables the deployment |
| workflow-manager | `SCALE_DOWN` | `fb-pod-scale-down` | `FbWorkerScaleDown` | fb-worker | scales itself down |
| interface | `DEPLOYMENT_HEALTH_CHECK` | `E5_MONITOR` | `HEALTH` | monitoring-service | runs the deployment health check and completes the delegation |
| interface | `DEPLOYMENT_ACTION` | `E5_WORKFLOW_OPS` | `DEPLOYMENT_ACTION` | workflow-ops | performs the deployment action |
| interface | `UPDATE_DEPLOYMENT_SCHEDULE_STATE` | `E5_WORKFLOW_OPS` | `UPDATE_SCHEDULE_STATE` | workflow-ops | updates the schedule state |
| interface | `EXPORT_STATE` | `E5_WORKFLOW_OPS` | `EXPORT_STATE` | workflow-ops | exports workflow state |
| interface | `IMPORT_STATE` | `E5_WORKFLOW_OPS` | `IMPORT_STATE` | workflow-ops | imports workflow state |
| interface | `HOLD_OR_RESUME_WF_TRIGGER` | `E5_WORKFLOW_OPS` | `HOLD_OR_RESUME_WF_TRIGGER` | workflow-ops | holds or resumes the workflow trigger |
| interface | `WORKFLOW_RESOURCE_PROVISIONING` | `E5_WORKFLOW_OPS` | `WORKFLOW_RESOURCE_PROVISIONING` | workflow-ops | provisions workflow resources |

Consumer poll sites, all `path:line` checkable:

| consumer | polls with | at |
|---|---|---|
| workflow-ops | env `TASK_VIRTUAL_QUEUE` | `src/polling_service.py:81` |
| workflow-manager | config `task.virtual.queue.name` | `src/main/java/com/e5/grpc/WorkflowManagerTaskHandler.java:91` |
| fb-worker | env `VIRTUAL_QNAME` (own) and `PLATFORM_VIRTUAL_QNAME` (platform) | rf-fb-worker-core `conf/settings.py:23,27` → `api/keywords.py:659,514` |
| monitoring-service | **hardcoded** `"monitoring_service_vq"` | `src/main/java/com/e5/service/MonitoringServiceImpl.java:333` → `getTask` at `:339` |
| credential-monitor | **hardcoded** `"monitoring_service_vq"` | `src/main/java/com/e5/service/MonitoringServiceImpl.java:200` → `getTask` at `:205` |

> ### DEFECT: competing consumers on one virtual queue
> `monitoring-service` and `credential-monitor` **both** hardcode
> `"monitoring_service_vq"` and **both** call `getTask` on it. They are competing
> consumers: whichever polls first takes the task, so a health check can be handled by
> the wrong service. Neither reads a config key, so this cannot be fixed by deployment.

> ### Also a defect: queue names hardcoded in production source
> Six production sites hardcode a queue name instead of reading config, which defeats the
> per-environment injection the rest of the platform relies on: credential-monitor
> `health/action/TaskHandler.java:84` and `service/MonitoringServiceImpl.java:200`,
> interface `grpc/impl/InfraOpsServiceImpl.java:140`, monitoring-service
> `actions/TaskAction.java:31` and `service/MonitoringServiceImpl.java:333`, workflow-ops
> `src/helpers/async_task_manager.py` (workflow-manager's queue, hardcoded in a *different*
> service).

---

## 2. Event plane — Kafka

**The payload type is the contract, not the topic.** Each repo names the topic with its
own config key, so the key cannot join two repos; the envelope class can.

### 2.1 Audit events — webhook-service → audit-service

- **Producer** webhook-service `integration/audit/AuditKafkaProducer.java:77`
  `publish(AuditEvent)` wraps the event in a **CloudEvents** envelope
  (`CloudEventAuditEvent`, built at `:120`) and sends at `:94`. Topic read from
  `auditProperties.getTopic()` at `:69`.
- **Consumer** audit-service `CloudEventsAuditConsumer.java:90`,
  `@KafkaListener(topics = "${a topic config key:the audit-event topic}")`, consume
  method at `:95`. Deserialises as String and parses JSON in the consumer
  (`KafkaConsumerConfig.java:65`).
- **Consumer does**: persists the audit event; a failure goes to the dead-letter path at
  `consumer/AuditEventDeadLetterRecoverer.java:97`.
- **Evidence the two are one channel**: the producer builds `CloudEventAuditEvent`, the
  consumer class is `CloudEventsAuditConsumer`. The envelope matches; the topic names do
  not have to.
- *Note*: the consumer's Spring default is the audit-event topic; qa actually runs
  the QA audit topic (observed 2026-10-06). The default is stale, which is exactly why
  defaults are not facts.
- **workflow-api also produces audit events** (`integration/.../KafkaProducerConfig.java`,
  and it declares a topic env var like the other two) — *one-sided evidence,
  needs a reader to confirm the envelope.*

### 2.2 Workflow status updates → workflow-api

- **Consumer** workflow-api `inbound/consumer/StatusUpdateConsumer.java:41`,
  `@KafkaListener(topics = "${a topic config key:e5.workflow.updates}")`,
  handler `onWorkflowUpdate` at `:46`.
- **Producer**: *not established.* workflow-manager produces at
  `grpc/WorkflowManagerTaskHandler.java:1455,869` and `service/PodScaleDownService.java:168`,
  but no payload type has been matched to `onWorkflowUpdate`. **Open question — do not
  assume.**

### 2.3 Webhook requests → webhook-service

- **Consumer** webhook-service `inbound/consumer/WebhookKafkaConsumer.java:77`
  `@KafkaListener`, `consumeWebhookRequest` at `:82`; a failed destination goes to a DLQ
  at `:210`.
- **Producer**: workflow-api declares `WEBHOOK_SERVICE_TOPIC`. *One-sided; the envelope
  has not been matched.*

### 2.4 Event store chain — event-store → event-processor

- event-store produces (`kafka/config/KafkaProducerConfig.java`), event-processor consumes
  (`config/KafkaConfig.java`), and both declare an event-store topic key. The
  **@KafkaListener is absent from event-processor**, so the consume mechanism is a manual
  consumer and has not been read. *One-sided; open.*

### 2.5 AKST internal — task-manager / platform-core

- AKST forwards and notifies over Kafka: platform-core
  `akst/routing/TaskEventForwarder.java:40`, `akst/routing/RouteFailureNotifier.java:61`,
  `akst/metrics/MetricEventEmitter.java:16`, and the inbound side at
  `event/receiver/KafkaConsumer.java` / `event/receiver/Aggregator.java:89`.
- `akstService.akstCmdQEndpoint` is the command channel key, declared by both `akst` and
  `task-manager` — the one key that *is* shared, because both are the same codebase
  (platform-core) in two deployment roles.

---

## 3. What qa was running on 2026-10-06

**Dated observation. Not a fact about code, not stored in `graph/`, and already possibly
wrong.** Recorded only so a reader can connect this document to a running system.

| channel | value in qa | held by |
|---|---|---|
| workflow-ops' queue | `e5_platform_workflow_ops_vq` | workflow-ops (consumer); interface, workflow-manager, credential-monitor, monitoring-service hold the name to address it |
| workflow-manager's queue | `wfmanager_service_vq` | workflow-manager (consumer); interface, dynamic-scaling hold the name |
| monitoring queue | `monitoring_service_vq` | monitoring-service **and** credential-monitor (see defect above) |
| fb-worker queues | `Ftps-Robotframework_VQ`, `Hchb-Robotframework_VQ`, `addus-…_Ftps_VQ`, `addus-…_Hchb_VQ` | fb-worker, one per deployed instance |
| non-RPA worker queues | `Nonrpahchb-NonRPAWorker_VQ`, `addus-…_Nonrpahchb_VQ` | the non-RPA worker — **repo not mirrored, poll site unread** |
| AKST command topic | `e5_qa_task_manager_cmdq` | dynamic-scaling, fb-worker, interface, task-manager, workflow-manager, workflow-ops, non-RPA worker |
| AKST response topic | `e5_qa_task_manager_response` | dynamic-scaling, interface, workflow-manager |
| audit events | the QA audit topic | audit-service, webhook-service, workflow-api |
| webhook requests | the QA webhook-request topic | webhook-service, workflow-api |
| event store | `e5_platform_event_store` | event-store, event-processor |

Re-read it with `python3 tools/cluster_links.py snapshot` (needs a kubeconfig; read-only).

---

## 4. Keeping this current

1. A row changes when a **task name or payload type** changes — which `./e5 estate diff`
   and `./e5 cite drift` both surface, because those are extracted facts.
2. A queue or topic **value** changing is not a change to this document. That is the point.
3. `./e5 check` verifies the task-plane rows against both routing switches
   (`check_routing.py`) and that every cited line resolves (`citations.py`).
4. Open questions are marked **open** above. Closing one means reading both sides and
   editing this file — not adding an extractor.

---
type: subsystem
name: Event Processor
repo: e5-platform-ss-event-processor
repo_path: platform-whole-context/subsystem-workflow-interaction-support/e5-platform-ss-event-processor
branch: release/v1.2.1
commit: eb630c2
language: java
build: gradle
deployment: event-processor
namespace_pattern: platform-subsystem-{env}
etp_roles: []
owns_schema: false
ports: [50051, 8080]
health: none
calls: [deployment-mgmt]
called_by: [webhook-dispatcher]
provenance: code
verified: 2026-10-02
deployed_in_qa: v1.2.0
deployment_lag: release/v1.2.1 available, v1.2.0 running
prose_reviewed_at_commit: 3da0e8c
---

> **Partly re-read 2026-11-13 against `release/v1.2.1` (`eb630c2`). Most of this card still
> describes `release/v1.2.0`.** v1.2.1 is not a patch: it redesigns event emission
> (`EventProcessorServiceImpl`, +244 lines; 14 files, +630/-293), checks webhook status
> before dispatching, adds a reprocessing time to the gRPC request, moves the scheduler to
> 60 minutes, and **removes retry-counting entirely** - `spring.scheduler.max-retry-count`
> (`RETRY_COUNT`, default 3) is gone from `application.yml`, and no retry reference remains
> in `src/main`, including the proto's `max_retry_count`. The config-key list below is
> corrected. **The Outbound and Failure-modes sections have not been re-read** and may no
> longer hold; `prose_reviewed_at_commit` is left at the old commit until they are.

# Event Processor

The **webhook subscription resolver** for the legacy dispatch path: it turns one CloudEvent into N per-subscriber dispatch messages. It does **not** make the outbound HTTP call.

**Responsibility (what it owns):** resolving which webhooks are subscribed to an event (by `deploymentVersionId`, else by workflow/variant/tenant triple), recording an `IN_PROGRESS` event-status row through deployment-mgmt, and emitting one `WebhookKafkaDTO` per enabled webhook (`EventProcessorServiceImpl.java:48-111`). It also owns the **retry sweeper** that re-dispatches not-yet-delivered event statuses on a cron (`SchedulerService.java:67-95`).

**Not its responsibility:** HTTP delivery (that is the `webhook-dispatcher` Lambda), persistence (all state is written via gRPC into the deployment-mgmt schema), and anything to do with [webhook-service](webhook-service.md) — the two are **not** connected; see ../../disagreements/event-processor-does-not-feed-webhook-service.md (archived).

**Entry points:** `E5PlatformSsEventProcessorApplication.java:9` (`@EnableScheduling`). Three ingresses: two Kafka listeners (`KafkaListenerService.java:40`, `:63`), a cron (`SchedulerService.java:67`), and two REST endpoints that re-enter the listeners with a synthetic `ConsumerRecord` (`EventProcessorController.java:46-57`, `:68-78`).

**Inbound:** Kafka `KAFKA_CONSUMER_EVENT_STORE_TOPIC` → a topic config key (`KafkaListenerService.java:40`) carrying the [platform CloudEvent](../contracts/platform-cloudevent-v1.md); Kafka `KAFKA_CONSUMER_DB_ACTION_TOPIC` → a topic config key (`:55`) carrying delivery-status callbacks. HTTP `POST /api/v1/event/processor` and `POST /api/v1/event/action` (`Constants.java:37,23,28,33`). See [event-processor-http-api](event-processor-http-api.md).

**Outbound:** Kafka `KAFKA_PRODUCER_EVENT_DISPATCH_TOPIC` → a topic config key (`KafkaProducerService.java:21,33`; also `SchedulerService.java:39,98`). gRPC to [deployment-mgmt](deployment-mgmt.md)'s `DeploymentManagementService` at `MANAGEMENT_SERVICE_HOST:MANAGEMENT_SERVICE_PORT` (`ManagedChannelConfig.java:16-30`, plaintext): `findWebhooksByEventTypeAndDeploymentVersionId` (`GrpcServiceImpl.java:34`), `findWebhooksByEventTypeAndWorkflowDetails` (`:69`), `findWebhooksByEventType` (`:91`), `saveEventStatus` (`:112`), `updateWebhookStatusById` (`:120`), `updateEventStatusById` (`:127`), `getEventStatusForScheduler` (`:134`). See [deployment-mgmt-webhook-rpcs](../contracts/deployment-mgmt-webhook-rpcs.md).

**Data owned:** none — `webhook` and `event_status` rows live in the deployment-mgmt schema and are only reachable over gRPC. `spring-boot-starter-data-jpa` + runtime H2 are on the classpath (`build.gradle:25,31`) with no entity or datasource config: an unused embedded DB.

**Config keys:** `grpc.server.port`, `spring.e5.platform.db.host`, `spring.e5.platform.db.port`, `spring.kafka.bootstrap-servers`, a topic config key.{action-consumer,event-store}`, `spring.kafka.consumer.{group-id,auto-offset-reset,key-deserializer,value-deserializer}`, a topic config key, `spring.kafka.producer.{group-id,auto-offset-reset,key-serializer,value-serializer}`, `spring.scheduler.cron` (`application.yml:1-32`). *`spring.scheduler.max-retry-count` was removed in `release/v1.2.1` - see the note at the top.* **Env-injected and blank in all four committed configs** (`deployment-configs/dev-config.yaml:13-28`): `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_CONSUMER_DB_ACTION_TOPIC`, `KAFKA_CONSUMER_EVENT_STORE_TOPIC`, `KAFKA_PRODUCER_EVENT_DISPATCH_TOPIC`. `KAFKA_CONSUMER_GROUP_ID` is the identical literal `webhook-dispatcher-group` in dev/qa/test/prod.

**ETP role:** none. It is a Kafka fan-out stage, not a transport-task-protocol participant.

**Failure modes:**
- **Fan-out loss after gRPC failure.** `GrpcServiceImpl` wraps every failure in `DatabaseDataRetrievalException` (`:44-47`), `EventProcessorServiceImpl` rethrows as `RuntimeException` (`:85-88`), and the listener only catches `IOException` (`KafkaListenerService.java:47`). With no `CommonErrorHandler` bean anywhere in the repo, Spring Kafka's default handler retries in-process and then **commits the offset and drops the record — there is no DLQ**.
- **Producer failures are swallowed**: `sendToKafka` catches `Exception` and only logs (`KafkaProducerService.java:35-39`); the send itself has no callback.
- **Original exception text is destroyed**: the catch blocks replace the real cause with "Webhooks response from gRPC call is empty" (`GrpcServiceImpl.java:46`, `:80`), so ops sees a misleading message.
- **Dead null-checks**: `webhooks == null` is tested *after* `webhooks.getWebhooksList()` is dereferenced (`GrpcServiceImpl.java:36` then `:38`); a blocking stub never returns null, so the guard can only NPE first.
- **Silent skip** when an event has neither `deploymentVersionId` nor a complete workflow triple — logged at WARN and dropped (`EventProcessorServiceImpl.java:80-85`).
- **No health surface:** no actuator dependency (`build.gradle:21-56`), no probes in any deployment config. A `grpc.server.port` is opened (`application.yml:4-6`) although the repo contains **no** gRPC service implementation.
- `GlobalExceptionHandler` returns `void` for every exception (`:22`, `:40`), so REST callers get HTTP 200 on failure.

**Code-level landmarks:** `EventProcessorServiceImpl.java:48-119` (resolution + fan-out), `GrpcServiceImpl.java:27-135` (every deployment-mgmt call), `KafkaListenerService.java:39-90` (both listeners), `SchedulerService.java:67-122` (retry sweep), `WebhookKafkaDTO.java:36-44` (outbound shape).

**Known divergences:** event-processor-does-not-feed-webhook-service (archived), event-processor-unauthenticated-http-backdoor (archived). `README.md` is a 1-line UTF-16 title stub with no content.

**Relevant runbooks:** none exist. Stuck dispatches are re-driven by the cron (`SCHEDULER_TIME_INTERVAL`, hourly in dev/prod, every 2 min in qa) bounded by `RETRY_COUNT`.

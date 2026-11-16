---
type: subsystem
name: Webhook Service
repo: e5-platform-ss-webhook-service
repo_path: platform-whole-context/subsystem-workflow-interaction-support/webhook-service
branch: release/v1.4.0
commit: 4bb7a37
language: java
build: maven
deployment: webhook-service
namespace_pattern: platform-subsystem-{env}
etp_roles: [task-queue, task-worker]
owns_schema: true
ports: [8083]
health: http /actuator/health
calls: [audit-service]
called_by: [workflow-api]
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.3.3
deployment_lag: release/v1.4.0 available, v1.3.3 running
prose_reviewed_at_commit: 60385c0
---

# Webhook Service

The **outbound HTTP delivery engine** — the only service in the subsystem that actually calls a customer endpoint. Distinct from [event-processor](event-processor.md) and the `webhook-dispatcher` Lambda, which form a separate, unconnected legacy path.

**Responsibility (what it owns):** the `webhook` Postgres schema (queue, dead letters, destinations); CloudEvents v1.0 ingestion with per-destination idempotency; strict FIFO-per-destination delivery via `FOR UPDATE SKIP LOCKED`; per-**host** circuit breaking; retry/backoff and dead-lettering; emitting the `com.webhook.audit.*` CloudEvents family.

**Not its responsibility:** deciding *who* subscribes (callers pass `destinationid`; there is no subscription table), storing destination URLs or credentials (AWS Secrets Manager only — `V1__init.sql:62`), and exposing any business REST API — the repo contains **zero** `@RestController` classes; port 8083 serves actuator only.

**Entry points:** `WebhookServiceApplication.java:46`. Ingestion: `WebhookKafkaConsumer.consumeWebhookRequest` (`:82`). Delivery: `WebhookQueuePoller.poll` (`:164`), a `@Scheduled` loop dispatching onto virtual threads (`:135`, `:187`).

**Inbound:** Kafka, the webhook-request topic (`WebhookKafkaConsumer.java:78`), group `KAFKA_CONSUMER_GROUP` → `spring.kafka.consumer.group-id`, manual ack, concurrency `KAFKA_LISTENER_CONCURRENCY` (`application.yml:52-54`). Contract: [webhook-request message](../contracts/webhook-request-message.md). HTTP: `/actuator/{health,info,prometheus,metrics}` only (`application.yml:61-66`) — see [webhook-service-http-api](webhook-service-http-api.md).

**Outbound:** HTTPS POST to each destination's Secrets-Manager URL (`WebhookHttpClient.java:83-98`). It also **auto-creates three topics** on startup — the webhook-request topic, the webhook-request dead-letter topic and the shared the audit-event topic — with a topic config key defaulting to **1** (`KafkaConfig.java:39-61`), which is what ships when a topic env var is left blank. Kafka a topic env var → `audit.topic` (default the audit-event topic, `AuditKafkaProducer.java:69,184`) → [audit-service](audit-service.md). Kafka a topic set by deploy-time config (default the webhook-request dead-letter topic), written by **two** producers with **two different payload shapes** (`DeadLetterService.java:34,51` and `DeadLetterEventPublisher.java:29,44`) — see [webhook dead-letter message](../contracts/webhook-dead-letter-message.md). AWS Secrets Manager reads via `SecretsManagerDestinationConfigService`; Redis for circuit state (`RedisCircuitBreakerRepository`). No gRPC, no VQ.

**Data owned:** `destinations`, `webhook_queue`, `webhook_dead_letters`, `url_sequences` (deprecated) — `db/migration/V1__init.sql`, V2–V5. Circuit-breaker state is **not** a table; it is Redis or in-process memory (`application.yml:122`).

**Config keys:** prefixes `spring.datasource.*`, `spring.data.redis.*`, `spring.kafka.*`, `kafka.topics.*` (`KafkaTopicsConfig.java:20`), `webhook.circuit-breaker.*` (`CircuitBreakerConfig.java:14`), `webhook.delivery.*` (`WebhookDeliveryProperties.java:15`), `webhook.queue.*` (`WebhookQueueConfig.java:14`), `webhook.replay.default-max-batch-size`, `webhook.dead-letter.{cleanup-cron,retention-days}`, `audit.*` (`AuditConfig.java:17`), `aws.region`, `aws.secretsmanager.*`, `http-client.*`, `secrets-manager.retry.*`, `redis.key-prefixes.*`, `credentials.cache-ttl-minutes`, `otel.*`. **Every env var in `deployment-configs/{dev,qa,test,demo,prod}-config.yaml` is committed blank** (lines 9-314, prod and dev differ only in `nodeSelector`). Blank `SPRING_PROFILES_ACTIVE` activates the implicit `default` profile, which flips circuit state to process-local `memory` (`application.yml:213-222`, `application-default.yml:11-14`) — multi-pod circuit breaking then does not work. `WEBHOOK_POSTGRES_*` and `WEBHOOK_CB_STORAGE` are absent from the deployment configs entirely; `WEBHOOK_STREAM_*`, `WEBHOOK_PENDING_TIMEOUT`, `RATE_LIMIT_*`, `OAUTH2_*`, `AUDIT_RETENTION_*`, `AWS_S3_*` are declared there but read by no code, and `webhook.replay.default-max-batch-size` (`application.yml:117`) is read by no code either.

**ETP role:** task-queue + task-worker over its own Postgres queue (not AKST). `pollReadyWebhooksFifo` is the claim (`WebhookQueueRepository.java:111-128`), `PROCESSING` is the lease, and `recoverStuckMessages` (`WebhookQueueService.java:194`) is the lease reaper.

**Failure modes:**
- **Retry/DLQ:** per-row `retry_config`, default max 5 attempts, EXPONENTIAL ×2 from 1 s capped at 1 h, retried on 429/500/502/503/504 (`RetryConfig.java:33-38`). Exhaustion or a permanent failure **copies** the row into `webhook_dead_letters` and flips the queue row to `DEAD_LETTERED` without deleting it (`RetryExhaustionHandler.java:57-67`), then emits a Kafka notification (`WebhookQueuePoller.java:414-430`). `Retry-After` can only *lengthen* the delay (`:368-375`).
- **Ingestion:** validation rejects are ACKed and DLQ'd; only `PERSISTENCE_ERROR`/`ENQUEUE_ERROR` withhold the ack for redelivery (`WebhookKafkaConsumer.java:53-56,137-151`). `ENQUEUE_ERROR` is never produced by `WebhookIngestionService` — a dead constant.
- **Swallowed:** DLQ send failure is logged only (`WebhookKafkaConsumer.java:218-221`); audit publish failure falls back to the `audit-fallback` logger (`AuditKafkaProducer.java:199-212`), i.e. the audit trail degrades to stdout; bad `retry_config` JSON silently reverts to defaults (`WebhookQueuePoller.java:448-452`).
- **Dead letters are terminal.** `WebhookQueue.fromDeadLetter:33` has no caller, so nothing re-queues a dead letter; `DeadLetterRetentionJob.java:30` deletes them 30 days later.
- **Lost on crash:** rows left in `PROCESSING` are only recovered by the stuck job every 60 s (hardcoded — see divergence below).
- **Unauthenticated actuator:** no `spring-boot-starter-security` in `pom.xml` and `show-details: always` (`application.yml:69`) exposes DB/Redis/Kafka detail on 8083.
- **Circuit transitions are never audited** despite the event types existing — see divergence.

**Code-level landmarks:** `WebhookIngestionService.java:117-216` (ingest pipeline), `WebhookQueuePoller.java:197-248` (delivery loop), `WebhookQueueRepository.java:111-128` (FIFO claim SQL), `CloudEventsValidator.java:46-125` (inbound contract enforcement), `WebhookHttpClient.java:76-126` (outbound call + error classification), `V1__init.sql:16-240` (schema).

**Known divergences:** webhook-queue-config-prefix-mismatch (archived), webhook-destination-suspension-changelog-stale (archived), webhook-circuit-breaker-audit-never-emitted (archived), webhook-async-audit-is-synchronous (archived), webhook-redis-streams-stale-comments (archived), webhook-features-doc-claims-missing-api (archived). `README.md:3` says Spring Boot 3.2.5 — `pom.xml:10` confirms it, but `pom.xml:16` version is `0.0.1-SNAPSHOT`, not 1.3.3.

**Relevant runbooks:** none in-repo. Operational levers: a `com.e5platform.workflow.webhook.availability` CloudEvent forces a host's circuit to HALF_OPEN and wakes queued rows (`WebhookIngestionService.java:172-180`); `DeadLetterRetentionJob.java:40` purges dead letters nightly.

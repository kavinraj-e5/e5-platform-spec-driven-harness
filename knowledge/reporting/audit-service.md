---
type: subsystem
name: Audit Service
repo: e5-platform-ss-audit-service
repo_path: platform-whole-context/subsystem-workflow-interaction-support/e5-platform-ss-audit-service
branch: release/v1.1.1
commit: b73dcec
language: java
build: maven
deployment: audit-service
namespace_pattern: platform-subsystem-{env}
etp_roles: []
owns_schema: true
ports: [8080]
health: http /actuator/health
calls: []
called_by: [webhook-service, workflow-api]
provenance: code
verified: 2026-10-02
deployed_in_qa: v1.1.0
deployment_lag: release/v1.1.1 available, v1.1.0 running
---

# Audit Service

A **write-only CloudEvents sink** for the HIPAA audit trail: Kafka in, partitioned Postgres out, optional S3 Parquet cold archive. At v1.1.1 it exposes **no query API at all** — readers go straight to the database or the bucket (`SecurityConfig.java:12-18`).

**Responsibility (what it owns):** the `audit` Postgres schema; CloudEvents v1.0 envelope validation plus a controlled-vocabulary business validation; idempotent insert keyed on `event_id`; monthly partition creation; S3 Parquet archival with Object Lock and an `archive_log` ledger; raising FreshDesk tickets for a fixed set of alertable actions.

**Not its responsibility:** producing audit events (every producer builds its own CloudEvent), querying/reporting, retention *deletion* policy enforcement beyond partition drop, and access control on audit reads — see the README divergence below.

**Entry points:** `AuditServiceApplication.java`. Ingestion: `CloudEventsAuditConsumer.consume` (`:95`). Archival: `ArchiveService.runArchiveJob` (`:83`), ShedLock-guarded (`:78-82`).

**Inbound:** Kafka, the audit-event topic (`CloudEventsAuditConsumer.java:91`), group `audit-service-group` (`application.yml:85`), manual ack, concurrency 3. Contract: [audit-event message](../contracts/audit-event-message.md). HTTP `GET /api/v1/health` (`HealthController.java:31`) plus `/actuator/{health,info,prometheus,metrics}` — see [audit-service-http-api](audit-service-http-api.md).

**Outbound:** Kafka, the audit-event dead-letter topic (`AuditEventDeadLetterRecoverer.java:39,113`); Kafka a topic set by deploy-time config via the notification-hub SDK producer (`FreshDeskNotifierConfig.java:42,96`); S3 `PutObject` with Object Lock + checksum verify (`ArchiveService.java:139-142`). No gRPC, no VQ.

**Data owned:** `audit.events` (RANGE-partitioned monthly on `event_time`), `audit.archive_log`, `audit.meta_audit_log`, `shedlock` — `db/migration/V1__init.sql:20,113,228,240`. Partitions are created by `audit.ensure_partitions_exist(3)` at migration time (`:222`).

**Config keys:** `spring.datasource.*`, `spring.jpa.*`, `spring.flyway.*`, `spring.kafka.*`, `kafka.topics.{audit-events,audit-events-dlq,notification-hub}` (`KafkaTopicsConfig.java:27`), `kafka-error-handler.{initial-interval-ms,max-interval-ms,multiplier,max-elapsed-time-ms}`, `dead-letter.max-stack-trace-length`, `error-response.problem-base-uri`, `notification-hub.freshdesk.*` (`FreshDeskNotificationProperties.java:17`), `aws.region`, `aws.s3.{enabled,endpoint,bucket-name,access-key,secret-key,kms-key-id,object-lock-enabled,checksum-enabled}`, `aws.secretsmanager.*`, `cache.secrets.*`, `archive.schedule`, `archive.retention-days`, `audit.retention.archive-after-days`, `app.temp-dir`. **All env vars in `deployment-configs/*-config.yaml` are committed blank** (prod lines 9-177). Keys declared with a bare `${ENV}` and no YAML fallback are **hard startup requirements**: `SPRING_PROFILES_ACTIVE`, `DB_HOST/PORT/NAME/USERNAME/PASSWORD`, `KAFKA_BOOTSTRAP_SERVERS`, `AWS_REGION`, `AWS_S3_ENDPOINT`, `AWS_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SECRETSMANAGER_ENABLED/ENDPOINT`, `AWS_SECRET_DATABASE`, `AWS_SECRETSMANAGER_CACHE_TTL`, `CACHE_SECRETS_*`, `KAFKA_ERROR_*`, `DEAD_LETTER_MAX_STACK_TRACE_LENGTH`, `ERROR_PROBLEM_BASE_URI`, `AUDIT_RETENTION_ARCHIVE_AFTER_DAYS` (the `:90` fallback at `PartitionRepository.java:26` is unreachable because `application.yml:163` re-declares the key with a mandatory placeholder). **Dead keys** read by no code: `audit.event.batch-size`, `audit.event.processing-timeout-ms`, `audit.retention.hot-tier-days`, `audit.retention.total-retention-years`, `audit.query.default-page-size`, `audit.query.max-page-size`.

**ETP role:** none — pure consumer plus a ShedLock-coordinated batch job.

**Failure modes:**
- **Retry then DLQ.** `DefaultErrorHandler` with `ExponentialBackOff` from `kafka-error-handler.*` (`KafkaErrorHandlerConfig.java:57-65,83`). `ValidationException`, `IllegalArgumentException`, `NullPointerException`, `ClassCastException` are **non-retryable** → straight to DLQ (`:86-91`). `CloudEventValidationException` is *not* in that list and is rewrapped as `RuntimeException` (`CloudEventsAuditConsumer.java:143`), so a malformed envelope is retried for the whole `max-elapsed-time-ms` window before DLQ — head-of-line blocking on its partition.
- **Audit events can be lost.** The DLQ send is fire-and-forget; a failed publish is logged only (`AuditEventDeadLetterRecoverer.java:116-127`) while the source offset is committed by the error handler. For a HIPAA trail this is silent loss.
- **Duplicates dropped quietly** on `existsByEventId` (`EventProcessingServiceImpl.java:65-69`); metric `audit.events.duplicates` (`AuditMetricsImpl.java:24`) is the only signal.
- **Archive is off unless enabled.** `ArchiveService` and `S3HealthIndicator` are `@ConditionalOnProperty(aws.s3.enabled=true, matchIfMissing=false)` (`ArchiveService.java:35`, `S3HealthIndicator.java:18`) and `application.yml:129` defaults it to `false`; `AWS_S3_ENABLED` is blank in every committed config. See ../../disagreements/audit-s3-archive-off-by-default.md (archived).
- **Per-partition archive failures are swallowed** and the loop continues (`ArchiveService.java:98-103`); the whole job swallows its own failure too (`:112-115`).
- **FreshDesk mis-config fails startup** when the flag is on (`FreshDeskNotifierConfig.java:109-122`) — deliberately fail-fast; when off, a no-op notifier is installed (`:47-49`) and alerts are silently not raised.
- **`audit.meta_audit_log` is always empty** — `@MetaAudited` is applied to no method. See divergence.

**Code-level landmarks:** `CloudEventsAuditConsumer.java:95-163` (ingest + ack/DLQ decision), `EventProcessingServiceImpl.java:52-88` (validate→dedupe→enrich→persist→alert), `EventValidator.java:38-64` (the accept/reject contract), `KafkaErrorHandlerConfig.java:70-104` (retry classes + DLQ routing), `V1__init.sql:20-65,163-222` (partitioned table + partition functions), `ArchiveService.java:83-170` (archive job).

**Known divergences:** audit-readme-claims-query-api-rbac-metaaudit (archived), audit-failed-events-index-case-mismatch (archived), audit-s3-archive-off-by-default (archived). Minor: a topic config key has two different in-code defaults — `e5-notification-hub-platform-2.0` (`FreshDeskNotifierConfig.java:42`) vs `e5.notification.hub` (`application.yml:105`, which wins); `EventValidator.java:133` caps `actionName` at 100 chars while the column is `VARCHAR(200)` (`V1__init.sql:48`).

**Relevant runbooks:** none in-repo. `audit.ensure_partitions_exist(n)` must be re-run before partitions run out — nothing in the application schedules it.

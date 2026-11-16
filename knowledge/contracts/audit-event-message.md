---
type: contract
name: audit event message (CloudEvents audit family)
kind: kafka-message
owner: audit-service
producers: [webhook-service, workflow-api]
consumers: [audit-service]
serialization: structured CloudEvents JSON body + ce_* Kafka headers
provenance: code
verified: 2026-10-02
---

# the audit-event topic

The platform audit stream consumed by [audit-service](../reporting/audit-service.md) into [audit.events](../tables/audit-events.md). Producers hand-build the envelope; the consumer validates it with the CloudEvents SDK **and** a second controlled-vocabulary pass.

## Envelope
Body (`CloudEventAuditEvent.java:13-23`, serialised at `AuditKafkaProducer.java:85`): `specversion`, `id`, `source`, `type`, `time`, `datacontenttype`, `subject` (= `data.entityId`), `traceparent`, `requestid`, `data`. The consumer DTO (`CloudEventAuditDto.java:15-45`) has no `requestid` field, so that attribute is **silently discarded** — tolerated only because `spring.jackson.deserialization.fail-on-unknown-properties: false` (`application.yml:42-43`). `source` is built from `audit.source.pattern` × `audit.source.environment` (`AuditKafkaProducer.java:71-73`, default `/webhook-service/local`). Duplicated as Kafka headers `ce_specversion`, `ce_type`, `ce_source`, `ce_id`, `ce_time`, `ce_subject`, `content-type` plus a W3C `traceparent` (`:165-185`). Record **key** = `data.entityId`, else the event id (`:86`). The consumer prefers the `traceparent` header over the body attribute (`CloudEventsAuditConsumer.java:169-196`).

`data` is a flat object, field-for-field identical on both sides (producer `AuditEventData.java:13-57`, consumer `dto/AuditEventData.java:16-58`): `entityId, actorId, actorType, actionCategory, actionName, outcome, outcomeReason, sensitivityLevel, resourceId, resourceType, correlationId, clientIp, serviceName, serviceVersion, details{}`. Only `details` reaches the database as JSONB (`CloudEventMapper.java:65`); `serviceName` falls back to a segment of the `source` URI when absent (`:88-100`).

## Accept/reject rules (`EventValidator.java:38-64`, enums in `domain/enums/`)
Mandatory: `specversion == "1.0"`; `id` a **well-formed UUID**; `source` ≤500; `type` ≤200; `time` present and **not in the future**; `data.entityId` ≤200; `data.actorId` ≤200; `data.actionCategory` ∈ `CREATE,READ,UPDATE,DELETE,EXECUTE`; `data.actionName` ≤100; `data.outcome` ∈ `SUCCESS,FAILURE,PARTIAL`. Optional-if-present: `actorType` ∈ `USER,SYSTEM,SERVICE,ADMIN`; `sensitivityLevel` ∈ `PUBLIC,INTERNAL,CONFIDENTIAL,PHI`. Matching is case-insensitive (`:171`) but the value is persisted **verbatim** (`EventEnricher.java:77`).

## Event-type families produced by webhook-service (`CloudEventType.java:10-18`)
| `type` | `data.actionName` | Emitted at |
|---|---|---|
| `com.webhook.audit.webhook.received` | `webhook.received` | `WebhookKafkaConsumer.java:124` |
| `com.webhook.audit.webhook.rejected` | `webhook.rejected` | `:142` (`entityId` is a synthetic `rejected-<uuid>`, `AuditService.java:67`) |
| `com.webhook.audit.webhook.delivered` | `webhook.delivered` | `WebhookQueuePoller.java:329` |
| `com.webhook.audit.webhook.delivery_failed` | `webhook.delivery_failed` | `:379` |
| `com.webhook.audit.webhook.retry_scheduled` | `webhook.retry_scheduled` | `:397` |
| `com.webhook.audit.webhook.dead_lettered` | `webhook.dead_lettered` | `:423` |
| `com.webhook.audit.circuit_breaker.{opened,closed,half_open}` | — | **never emitted**; see ../../disagreements/webhook-circuit-breaker-audit-never-emitted.md (archived) |

`actionName` is the alerting key: `webhook.delivery_failed` and `webhook.dead_lettered` (plus `validation_failure`, `transformation_failure`) are the only values that raise a FreshDesk ticket (`NotificationAlertEnum.java:13-16`, `NotificationHubPublisher.java:35`). Renaming an `actionName` silently disables the ticket.

## Failure routing
Non-retryable (`ValidationException`, `IllegalArgumentException`, `NullPointerException`, `ClassCastException`) → immediate the audit-event dead-letter topic with `x-original-*` / error / truncated stack-trace headers (`KafkaErrorHandlerConfig.java:86-91`, `AuditEventDeadLetterRecoverer.java:60-103`). Envelope-level `CloudEventValidationException` is **retried** for the whole `kafka-error-handler.max-elapsed-time-ms` window first (`CloudEventsAuditConsumer.java:135-143`). A failed DLQ publish is logged only — the event is lost (`AuditEventDeadLetterRecoverer.java:116-127`).

## Topic-name hazard
webhook-service resolves this topic **twice from two different keys**: a topic config key ← a topic env var for topic auto-creation (`KafkaConfig.java:56-61`) and `audit.topic` ← `AUDIT_TOPIC` for publishing (`AuditConfig.java:22`, `AuditKafkaProducer.java:69`). Both are blank in the committed deployment configs; setting only one makes the service create one topic and publish to another.

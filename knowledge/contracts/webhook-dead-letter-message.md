---
type: contract
name: webhook-service dead-letter message (two payload shapes)
kind: kafka-message
owner: webhook-service
producers: [webhook-service]
consumers: []
serialization: plain JSON (not CloudEvents)
provenance: code
verified: 2026-10-02
---

# the webhook-request dead-letter topic

One topic, **two structurally unrelated messages**, both produced by [webhook-service](../reporting/webhook-service.md). Any consumer must discriminate on field presence — there is no `type`, no `ce_type` header and no schema marker. No consumer exists in this repository set.

## Shape A — ingestion rejection (`DeadLetterService.buildDlqMessage:67-84`)
```
{ "originalMessage": <the original CloudEvent JSON, or a JSON string if unparseable>,
  "error": { "code": "<IngestionResult errorCode>", "message": "<text> [destinationId=…]",
             "timestamp": "<ISO-8601>" },
  "sourcePartition": <int>, "sourceOffset": <long> }
```
Emitted from `WebhookKafkaConsumer.sendToDlqForDestination:210-222` for every non-transient rejection: `INVALID_CLOUDEVENT`, `EMPTY_MESSAGE`, `INVALID_SPEC_VERSION`, `MISSING_ATTRIBUTE`, `MISSING_EXTENSION`, `VALIDATION_ERROR`, `EMPTY_DATA`, `MISSING_DESTINATION_ID`, `MISSING_IDEMPOTENCY_KEY`, `DESTINATION_NOT_FOUND`, `DESTINATION_INACTIVE`, `DESTINATION_CONFIG_NOT_FOUND`. `PERSISTENCE_ERROR` does **not** land here — it withholds the ack instead (`:53-56,137-140`).

## Shape B — delivery exhaustion notification (`DeadLetterEventPublisher.buildEventJson:60-78`)
```
{ "webhookId": "<uuid of the original webhook_queue row>", "sourceService": "…",
  "destinationId": "…", "destinationUrl": "<full URL, unmasked>",
  "failureReason": "Exhausted after N attempts, last response: HTTP X",
  "attemptCount": <int>, "lastResponseCode": <int|null>,
  "deadLetteredAt": "<ISO-8601>" }
```
Emitted from `WebhookQueuePoller.handleExhaustion:420` after the row is written to [webhook_dead_letters](../tables/webhook_dead_letters.md). It is a **notification, not the payload** — the undelivered body stays in Postgres.

## Why this is risky
- Shape B publishes the **raw destination URL** to Kafka, while the audit path deliberately masks it down to `scheme://host/***` (`AuditService.maskUrl:302-312`, used at `:90,:118,:177`). The DLQ topic is therefore a weaker-classified copy of data the service otherwise protects.
- `DeadLetterEventPublisher`'s own `@Value` default is `webhook-dead-letters` (`:29`) while `DeadLetterService` resolves the topic through `KafkaTopicsConfig.getWebhookRequestsDlq` (`:34`). They only agree because `application.yml:92` sets the key; remove it and the two producers split across two topics.
- The topic is **auto-created by webhook-service** via `KafkaConfig.webhookRequestsDlqTopic:47-53` with a topic config key (default 3) and a topic config key (**default 1**), against the Boot-autoconfigured `KafkaAdmin`. a topic env var is blank in every deployment config, so an unreplicated DLQ is the default outcome even though `application.yml:86` says production wants 3.
- Nothing consumes this topic: there is no replay job and no alerting consumer. Operationally it is a write-only audit tail; actionable failure signal comes from [audit-event message](audit-event-message.md) instead.

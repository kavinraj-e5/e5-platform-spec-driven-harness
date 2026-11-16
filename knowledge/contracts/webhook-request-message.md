---
type: contract
name: webhook request message (CloudEvents v1.0 ingestion)
kind: kafka-message
owner: webhook-service
producers: [workflow-api]
consumers: [webhook-service]
serialization: CloudEvents v1.0 structured JSON (String key/value)
provenance: code
verified: 2026-10-02
---

# the webhook-request topic

The ingestion contract of [webhook-service](../reporting/webhook-service.md), enforced by the **official CloudEvents SDK** (`CloudEventsValidator.java:53-61`) rather than hand-rolled parsing — this is the strictest envelope in the subsystem and is unrelated to [platform-cloudevent-v1](platform-cloudevent-v1.md).

## Required attributes (`CloudEventsValidator.java:68-107`)
| Attribute | Rule | Rejection code |
|---|---|---|
| `specversion` | must be exactly `1.0` | `INVALID_SPEC_VERSION` |
| `id` | non-blank | `MISSING_ATTRIBUTE` |
| `source` | non-null | `MISSING_ATTRIBUTE` |
| `type` | non-blank | `MISSING_ATTRIBUTE` |
| `destinationid` **(extension)** | non-blank, ≤255 chars | `MISSING_EXTENSION` / `VALIDATION_ERROR` |
| `idempotencykey` **(extension)** | non-blank, ≤255 chars | `MISSING_EXTENSION` / `VALIDATION_ERROR` |
| `data` | must deserialise to a **non-empty JSON object** (a scalar is wrapped as `{"value": …}`) | `EMPTY_DATA` |
| whole message | parseable as structured-mode CloudEvents JSON | `INVALID_CLOUDEVENT` / `EMPTY_MESSAGE` |

Anything that fails is ACKed and copied to [webhook dead-letter message](webhook-dead-letter-message.md) — never retried (`WebhookKafkaConsumer.java:132-144`).

## Semantics the producer must know
- **Fan-out by comma.** `destinationid` may be a comma-separated list; the consumer splits it and runs the pipeline once per destination (`WebhookKafkaConsumer.java:164-183`, `:115-116`), producing N queue rows from one record.
- **Idempotency is scoped per destination.** The stored key is `sha256(idempotencykey + ":" + destinationId)` (`WebhookIngestionService.java:190`), unique on `webhook_queue.idempotency_key`. Re-sending the same key to the same destination is a no-op; to the same destination *after* it dead-lettered it is **also** a no-op only while the queue row survives.
- **`data` is split, not forwarded whole.** `workflowId, workflowName, clientId, clientName` are moved into `webhook_queue.metadata`, and `taskId, status, message` plus those four are **removed** from the delivered body (`WebhookIngestionService.java:247-277`). Everything else in `data` is what the customer receives.
- **The destination must pre-exist and be `ACTIVE`** in [destinations](../tables/destinations.md) *and* have a Secrets Manager entry, otherwise `DESTINATION_NOT_FOUND` / `DESTINATION_INACTIVE` / `DESTINATION_CONFIG_NOT_FOUND`.
- **`X-Source-IP`** is read as an optional Kafka header (`WebhookKafkaConsumer.java:87`) and then never used.

## Control-plane event type
`type = com.e5platform.workflow.webhook.availability` is **not** queued. It forces the destination host's circuit breaker to HALF_OPEN and resets `next_attempt_at` on that destination's queued rows, returning `SKIPPED` (`WebhookIngestionService.java:51,172-180`). Everything else about the envelope (including `idempotencykey`) is still validated first.

## Delivery guarantees
Manual ack via a dedicated container factory (`KafkaConfig.kafkaListenerContainerFactory:63-72`), `enable-auto-commit: false`, concurrency `KAFKA_LISTENER_CONCURRENCY` (default 3), `auto-offset-reset: earliest` (`application.yml:43-54`). The topic is auto-created by `KafkaConfig.webhookRequestsTopic:39-45` with a topic config key (default 6) and a topic config key (default **1**). Only `PERSISTENCE_ERROR` withholds the ack for redelivery; every other failure ACKs. There is no key convention in the repo, so ordering across partitions is not guaranteed — FIFO is re-established in Postgres by `destination_id`, not by Kafka.

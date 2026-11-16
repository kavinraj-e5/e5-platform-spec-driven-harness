---
type: contract
name: WebhookKafkaDTO dispatch + status loop
kind: kafka-message-group
owner: event-processor
producers: [event-processor, webhook-dispatcher]
consumers: [webhook-dispatcher, event-processor]
serialization: JSON string (StringSerializer / StringDeserializer)
provenance: code
verified: 2026-10-02
---

# WebhookKafkaDTO dispatch + status loop

The **legacy** webhook path's two-topic round trip, owned by [event-processor](../reporting/event-processor.md). This is **not** a CloudEvent and is **not** compatible with [webhook-service](../reporting/webhook-service.md)'s the webhook-request topic — see ../../disagreements/event-processor-does-not-feed-webhook-service.md (archived).

## Leg 1 — dispatch request (event-processor → webhook-dispatcher Lambda)
Topic a topic config key ← `KAFKA_PRODUCER_EVENT_DISPATCH_TOPIC`. Written by `KafkaProducerService.sendToKafka:30-34` (live path) and `SchedulerService.sendToKafka:95-99` (retry sweep).

`WebhookKafkaDTO` (`WebhookKafkaDTO.java:36-44`, `@JsonInclude(NON_NULL)`):
| Field | Meaning | Populated by |
|---|---|---|
| `webhookId` | DMS `webhooks.id` | `EventProcessorServiceImpl.java:124` |
| `webhookUrl` | the customer endpoint — **the URL travels on the wire** | `:129` |
| `authType` | DMS `webhooks.auth_type` | `:130` |
| `secureKey` | **the shared secret, in cleartext, inside the Kafka record** | `:131` |
| `eventStatusId` | DMS `tbl_event_status.id` to report back against | `:127` |
| `isScheduler` | `true` only on the retry sweep (no `setScheduler(true)` at event-processor `release/v1.2.1` - re-verify; it was line 85 of SchedulerService.java at 3da0e8c) | — |
| `event` | the whole inbound [platform CloudEvent](platform-cloudevent-v1.md) as a nested object | `:126` |

The consumer is an AWS Lambda, not a Spring consumer: `LambdaHandler.handleRequest:40-52` walks an MSK event-source payload (`records` → per-partition arrays → base64 `value`) and `WebhookEventDispatcherServiceImpl.java:69,103` reads `webhookUrl` and POSTs.

## Leg 2 — status callback (webhook-dispatcher → event-processor)
Topic a topic config key ← `KAFKA_CONSUMER_DB_ACTION_TOPIC`, produced by the Lambda to `KAFKA_PRODUCER_TOPIC_NAME` (`ConfigLoader.java:36`). Consumed by `KafkaListenerService.actionListener:55`.

Shape is a loose envelope with two optional branches (this parsing is no longer in `KafkaListenerService` at `release/v1.2.1` - re-verify; was `:67-84`):
| Branch | Fields read | Effect |
|---|---|---|
| `webhook` | `id`, `isEnabled` | → `UpdateWebhookStatusById` on [deployment-mgmt](../subsystems/deployment-mgmt.md); `isEnabled=false` disables the subscription and is logged at ERROR (`:73`) |
| `eventStatus` | `id`, `status` | → `UpdateEventStatusById`; the sweep later re-dispatches rows whose status is still not terminal |

Field names come from `Constants.TABLES` (`Constants.java:106,103,111`). Both branches are optional; a record with neither is silently ignored. Only `JsonProcessingException` is caught (`:87`), so a gRPC failure escapes the listener with no DLQ.

## Security note
`secureKey` and `webhookUrl` are persisted in Kafka for the topic's whole retention, and the same `event` payload is also stored as a string in DMS `tbl_event_status.event` (not in `GrpcServiceImpl` at `release/v1.2.1` - re-verify; was `:110`). Contrast [webhook-service](../reporting/webhook-service.md), which keeps URLs and credentials exclusively in AWS Secrets Manager.

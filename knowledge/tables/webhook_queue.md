---
type: table
name: webhook_queue
schema: public
database: webhook-service Postgres (DB_NAME)
owner: webhook-service
migration: src/main/resources/db/migration/V1__init.sql:71
entity: com/webhook/service/domain/queue/WebhookQueue.java
provenance: code
verified: 2026-10-02
---

# webhook_queue

The **durable delivery queue** of [webhook-service](../reporting/webhook-service.md) — it is both the backlog and the distributed lock: `PROCESSING` is the lease, `FOR UPDATE SKIP LOCKED` is the claim.

## Fields (`V1__init.sql:71-115`, `V4__add_circuit_probe_flag.sql:2`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `destination_id` | VARCHAR(255) | logical ref to [destinations](destinations.md); the FIFO grouping key |
| `destination_url`, `destination_url_hash` | TEXT / VARCHAR(64) | URL snapshot at ingest; hash is SHA-256 (`HashingUtil.sha256Hex`) |
| `source_service` | VARCHAR(100) | copied from the destination row |
| `status` | VARCHAR(20) NOT NULL DEFAULT 'PENDING' | `PENDING, PROCESSING, RETRYING, DELIVERED, FAILED, DEAD_LETTERED` (`WebhookStatus.java:7-12`) |
| `payload` | JSONB NOT NULL | CloudEvent `data` **minus** `taskId,status,message,workflowId,workflowName,clientId,clientName` (`WebhookIngestionService.java:247-260`) — this is the exact body sent to the customer |
| `metadata` | JSONB | the `workflowId/workflowName/clientId/clientName` lifted out of `data` (`:262-277`) |
| `retry_config` | JSONB NOT NULL | snapshot of the destination's config or `RetryConfig` defaults (`:279-292`) |
| `attempt_count`, `next_attempt_at`, `last_attempt_at`, `last_response_code`, `last_error_message` | — | attempt bookkeeping; error text truncated to 4000 chars (`WebhookQueueService.java:33`) |
| `idempotency_key` | VARCHAR(255) NOT NULL | `uk_webhook_queue_idempotency`; value is `sha256(rawKey + ":" + destinationId)` (`WebhookIngestionService.java:190`) — **not** the sender's raw key |
| `priority` | VARCHAR(10) NOT NULL DEFAULT 'NORMAL' | always written as `NORMAL` (`:233`); `HIGH`/`LOW` are unreachable from ingestion |
| `circuit_probe` | TIMESTAMPTZ | non-null = woken by an availability probe; stores the original `next_attempt_at` to restore (`V4:4-6`) |
| `sequence_number` | BIGINT | **dead** — see [url_sequences](url_sequences.md) |

**FKs:** none (no constraint to `destinations`).

## Who reads/writes it
| Operation | Code | Effect |
|---|---|---|
| idempotency probe | `WebhookIngestionService.java:192` | read by `idempotency_key` → SKIPPED if present |
| insert | `WebhookIngestionService.java:203` | new row `PENDING`, `attempt_count=0`, `next_attempt_at=now()` |
| FIFO claim | `WebhookQueueRepository.pollReadyWebhooksFifo:111-128` → `WebhookQueueService.pollReadyWebhooks:58-73` | `DISTINCT ON (destination_id)` + `FOR UPDATE SKIP LOCKED`, then all claimed rows set to `PROCESSING` |
| terminal success | `markAsDelivered:76-88` (row retained) | `DELIVERED`, `attempt_count+1` |
| retry | `markAsRetrying:91-113` / `markAsRetryingProbe:171` | `RETRYING` + new `next_attempt_at`; probe variant restores schedule without burning an attempt (`WebhookQueuePoller.java:347-353`) |
| release | `releaseWebhook:130` / `releaseWithDelay:147` | back to `PENDING` when the circuit is open (`WebhookQueuePoller.java:290`) or on unexpected error (`:242`) |
| lease reaper | `recoverStuckMessages:139-147`, scheduled at `WebhookQueueService.java:194` | `PROCESSING` older than the stuck timeout → `PENDING` |
| probe wake-up | `resetNextAttemptForProbe:171-180` via `resetForCircuitProbe:165` | sets `circuit_probe = next_attempt_at`, `next_attempt_at = NOW()` for a destination |
| exhaustion | `RetryExhaustionHandler.handleExhaustion:50-67` | inserts into [webhook_dead_letters](webhook_dead_letters.md) **and keeps the queue row**, flipping it to `DEAD_LETTERED` (`:60,:67`) — the row is never deleted, so the queue table grows without bound and `V1__init.sql:186` ("before deletion") is stale |

Indexes: `idx_webhook_queue_fifo_polling(destination_id, created_at) WHERE status IN ('PENDING','RETRYING')`, `idx_webhook_queue_stuck_recovery(updated_at) WHERE status='PROCESSING'`, `idx_webhook_queue_ready_processing`, plus url-hash/destination/source indexes (`V1__init.sql:118-237`).

**Consumers:** `WebhookHealthIndicator.java:36` surfaces pending/processing/retrying counts on `/actuator/health`. No CDC, no outbox — the only external signal is the audit CloudEvent stream.

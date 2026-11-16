---
type: table
name: webhook_dead_letters
schema: public
database: webhook-service Postgres (DB_NAME)
owner: webhook-service
migration: src/main/resources/db/migration/V1__init.sql:135
entity: com/webhook/service/domain/deadletter/WebhookDeadLetter.java
provenance: code
verified: 2026-10-02
---

# webhook_dead_letters

The terminal store for webhooks [webhook-service](../reporting/webhook-service.md) gave up on. It is a **copy**, not a move: the originating [webhook_queue](webhook_queue.md) row survives with status `DEAD_LETTERED` (`RetryExhaustionHandler.java:57-67`), so the column comment "before deletion" (`V1__init.sql:186`) is stale.

## Fields (`V1__init.sql:135-176`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `original_webhook_id` | UUID NOT NULL | the `webhook_queue.id` it came from — logical ref, **no FK** |
| `destination_id` | VARCHAR(255) | logical ref to [destinations](destinations.md) |
| `destination_url`, `destination_url_hash` | TEXT / VARCHAR(64) | snapshot from the original ingestion |
| `source_service` | VARCHAR(100) | audit trail |
| `payload` | JSONB NOT NULL | the body that would have been delivered |
| `retry_config` | JSONB NOT NULL | config in force at the time of failure |
| `attempt_count` | INTEGER NOT NULL | attempts burned |
| `last_response_code`, `last_error_message` | INTEGER / TEXT | last observed outcome |
| `idempotency_key` | VARCHAR(255) NOT NULL | **not unique here** — only an index, `idx_dead_letters_idempotency` (`V3:10`) |
| `priority`, `metadata` | VARCHAR(10) NOT NULL / JSONB | carried over |
| `failure_reason` | TEXT NOT NULL | built by `WebhookQueuePoller.buildExhaustionReason:455-464` |
| `dead_lettered_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | replay/FIFO ordering key |
| `created_at` | TIMESTAMPTZ NOT NULL | *original* queue-row creation time |

HTTP method, content type and headers are intentionally absent — replay re-reads them from Secrets Manager, i.e. **replay uses current config, not the config that failed** (`WebhookDeadLetter.java:17-29`).

## Who reads/writes it
| Operation | Code | Effect |
|---|---|---|
| insert | `RetryExhaustionHandler.handleExhaustion:50-57` | called from `WebhookQueuePoller.handleExhaustion:414-416` on retry exhaustion **or** permanent (4xx) failure (`:404-409`) |
| notify | `DeadLetterEventPublisher.publishDeadLetterEvent:35` | publishes a summary to the webhook-request dead-letter topic — see [webhook dead-letter message](../contracts/webhook-dead-letter-message.md) |
| audit | `AuditService.logWebhookDeadLettered` (`WebhookQueuePoller.java:423-429`) | CloudEvent `com.webhook.audit.webhook.dead_lettered` → [audit-service](../reporting/audit-service.md); this is also one of the two actions that raise a FreshDesk ticket |
| requeue | `WebhookQueue.fromDeadLetter:33` | **has no caller in production code** — the dead-letter replay path is not wired at v1.3.3 |
| purge | `DeadLetterRetentionJob.cleanupExpiredDeadLetters:42`, cron `webhook.dead-letter.cleanup-cron` (default `0 0 2 * * ?`, `:40`) | `deleteByDeadLetteredAtBefore(now - webhook.dead-letter.retention-days)`; neither key is set in `application.yml` or any deployment config, so the effective policy is **delete after 30 days** (`:30`) |

Indexes: `idx_dead_letters_url_hash`, `idx_dead_letters_dead_lettered_at`, `idx_dead_letters_destination_id`, `idx_dead_letters_source_service` (`V1__init.sql:179-182`), `idx_dead_letters_idempotency` (`V3:10`).

**Consumers:** no query API exists (see [webhook-service-http-api](../reporting/webhook-service-http-api.md)) — inspection is direct SQL. The availability CloudEvent only resets `next_attempt_at` on **queue** rows (`WebhookQueueRepository.resetNextAttemptForProbe:171-180`); it does not read this table. Net effect: a dead-lettered webhook is **never retried and is deleted after 30 days** — see ../../disagreements/webhook-destination-suspension-changelog-stale.md (archived).

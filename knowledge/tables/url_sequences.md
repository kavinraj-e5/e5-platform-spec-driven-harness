---
type: table
name: url_sequences
schema: public
database: webhook-service Postgres (DB_NAME)
owner: webhook-service
migration: src/main/resources/db/migration/V1__init.sql:197
entity: com/webhook/service/domain/queue/UrlSequence.java
status: deprecated — still created, never written by the delivery path
provenance: code
verified: 2026-10-02
---

# url_sequences

A **vestigial FIFO counter** from the pre-Postgres-queue design of [webhook-service](../reporting/webhook-service.md). Still created by V1 and still has an entity, repository and custom repository impl, but nothing in the ingestion or delivery path touches it.

## Fields (`V1__init.sql:197-200`)
| Column | Type | Notes |
|---|---|---|
| `destination_url_hash` | VARCHAR(64) PK | SHA-256 of the destination URL |
| `current_sequence` | BIGINT NOT NULL DEFAULT 0 | monotonic counter per URL |

**FKs:** none.

## Who reads/writes it
| Access | Code | Reality |
|---|---|---|
| declared | `UrlSequenceRepository`, `UrlSequenceRepositoryCustom`, `UrlSequenceRepositoryImpl` | the only JPA artefacts for this table; **no class outside this group references them**, nor `UrlHashUtil` |
| not called | `WebhookIngestionService.java:218-242` | `createWebhook` never sets `sequence_number` and never increments a sequence |
| not read | `WebhookQueueRepository.pollReadyWebhooksFifo:111-128` | ordering comes from `DISTINCT ON (destination_id)` + `created_at`, not from a sequence |

So both this table and `webhook_queue.sequence_number` are permanently NULL/empty at v1.3.3.

## Why it matters
The migration comments disagree with the code about *how* FIFO is achieved: `V1__init.sql:127` and `:203` attribute ordering to "Redis Streams", while the shipped mechanism is Postgres `FOR UPDATE SKIP LOCKED` (`V1__init.sql:210`, `WebhookQueuePoller.java:48`). See ../../disagreements/webhook-redis-streams-stale-comments.md (archived).

A future migration is expected to drop this table (`V1__init.sql:195,203`); it has not happened through V5. Removing it is safe today: the three repository classes, `UrlSequence` and `UrlHashUtil` have zero production callers (`UrlSequence.java:13-14` is a managed `@Entity` because its package `domain.queue` is in the `@EntityScan` list at `WebhookServiceApplication.java:36-40`, which is also why `spring.jpa.hibernate.ddl-auto: validate` still requires the table to exist).

**Consumers:** none.

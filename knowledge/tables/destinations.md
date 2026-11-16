---
type: table
name: destinations
schema: public
database: webhook-service Postgres (DB_NAME)
owner: webhook-service
migration: src/main/resources/db/migration/V1__init.sql:16
entity: com/webhook/service/domain/destination/Destination.java
provenance: code
verified: 2026-10-02
---

# destinations

The **routing registry** for outbound webhooks, owned by [webhook-service](../reporting/webhook-service.md). Deliberately holds **no URL and no credentials** — those live in AWS Secrets Manager under `aws.secretsmanager.destinations-path-prefix/{destinationId}` (`V1__init.sql:62`). Not to be confused with deployment-mgmt's `webhooks` registry, which is the subscription table for the *other* dispatch path.

## Fields (`V1__init.sql:16-40`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `destination_id` | VARCHAR(255) NOT NULL | business key, `uq_destination_id` unique; the value senders put in the CloudEvent `destinationid` extension |
| `auth_type` | VARCHAR(20) NOT NULL DEFAULT 'NONE' | `BEARER`, `HEADER`, `HMAC_SHA256`, `NONE` (`V2__add_hmac_sha256_auth_type.sql:5`, enum `DestinationAuthType`) — no DB constraint |
| `source_service` | VARCHAR(100) | copied onto every queue/dead-letter row for audit |
| `retry_config` | JSONB | overrides `RetryConfig` defaults; snapshotted per queue row at ingestion |
| `status` | VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' | enum `DestinationStatus`; anything but `ACTIVE` rejects ingestion |
| `created_at`, `updated_at` | TIMESTAMPTZ NOT NULL | `updated_at` maintained by trigger `destinations_updated_at_trigger` (`:56`) |

**Dropped column:** `webhook_delivery_suspended` was added by `V3__destination_webhook_delivery_suspended.sql:5` and removed by `V5__drop_webhook_delivery_suspended.sql:2` (commit 60385c0). No code reads it today — see ../../disagreements/webhook-destination-suspension-changelog-stale.md (archived).

**FKs:** none. `webhook_queue.destination_id` and `webhook_dead_letters.destination_id` reference this table **logically only** — no FK constraint exists, so a destination can be deleted out from under queued rows.

## Who reads/writes it
| Access | Code | Purpose |
|---|---|---|
| read by `destination_id` | `WebhookIngestionService.java:142` | existence + `ACTIVE` gate before queueing |
| read by `destination_id` | `WebhookHttpClient.java:129-131` | resolve `auth_type` at delivery time (falls back to `NONE` if the row vanished) |
| read | `WebhookIngestionService.java:224-229,279-292` | copy `source_service` and `retry_config` onto the new [webhook_queue](webhook_queue.md) row |

**Writes: none from the application.** There is no destination CRUD API, no seeder and no migration insert — rows must be provisioned out of band (SQL/ops tooling) alongside the matching Secrets Manager entry. Indexes: `idx_destinations_status`, `idx_destinations_destination_id`, `idx_destinations_source_service` (`:43-45`).

**Consumers:** none outside webhook-service; nothing is published when a destination changes.

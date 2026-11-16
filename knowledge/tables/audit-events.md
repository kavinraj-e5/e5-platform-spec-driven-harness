---
type: table
name: audit.events
schema: audit
database: audit-service Postgres (DB_NAME)
owner: audit-service
migration: src/main/resources/db/migration/V1__init.sql:20
entity: com/healthcare/audit/domain/entity/AuditEventEntity.java
partitioning: RANGE (event_time), monthly, child name events_YYYY_MM
provenance: code
verified: 2026-10-02
---

# audit.events

The platform's **append-only audit ledger**, owned by [audit-service](../reporting/audit-service.md). Monthly RANGE partitions on `event_time`; composite PK `(event_id, event_time)` because the partition key must be part of the key (`V1__init.sql:64`).

## Fields (`V1__init.sql:20-65`; all mapped as `String`, no JPA enums — `AuditEventEntity.java:90-105`)
| Column | Type | Source attribute |
|---|---|---|
| `event_id`, `event_time` | UUID / TIMESTAMPTZ, PK | CloudEvent `id` (must be a UUID, `EventValidator.java:74-80`) and `time` |
| `received_at` | TIMESTAMPTZ NOT NULL | set by `EventEnricher` at ingest |
| `spec_version`, `source`, `event_type` | VARCHAR(10/500/200) NOT NULL | CloudEvent `specversion` (must be `1.0`), `source`, `type` |
| `trace_id`, `span_id`, `trace_available` | VARCHAR(64/32), BOOLEAN | W3C `traceparent` from the Kafka header, else the CloudEvent body (`CloudEventsAuditConsumer.java:169-196`) |
| `actor_id` NOT NULL, `actor_type`, `client_ip` | VARCHAR(200/50/45) | `data.actorId` / `actorType` (`USER,SYSTEM,SERVICE,ADMIN`) / `clientIp` |
| `entity_id` NOT NULL, `resource_id`, `resource_type` | VARCHAR(200/200/100) | `data.entityId` / `resourceId` / `resourceType` |
| `action_category` NOT NULL | VARCHAR(50) | must be `CREATE,READ,UPDATE,DELETE,EXECUTE` (`ActionCategory.java:8-12`) |
| `action_name` NOT NULL | VARCHAR(200) | validated to ≤100 chars (`EventValidator.java:133`) — the column is twice that |
| `outcome` NOT NULL | VARCHAR(20) | must be `SUCCESS,FAILURE,PARTIAL`; **stored verbatim in upper case** (`EventEnricher.java:77`) |
| `outcome_reason` | TEXT | free text |
| `sensitivity_level` | VARCHAR(50) | `PUBLIC,INTERNAL,CONFIDENTIAL,PHI` (`SensitivityLevel.java:7-11`) |
| `correlation_id` | VARCHAR(200) | caller-supplied |
| `service_name` NOT NULL, `service_version` | VARCHAR(200/50) | `data.serviceName`, else parsed out of the `source` URI (`CloudEventMapper.java:88-100`) |
| `event_data` | JSONB | the producer's `data.details` map (`CloudEventMapper.java:65`) |

**FKs:** none. `archive_log.partition_name` refers to child partitions of this table by name only.

## Who reads/writes it
| Operation | Code | Notes |
|---|---|---|
| insert | `EventProcessingServiceImpl.java:80` | inside a `@Transactional` method, after validate + dedupe + enrich |
| dedupe read | `AuditEventRepository.existsByEventId` (`EventProcessingServiceImpl.java:65`) | duplicate → skipped, counted on `audit.events.duplicates` |
| trace lookup | `AuditEventRepository.findByTraceIdOrderByEventTimeAsc:51` | no production caller |
| counts | `countByEntityId:131`, `countByEventTimeBetween:137`, `countByEventId:156`, `countByReceivedAtAfter:162` | no production caller |
| streaming export | `streamByEventTimeBetween:175-180` (fetch size 1000) | the only other production reader: `ArchiveService.exportToParquet` → Parquet → S3 |
| partition create | `audit.ensure_partitions_exist(3)` at migration time (`V1__init.sql:222`) | **nothing re-runs it** — partitions expire ~3 months after deploy |
| partition drop | `ArchiveService.cleanupOldPartitions` (`:107`) | only when `aws.s3.enabled=true` |

Indexes: `idx_events_{entity,trace,actor,resource,service,type}_time`, two `INCLUDE` composites, a GIN index on `event_data`, expression indexes on `event_data->>'ip_address'` and `->>'session_id'`, and `idx_events_failed ... WHERE outcome = 'failure'` — which matches nothing, because values are upper case. See ../../disagreements/audit-failed-events-index-case-mismatch.md (archived).

**Consumers:** no API reads this table ([audit-service-http-api](../reporting/audit-service-http-api.md)); analysts query Postgres or the [archive_log](audit-archive-log.md)-registered Parquet files directly. Events arrive via [audit-event message](../contracts/audit-event-message.md).

---
type: table
name: audit.archive_log
schema: audit
database: audit-service Postgres (DB_NAME)
owner: audit-service
migration: src/main/resources/db/migration/V1__init.sql:113
entity: com/healthcare/audit/domain/entity/ArchiveLogEntity.java
provenance: code
verified: 2026-10-02
---

# audit.archive_log

The **ledger of S3 cold-archive artefacts** for [audit-service](../reporting/audit-service.md): one row per `audit.events` partition that has been written to Parquet and uploaded with Object Lock. It is the only durable record of which S3 object covers which date range — if a row is missing, the Parquet file is effectively unfindable.

## Fields (`V1__init.sql:113-144`)
| Column | Type | Notes |
|---|---|---|
| `archive_id` | UUID PK | `gen_random_uuid()` |
| `partition_name` | VARCHAR(100) NOT NULL | `events_YYYY_MM`; `uk_archive_partition` unique → an archive run is idempotent per partition only because this constraint rejects the second insert |
| `partition_start`, `partition_end` | DATE NOT NULL | parsed from `pg_get_expr(relpartbound)` (`PartitionRepository.findEligiblePartitions:42-67`) |
| `event_count` | BIGINT NOT NULL | **actual** rows streamed, not the pre-count (`ArchiveService.java:134,149`) |
| `s3_bucket`, `s3_key` | VARCHAR(200/500) NOT NULL | `uk_archive_s3_key` unique on the pair |
| `file_size_bytes` | BIGINT NOT NULL | `Files.size(parquetFile)` (`ArchiveService.java:152`) |
| `checksum` | VARCHAR(64) NOT NULL | SHA-256 returned by `S3ArchiveClient.uploadWithObjectLock` (`:139`) |
| `archived_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | — |
| `verified`, `verified_at` | BOOLEAN NOT NULL / TIMESTAMPTZ | `S3ArchiveClient.verifyUpload` (`:142`); `chk_verified_at` forces the timestamp when true |
| `partition_dropped`, `dropped_at` | BOOLEAN NOT NULL / TIMESTAMPTZ | set by the cleanup pass; `chk_dropped_at` pairs them |

**FKs:** none — `partition_name` is a soft reference to a child partition of [audit.events](audit-events.md).

## Who reads/writes it
| Operation | Code | Notes |
|---|---|---|
| insert | `ArchiveService.archivePartition:145-158` | the whole method is `@Transactional`, so a failed S3 verify still records `verified=false` rather than rolling back |
| update | `ArchiveLogRepository.markPartitionDropped` from `ArchiveService.cleanupOldPartitions:223-247` | sets `partition_dropped`/`dropped_at` after `PartitionRepository.dropPartition`; per-partition drop failures are caught and logged (`:240-242`) |
| read | `PartitionRepository.findPartitionsForCleanup` (`:227`) | selects which archived+verified partitions may be dropped |

**Everything here is inert unless `aws.s3.enabled=true`**: `ArchiveService` is `@ConditionalOnProperty(..., matchIfMissing=false)` (`:35`) and `application.yml:129` defaults the flag to `false`, with `AWS_S3_ENABLED` blank in every committed deployment config. See ../../disagreements/audit-s3-archive-off-by-default.md (archived).

Indexes: `idx_archive_log_partition_start`, `idx_archive_log_archived_at`, `idx_archive_log_verified WHERE verified = false` (`V1__init.sql:147-149`).

**Consumers:** none programmatic — compliance/restore work reads this table by hand. Archive failures surface only as the counters `audit.archive.partitions.failed` / `audit.archive.jobs.failed` (`ArchiveService.java:100,114`).

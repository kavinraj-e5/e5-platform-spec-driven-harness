---
type: table
name: shedlock
schema: public
database: audit-service Postgres (DB_NAME)
owner: audit-service
migration: src/main/resources/db/migration/V1__init.sql:228
entity: none — managed by net.javacrumbs ShedLock JdbcTemplateLockProvider
provenance: code
verified: 2026-10-02
---

# shedlock

The **cross-pod mutex** for [audit-service](../reporting/audit-service.md)'s scheduled work. Note it lives in the `public` schema, not `audit` (`V1__init.sql:228`) — the only audit-service table outside its own schema.

## Fields (`V1__init.sql:228-234`)
| Column | Type | Notes |
|---|---|---|
| `name` | VARCHAR(64) PK | the lock name; today only one value is ever used: `runArchiveJob` (`ArchiveService.java:79`) |
| `lock_until` | TIMESTAMP NOT NULL | lease expiry — `lockAtMostFor` |
| `locked_at` | TIMESTAMP NOT NULL | lease start — `lockAtLeastFor` is measured from here |
| `locked_by` | VARCHAR(255) NOT NULL | the holder's instance id |

Columns are `TIMESTAMP` **without** time zone, unlike every `audit.*` table which uses `TIMESTAMPTZ`. That is safe here only because the provider is built `.usingDbTime()` (`SchedulerConfig.java:35`), so the database clock — not each pod's JVM clock — decides lease expiry.

**FKs:** none.

## Who reads/writes it
| Operation | Code | Notes |
|---|---|---|
| lock table wiring | `SchedulerConfig.java:23` `@EnableSchedulerLock(defaultLockAtMostFor="PT30M")`, `:31-38` `JdbcTemplateLockProvider` over the app `DataSource` with `.usingDbTime()` | the provider upserts/reads this table; the table name is ShedLock's default, matching `V1__init.sql:228` |
| only guarded job | `ArchiveService.runArchiveJob` (`:77-83`) with `name="runArchiveJob"`, `lockAtLeastFor=PT5M`, `lockAtMostFor=PT2H` | the per-job overrides win over the 30-minute default |

**Unguarded scheduled work:** `ArchiveService.triggerArchiveJob:118-122` calls `runArchiveJob()` by **self-invocation**, which bypasses the ShedLock proxy entirely — a manual trigger acquires no lock and can run concurrently with the cron on another pod. (The same self-invocation hazard is acknowledged for transactions at `ArchiveService.java:62-67` but not for locking.) `triggerArchiveJob` has no production caller today, so the risk is latent.

Because `ArchiveService` is `@ConditionalOnProperty(aws.s3.enabled=true)` (`:35`) and the flag defaults to `false`, **this table is empty in any environment where S3 archiving is off** — which is every committed deployment config.

**Consumers:** none — operational use only (`SELECT * FROM shedlock` to see whether an archive run is in flight or a lease is stuck until `lock_until`).

---
type: table
name: scheduler_task
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.SchedulerTaskEntity
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/SchedulerTaskEntity.java:13
pk: id
created_by: V1_2025_11_24_003__create_scheduler_task_table.sql
provenance: code
verified: 2026-10-02
---

# `scheduler_task`

**An audit row per run of DMS's own Quartz jobs** — "did the nightly
[`workflow_run`](workflow_run.md) materialisation actually happen, and for which deployment".
Written only by DMS itself; **no rpc exposes it**.

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md).

## Columns

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, DEFAULT `uuid_generate_v4()` |
| `scheduler_type` | varchar(50) | NOT NULL — which job ran |
| `deployment_version_id` | uuid | NULL-able and **declares no FK**, in either the SQL or the entity (`:31-32`) |
| `owner` | varchar(100) in SQL / **`length = 50` in the entity** (`:34-35`) | NOT NULL — ⚠ the entity's length is half the column's, so Hibernate validation of a 51-char owner fails before the DB would |
| `status` | pg enum `scheduler_task_status` | NOT NULL DEFAULT `'YET_TO_START'`; values `YET_TO_START`, `IN_PROGRESS`, `COMPLETED`, `FAILED` (`V1_2025_11_24_002`) |
| `created_at`, `updated_at` | timestamp | DEFAULT `CURRENT_TIMESTAMP` |

**No indexes beyond the PK** — there is no index on `status` or `created_at`, so "did last
night's job finish" is a sequential scan.

## Who writes it

Only `BaseTask`, the Quartz superclass:
`schedulerTaskRepository.save(...)` at `BaseTask.java:60`, then
`update(...)` at `:68` and `:75` to move the row through its statuses.
Callers: `DailySchedulerTask` (cron `cron.scheduler.populateRunRecords`) and
`GenerateRunRecordsTask` (one-shot after `SaveDeploymentDetails`).

## Gotchas

- **Not exposed over gRPC at all.** Nothing outside the DMS pod can read job history; diagnosis
  requires direct DB access.
- `scheduler_task_status` is a *different* enum from `workflow_run_status` and from
  `event_status`, yet shares the value names `YET_TO_START`/`COMPLETED`/`FAILED` — three
  independent Postgres types with overlapping vocabularies.
- Because Quartz uses the default **in-memory** `StdSchedulerFactory` (`TaskScheduler.java:37`),
  N DMS replicas produce N `scheduler_task` rows for the same nightly run.

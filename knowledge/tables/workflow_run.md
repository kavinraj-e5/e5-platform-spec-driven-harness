---
type: table
name: workflow_run
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.WorkflowRunEntity
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/WorkflowRunEntity.java:21
pk: workflow_run_id
created_by: pre-Flyway baseline
provenance: code
verified: 2026-10-02
---

# `workflow_run`

**One row per scheduled or ad-hoc execution of a workflow.** The busiest table in the platform
and the one every subsystem reads: monitoring-service polls it, workflow-manager reads it,
interface paginates it, and DMS's own Quartz job writes it.

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md). No other service has a
connection to it — all access is through
[gRPC](../subsystems/deployment-mgmt-grpc-api.md#1-workflow-runs--12-rpcs--workflow_run-temporal_workflow).

**Created by:** not by any migration in this repo — it predates the Flyway baseline
(see dms-core-schema-predates-flyway (archived)).
Only *alterations* are versioned.

## Columns

| Column | Type | Notes |
|---|---|---|
| `workflow_run_id` | uuid | **PK**, `@GeneratedValue`, not updatable (`:26-29`) |
| `workflow_id` | uuid | "a UUID from another table but **not** a relationship" — no FK (`:31-33`) |
| `deployment_version_id` | uuid | **FK → `deployment_version`**, `nullable = false`, `@ManyToOne LAZY` (`:36-38`) |
| `schedule_id` | uuid | FK → `schedule`, `@ManyToOne` **EAGER** (`:40-42`) — every run load drags its schedule |
| `workflow_input` | jsonb | mapped as `String` with `@Type("jsonb")` (`:45-47`) |
| `start_time` | timestamp | actual start (`:49-50`) |
| `expected_start_time` | timestamp | from the cron expression (`:52-53`) |
| `end_time` | timestamp | (`:55-56`) |
| `invocation_type` | pg enum | `@Type("pgsql_enum")`, `nullable = false` (`:59-62`) |
| `status` | pg enum `workflow_run_status` | `nullable = false`, DEFAULT `'YET_TO_START'` (`:65-68`) |
| `event_status` | varchar | plain String, **not** an enum (`:70-71`) |
| `remarks` | text | (`:73-74`) |
| `workflow_trigger_info` | text | added `V1_2026_02_15_001` — the newest column in the schema (`:76-77`) |
| `created_at`, `updated_at` | timestamp | from `BaseEntity.java:17-22`, Hibernate-managed |

**Constraints:** `uq_expected_start_time_schedule UNIQUE (expected_start_time, schedule_id)`
(`V1_2025_11_24_001`) — this is what makes schedule materialisation idempotent.

**Inverse relation:** `@OneToMany` → `temporal_workflow` (`:79-80`).

## `workflow_run_status` enum history

| Migration | Change |
|---|---|
| `V1_2025_09_16_001` | `+ PARTIALLY_TERMINATED` |
| `V1_2025_09_30_001` | `+ YET_TO_START` |
| `V1_2025_09_30_002` | data: `NOT_STARTED` ∧ `event_status='NOT_EMITTED'` → `YET_TO_START`; column DEFAULT → `YET_TO_START` |
| `V1_2025_09_30_003` | data: `STARTED` → `IN_PROGRESS` |

⚠ Postgres enum values are never removed. `NOT_STARTED` and `STARTED` still exist in the type
and a stale client can still write them; only the *data* was migrated.

## Who writes it

| Writer | Path |
|---|---|
| DMS Quartz `DailySchedulerTask` | cron `cron.scheduler.populateRunRecords` (default `0 45 23 * * ?`), gated on `populateRunRecords.isEnabled`; `BaseTask.java:30` |
| DMS Quartz `GenerateRunRecordsTask` | one-shot, fired by `SaveDeploymentDetailsUseCase.java:68` |
| monitoring-service | `updateWfRunStatus`, `updateWfRunEventStatus` |
| workflow-ops | `SaveAdhocWorkflowRun`, `UpdateWorkflowAndScheduleStatus` |
| [credential-monitor](../subsystems/credential-monitor.md) | `updateWfRunStatus`, `updateWfRunEventStatus` — **fork baggage, not credential logic** |

## Who reads it

interface (`ListWorkflowRuns`), workflow-manager (`GetWorkflowRun`, `GetAllTemporalWFRecords`),
monitoring-service (`getYetToStartWorkflows`, `getWFRunListForDeploymentID`,
`getWFRunRecordWithPayload`), workflow-ops (`GetDeploymentDetailsWithId`).

## Gotchas

- `ListWorkflowRuns` is **raw native SQL with a `StringBuilder`-assembled WHERE clause**
  (`WorkflowRunRepositoryImpl.java:390,441`), separate count query at `:441`.
- `getDeployment`, `GetConfig` and `getDeploymentId` all resolve their answer *through* this
  table rather than from `deployment_version` — a run row is required for them to succeed.
- The `schedule_id` FK is nullable (ad-hoc runs), but the UNIQUE constraint includes it, so
  Postgres allows unlimited ad-hoc rows with the same `expected_start_time`.

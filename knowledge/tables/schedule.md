---
type: table
name: schedule
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.ScheduleEntity
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/ScheduleEntity.java:17
pk: schedule_id
created_by: pre-Flyway baseline
provenance: code
verified: 2026-10-02
---

# `schedule`

**The cron definitions that DMS's own Quartz job turns into
[`workflow_run`](workflow_run.md) rows.** This is the only place in the platform where a cron
expression is stored; AKST and the workers never see one.

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md).

## Columns

| Column | Type | Notes |
|---|---|---|
| `schedule_id` | uuid | **PK**, `@GeneratedValue`, not updatable (`:24-27`) |
| `runtime_config_id` | uuid | FK → `runtime_config.config_id`, `@ManyToOne LAZY` (`:30-32`) |
| `deployment_version_id` | uuid | FK → `deployment_version`, `@ManyToOne LAZY` (`:34-37`) |
| `description` | text | (`:39-40`) |
| `cron_expression` | text | parsed by `net.redhogs.cronparser:cron-parser-core:3.5` |
| `workflow_input` | jsonb | mapped as `String`, copied into each generated run (`:45-47`) |
| `timezone` | varchar | the cron's zone (`:49-50`) |
| `trigger_id` | varchar | **a String, not a FK** — the external trigger handle (`:52-53`) |
| `status` | pg enum `ScheduleStatus` | `nullable = false` (`:58-61`) |
| `created_at`, `updated_at` | timestamp | `BaseEntity` |

**Relations:** `@OneToMany(mappedBy = "schedule", LAZY)` → `workflow_run` (`:55-56`). The
inverse side on `workflow_run` is **EAGER** (`WorkflowRunEntity.java:40-42`), so the
directionality of fetching is asymmetric.

## Materialisation — the thing to understand first

| Job | Trigger | Code |
|---|---|---|
| `DailySchedulerTask` | Quartz cron `cron.scheduler.populateRunRecords`, default `0 45 23 * * ?`, scheduled at `Main.java:22` | reads `scheduleRepository.getAllActiveSchedules()` (`DailySchedulerTask.java:30`), writes runs via `BaseTask.java:30` |
| `GenerateRunRecordsTask` | one-shot, fired by `SaveDeploymentDetailsUseCase.java:68` after a deployment is saved | `scheduleRepository.getAllActiveSchedulesByDeploymentId(...)` (`GenerateRunRecordsTask.java:33`) |

Each run is bookkept in [`scheduler_task`](scheduler_task.md) (`BaseTask.java:60,68,75`).
Idempotency comes from `workflow_run`'s
`uq_expected_start_time_schedule UNIQUE (expected_start_time, schedule_id)`.

⚠ **The `populateRunRecords.isEnabled` default is inconsistent across the three read sites:**
`"false"` at `DailySchedulerTask.java:26` and `UpdateWorkflowAndScheduleStatusUseCase.java:158`,
but `"true"` at `GenerateRunRecordsTask.java:29`. With the key unset, deployment-time
materialisation runs while nightly materialisation does not.

## RPCs

`GetScheduleTriggers`, `UpdateWorkflowAndScheduleStatus`, `UpdateScheduleStatusTemp`,
`GetWorkflowScheduleInfo`, `ScheduleAdhocWorkflowRun`, and the schedule-writing half of
`SaveDeploymentDetails` / `CreateDeploymentVersion`.
See [deployment-mgmt-grpc-api](../subsystems/deployment-mgmt-grpc-api.md#4-schedules--6-rpcs--schedule-workflow_run).

## Who reads / writes

workflow-ops drives all of it: `GetScheduleTriggers`, `UpdateWorkflowAndScheduleStatus`,
`UpdateScheduleStatusTemp`, `SaveDeploymentDetails`, `SaveAdhocWorkflowRun`, `GetRuntimeConfig`.
devops-pipeline writes via `CreateDeploymentVersion`. `ScheduleAdhocWorkflowRun` and
`GetWorkflowScheduleInfo` have no call site.

## Gotchas

- `UpdateScheduleStatusTemp` — the name is literal; "Temp" is in the wire contract and
  workflow-ops calls it in production (`toggle_trigger_service.py:41`).
- Quartz runs with the **default in-memory `StdSchedulerFactory`** (`TaskScheduler.java:37`), so
  with more than one DMS replica every replica materialises the same schedules; only the UNIQUE
  constraint prevents duplicate rows.

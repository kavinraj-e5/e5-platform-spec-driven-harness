---
type: table
name: workflow_manager_state
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.WorkflowManagerStateEntity
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/WorkflowManagerStateEntity.java:17
pk: workflow_manager_state_id
created_by: V1_2025_07_09_002__create_workflow_manager_state.sql
provenance: code
verified: 2026-10-02
---

# `workflow_manager_state`

**The signal-counting ledger for pause/resume/terminate/scale-worker actions.**
workflow-manager fans a lifecycle action out to N app pods and uses this row to count how many
have answered; when `received_signal_response_count == total_signal_count` the action is done.

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md). **Sole consumer:**
workflow-manager (plus one read from interface).

## Columns

| Column | Type | Notes |
|---|---|---|
| `workflow_manager_state_id` | uuid | **PK** (`:21-24`) |
| `deployment_version_id` | uuid | **FK → `deployment_version`**, `fk_deployment_version` (`:26-28`) |
| `reference_id` | varchar(255) | correlates the action to the originating task (`:30-31`) |
| `app_name` | varchar(255) | (`:33-34`) |
| `workflow_manager_action` | varchar(255) | **a free String, not an enum** (`:36-37`) |
| `action_start_date_and_time` | **varchar(255)** | ⚠ a timestamp stored as text — cannot be range-queried or indexed as time (`:39-40`) |
| `active` | boolean | no `@Column`, so Hibernate derives the name (`:42`) |
| `total_signal_count` | int | NOT NULL DEFAULT 0 (`:44-46`) |
| `received_signal_response_count` | int | NOT NULL DEFAULT 0 (`:48-50`) |
| `workflow_run_id` | uuid | added `V1_2025_09_03_001`; **the FK was added then dropped again** by `V1_2025_10_14_001` — it is now an unenforced reference (`:52-53`) |
| `timeout_in_minutes` | int | NOT NULL **DEFAULT 65**, added `V1_2025_09_03_001` (`:55-57`) |
| `metadata` | jsonb | NOT NULL DEFAULT `'{}'`, added `V1_2025_12_06_001`; the scale-worker payload lives here (`:59-61`) |
| `created_at`, `updated_at` | timestamp | NOT NULL, `BaseEntity` |

## Migration history

| Migration | Change |
|---|---|
| `V1_2025_07_09_002` | CREATE, with `fk_deployment_version` |
| `V1_2025_09_03_001` | `+ workflow_run_id` **with** `fk_workflow_run`; `+ timeout_in_minutes DEFAULT 65` |
| `V1_2025_10_14_001` | **DROP `fk_workflow_run`** — the comment says only "Remove foreign key constraint from workflow_run_id" |
| `V1_2025_12_06_001` | `+ metadata jsonb NOT NULL DEFAULT '{}'` |

## RPCs — 10, all read/written by workflow-manager

`SaveWorkflowManagerRunStatus`, `GetWorkflowManagerState`,
`GetAllActiveWorkflowManagerRunBeforeTime`, `UpdateWorkflowManagerState`,
`IncrementReceivedSignalWorkflowManagerState`, `UpdateWorkflowManagerStateActive`,
`AddWorkflowManagerStateMetadata`, `UpdateScaleWorkerMetadataStatus`,
`GetWorkflowManagerStateMetadata`, `GetActiveWorkflowManagerRun` (also read by interface).

## Gotchas

- **Three of the mutating paths are raw native SQL**, not JPA:
  `WorkflowManagerStateRepositoryImpl.java:83` (a native SELECT over a `source_data` CTE),
  `:177` and `:221` (native `UPDATE`s). The signal counter is therefore incremented **outside
  Hibernate's dirty-checking and outside any optimistic lock** — this table has no `@Version`.
- Because `action_start_date_and_time` is text, `GetAllActiveWorkflowManagerRunBeforeTime`
  ("before time") must do its comparison in the native query rather than on an indexed timestamp.
- Dropping `fk_workflow_run` means a state row can outlive its run with nothing to detect it.

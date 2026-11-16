---
type: table
name: workflow_ops_state
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.WorkflowOpsStateEntity
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/WorkflowOpsStateEntity.java:19
pk: ops_state_id
created_by: V1_2025_09_03_002__create_workflow_ops_state.sql
provenance: code
verified: 2026-10-02
---

# `workflow_ops_state`

**Durable state for long-running workflow-ops tasks** (export/import state, deployment actions,
trigger toggles) with a timeout and **the only optimistic lock in the entire DMS schema**.

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md). **Sole consumer:**
workflow-ops (Python).

## Columns

| Column | Type | Notes |
|---|---|---|
| `ops_state_id` | uuid | **PK** — was named `task_id` until `V1_2025_09_20_003` renamed it (`:23-26`) |
| `task_id` | **varchar(255)** | was `request_id`, then renamed to `task_id`, then **retyped from uuid to varchar(255)** by `V1_2026_01_09_001` "to support both UUID and Long-based task IDs" (`:28-29`) |
| `task_name` | varchar(255) | (`:31-32`) |
| `status` | pg enum `event_status` | `ACTIVE`, `IN_ACTIVE`, `COMPLETED`, `FAILED`; declared `length = 15` *and* `@Enumerated(STRING)` *and* `@Type("pgsql_enum")` (`:34-37`) |
| `task_payload` | jsonb | mapped as `String` (`:39-41`) |
| `timeout` | timestamp | mapped to `java.time.Instant`, i.e. **`timestamp without time zone` read as an instant** (`:43-44`) |
| `version` | bigint | NOT NULL DEFAULT 0, **`@Version`** (`:46-48`) — added `V1_2025_09_19_001` |
| `created_at`, `updated_at` | timestamp | NOT NULL, `BaseEntity` |

**Indexes:** `idx_workflow_ops_state_task_id`, `idx_workflow_ops_state_status`,
`idx_workflow_ops_state_timeout`, `idx_workflow_ops_state_version`.
**No foreign keys at all** — `task_id` points at nothing enforceable.

## Migration history — three renames and a retype

| Migration | Change |
|---|---|
| `V1_2025_09_03_002` | CREATE TYPE `event_status`; CREATE TABLE with PK `task_id`, column `request_id` |
| `V1_2025_09_19_001` | `+ version bigint NOT NULL DEFAULT 0` + index |
| `V1_2025_09_20_003` | `task_id` → `ops_state_id` (PK), `request_id` → `task_id`; PK constraint recreated |
| `V1_2026_01_09_001` | `task_id` uuid → **varchar(255)** `USING task_id::VARCHAR(255)` |

Anyone reading an old design doc will find `request_id` and a uuid `task_id`; both names moved.

## RPCs — 4

`insertOpsTaskState`, `GetOpsTaskState`, `UpdateOpsTaskState`, `GetActiveOpsTaskStateDetails`.
All four are called from workflow-ops' `src/helpers/async_workflow.py` (`:28,67,116,165`).
`GetDeployedVersionDetails` is grouped with them in the proto comments but reads
`deployment_state`/`deployment_version` instead.

## Gotchas

- **`@Version` means `UpdateOpsTaskState` can lose.** Two concurrent updates to the same
  `ops_state_id` produce an `OptimisticLockException`; because DMS opens a **fresh Hibernate
  session per rpc** (`HibernateSessionUtil.execute`) there is no retry wrapper — the caller sees
  the failure.
- `timeout` is `timestamp without time zone` mapped to `Instant`, so the JVM's zone silently
  participates in every comparison.
- The retype of `task_id` to varchar means `insertOpsTaskState` will now accept a non-UUID
  string that older readers cannot parse.

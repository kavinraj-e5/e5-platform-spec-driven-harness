---
type: table
name: task_sdk
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.TaskSDKEntity
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/TaskSDKEntity.java:15
pk: id
created_by: V1_2025_08_11_007__create_task_sdk.sql
provenance: code
verified: 2026-10-02
---

# `task_sdk`

**An artifact-version registry — "which versions of `e5-task-sdk` exist and which is stable".**
It is **not** the ETP task table (that is [`interface_task`](interface_task.md)) and not a task
queue. The name is a frequent source of confusion, made worse by the migration that creates it.

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md).

## Columns

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, DEFAULT `uuid_generate_v4()` — the only table here using `uuid_generate_v4()` rather than `gen_random_uuid()`, so it requires the `uuid-ossp` extension |
| `version` | varchar(50) | NOT NULL |
| `type` | varchar(50) | NOT NULL — the only artifact registry with a `type` |
| `stable` | boolean DEFAULT false | ⚠ the entity maps it as **`String`** (`TaskSDKEntity.java:30-31`) |
| `created_at`, `updated_at` | timestamp | DEFAULT `now()` |

## ⚠ The migration is self-inconsistent

`V1_2025_08_11_007__create_task_sdk.sql` creates `public.task_sdk` but:
- its leading comment says `-- Table: public.task` (`:1`)
- its PK constraint is named **`task_pkey`**, not `task_sdk_pkey` (`:14`)
- it ends with `ALTER TABLE IF EXISTS public.task OWNER to platform_support_user;` (`:19`) —
  **referencing a table named `task` that does not exist**, which the `IF EXISTS` silently swallows

So a `grep` for `public.task` in the migrations finds a hit that creates nothing, and the PK
constraint name in `pg_constraint` will not match the table name. Any tool that infers table
names from constraint names will report a phantom `task` table.

## Sibling registries

`task_sdk` is one of eight tables sharing the shape `(id uuid PK, version varchar(50), stable
boolean, created_at, updated_at)`:

| Table | Entity | Divergence |
|---|---|---|
| `task_sdk` | `TaskSDKEntity.java:15` | `+ type`; `stable` mapped as String; `uuid_generate_v4()` |
| `platform_core` | `PlatformCoreEntity.java:14` | `stable` is **NOT NULL** here only |
| `fb_core` | `FBCoreEntity.java:14` | — |
| `common_util` | `CommonUtilEntity.java:14` | `stable` mapped as **String** |
| `state_service` | `StateServiceEntity.java:22` | `stable` mapped as **String** |
| `notification_hub` | `NotificationHubEntity.java:14` | — |
| `non_rpa_worker_core` | **none** | migration-only, **no entity, no repository — unreachable** |
| `dependency` | `DependencyEntity.java:17` | different shape: `service_id`, `service_type`, `key`, `value` (entity says `jsonb`, SQL says `text`) |

## RPCs

**None.** No rpc reads or writes `task_sdk`, and there is no repository for it. It is registered
in the Hibernate SessionFactory (`HibernateUtil.java:62-109`, **twice**) and populated out of
band by the artifact-onboarding pipeline. The only sibling with an rpc is `dependency`, read by
`validateScaleUpDownSafeDeployment`.

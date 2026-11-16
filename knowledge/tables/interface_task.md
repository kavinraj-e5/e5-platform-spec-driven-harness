---
type: table
name: interface_task
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.InterfaceTaskEntity
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/InterfaceTaskEntity.java:22
pk: task_id
created_by: pre-Flyway baseline
provenance: code
verified: 2026-10-02
---

# `interface_task`

**This is "the task table".** If a doc or conversation says "the task table" in the context of
the Transport-Task-Protocol, it means `interface_task` — the row behind every
`PerformTask` → `GetTaskStatus` → `UpdateTaskResponse` round trip. It is **not**
[`task_sdk`](task_sdk.md) (an artifact-version registry that merely sounds like it) and **not**
`task_route` (which does not exist at this commit).

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md).

## Columns

| Column | Type | Notes |
|---|---|---|
| `task_id` | uuid | **PK**, `@GeneratedValue`, not updatable (`:26-29`) |
| `task_request` | jsonb | the inbound payload, mapped as `String` (`:31-33`) |
| `task_response` | jsonb | written by `UpdateTaskResponse`; `null` until the async worker answers (`:35-37`) |
| `is_long_running` | boolean | decides whether the caller must poll (`:39-40`) |
| `deployment_id` | uuid | **no FK declared** (`:42-43`) |
| `task_type` | varchar | the ETP task type (`E5_WF_MANAGER`, `E5_MONITOR`, `E5_WORKFLOW_OPS`, …) — a free String, no enum (`:45-46`) |
| `source` | varchar | ETP routing source (`:48-49`) |
| `destination` | varchar | ETP routing destination (`:51-52`) |
| `created_at`, `updated_at` | timestamp | `BaseEntity` |

## The ETP triad

| rpc | Effect | Repository |
|---|---|---|
| `PerformTask` | INSERT; mints `task_id`; also reads `deployment_version` and `application` (`PerformTaskUseCase`) | `InterfaceTaskRepositoryImpl` |
| `GetTaskStatus` | SELECT by `task_id` | ″ |
| `UpdateTaskResponse` | UPDATE `task_response` | ″ |

[interface](../subsystems/interface.md) is the dominant caller: **every async GraphQL mutation**
does `PerformTask` then Kafka-produces, and `taskStatus` polls `GetTaskStatus` and dispatches on
`task_type` into 10 response shapes. monitoring-service, credential-monitor and
workflow-manager write back through `UpdateTaskResponse`.

## Who reads / writes

| Service | RPCs |
|---|---|
| interface | `PerformTask`, `GetTaskStatus`, `UpdateTaskResponse` |
| workflow-manager | `UpdateTaskResponse` |
| monitoring-service | `PerformTask`, `UpdateTaskResponse` |
| credential-monitor | `PerformTask`, `UpdateTaskResponse` (fork baggage; `performTask` would NPE on `setDeploymentId(null)` but has no caller) |
| dynamic-scaling | ⚠ **none that work.** Its `DeploymentManagementServiceBlockingStub` calls eight rpcs that do not exist here — see dms-proto-forks-disagree-on-service-identity (archived) |

## Gotchas

- **The row and the Kafka publish are not transactional.** interface inserts here, then produces
  to the task-manager topic; a Kafka failure after a successful `PerformTask` **orphans the row**
  and the exception is swallowed (`TaskManagerHelperImpl.java:109-111`).
- `deployment_id` has no FK and is named `deployment_id` while almost every other table in this
  schema correlates on `deployment_version_id` — easy to mis-join.
- `task_type` is a free string and is the key of two unsynchronised switch statements in
  interface — see
  unsynchronised-routing-switches (archived).
- There is **no status column**. "Done" is expressed only as `task_response IS NOT NULL`, so a
  task that failed and one that has not been picked up are indistinguishable in SQL.

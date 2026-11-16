---
type: table
name: task_route
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.TaskRouteEntity
entity_file: NOT PRESENT at release/v1.5.0 — present at release/v1.8.2
pk: id
created_by: V1_2026_08_03_001__create_task_route.sql (v1.8.2 only)
status: absent-at-pinned-commit
provenance: code
verified: 2026-10-02
---

# `task_route` — **does not exist at the pinned DMS commit**

This card exists so the question stops being asked. The Global Router design document describes
a `task_route` table in [deployment-mgmt](../subsystems/deployment-mgmt.md) with columns
`(id, namespace, virtual_queue_name, target_topic, registered_at, active)` plus rpcs
`GetTaskRoute` / `UpsertTaskRoutes` / `DeactivateTaskRoute`.

**The document is correct. The pinned commit is not the commit it describes.**

| At `release/v1.5.0` (`75bbdb3`) — what `../../registry.yaml` pins | At `release/v1.8.2` (`9d1c710`, 2026-09-17) |
|---|---|
| no `task_route` migration | `src/main/resources/db/migration/V1/2026/08/task_route/V1_2026_08_03_001__create_task_route.sql` |
| no `TaskRouteEntity` | `data/entity/TaskRouteEntity.java:22` |
| no `TaskRouteRepository` | `domain/repository/TaskRouteRepository.java`, `data/repository/TaskRouteRepositoryImpl.java` |
| no `GetTaskRouteUseCase` | `domain/usecase/GetTaskRouteUseCase.java` |
| 93 rpcs, none mentioning routes | 121 rpcs incl. `GetTaskRoute` `:2001`, `UpsertTaskRoutes` `:2002`, `DeactivateTaskRoute` `:2003` |

Full write-up: task-route-absent-in-pinned-dms-commit (archived).

## The v1.8.2 definition (for when the pin moves)

`V1_2026_08_03_001__create_task_route.sql`, header comment:
*"Routing data owner for Global Router (PIP-1012 / Phase 03)"*.

| Column | Type | Notes |
|---|---|---|
| `id` | **BIGSERIAL** | PK — the **only sequence-backed PK in the DMS schema**; entity uses `@GeneratedValue(strategy = IDENTITY)` (`TaskRouteEntity.java:25`) |
| `namespace` | text NOT NULL DEFAULT `''` | empty string, **not null**, is the "any namespace" sentinel |
| `virtual_queue_name` | text NOT NULL DEFAULT `''` | same sentinel convention |
| `target_topic` | text NOT NULL | the Kafka topic the VQ resolves to |
| `registered_at` | timestamptz NOT NULL DEFAULT `NOW()` | ⚠ **`timestamptz`** — every other timestamp in this schema is `timestamp without time zone` |
| `active` | boolean NOT NULL DEFAULT TRUE | `DeactivateTaskRoute` flips this; rows are never deleted |

**Constraint:** `uq_task_route_ns_vq UNIQUE (namespace, virtual_queue_name)` — this is the
upsert key for `UpsertTaskRoutes`.
**Indexes:** `idx_task_route_active`, `idx_task_route_vq`, `idx_task_route_ns`,
`idx_task_route_lookup (namespace, virtual_queue_name)`.
**No foreign keys** — `namespace` is a Kubernetes namespace *string*, not a platform id.

`GetTaskRoute`'s lookup is documented in the proto itself
(`deployment_management.proto:2422-2425`, v1.8.2): namespace only → `WHERE namespace = ?`;
vq only → `WHERE virtual_queue_name = ?`; both → `AND`.

## It is already in the database

`platform-whole-context/local-dev/baseline_full_schema.sql:1065-1074` — a `pg_dump` of the real
platform database — **contains `public.task_route` with exactly these columns, the
`uq_task_route_ns_vq` constraint and all four indexes**. So the table is live; only the pinned
source commit is behind.

⚠ **No code anywhere under the workspace root reads or writes it** except DMS v1.8.2. A
repo-wide grep for `task_route|TaskRoute|virtual_queue_name|target_topic` finds hits only in
that checkout, the baseline dump, and KB documents.

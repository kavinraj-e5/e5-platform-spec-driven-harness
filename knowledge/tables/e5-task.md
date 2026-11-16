---
type: table
name: e5_task
owner: akst
repo: e5-platform-core
repo_path: platform-whole-context/platform-workflow-building/platform-core
branch: release/v1.10.1
commit: 377cd53d
schema: ${dbconfig.schemaName} (interpolated at query build time)
ddl_source: Hibernate @Entity — no Flyway, no Liquibase, no committed DDL
dequeue_sql_source: StateService-1.2.8.jar e5/stateservice/service/TaskProcessor.buildQuery
provenance: code
verified: 2026-10-04
prose_reviewed_at_commit: a5f3c031
---

# `e5_task` — the AKST task table

The **only** table AKST owns. One row per delegated task. It is created by Hibernate from the
`@Entity`, not by a migration: there is no Flyway or Liquibase anywhere in `platform-core`, and
`E5DBServiceInitializer.buildSessionFactory(..., true, true, "com.e5.platform.core.akst.model")`
(`src/main/java/com/e5/platform/core/wre/engine/E5WorkflowEngine.java:429-434`) is what brings the
table into existence. **There is no DDL artefact to review in any repo.**

Entity: `src/main/java/com/e5/platform/core/akst/model/Task.java:40-88`, extending
`e5.stateservice.model.E5State<Task>` which contributes `created_date_time` / `updated_date_time`.

## Columns

| Column (quoted exactly as declared) | Java type | Null | Notes |
|---|---|---|---|
| `"taskId"` | `Long` | no | `@Id @GeneratedValue(IDENTITY)`, `updatable=false` (`Task.java:48-51`) |
| `"taskVQ"` | `String` | no | virtual-queue name; the dequeue key (`Task.java:52-53`) |
| `"priority"` | `Long` | no | lower sorts first; **defaults to `Long.MAX_VALUE`** when the envelope omits it (`AkstService.java:344`) |
| `"deadline"` | `Long` | no | epoch millis as a bare `Long`; **defaults to `Long.MAX_VALUE`** (`AkstService.java:343`) |
| `"taskType"` | `String` | no | `InvokeFunctionBlock` \| `RequestIntervention` \| a UIA name |
| `"namespace"` | `String` | no | home namespace, from `taskInfo.namespace` |
| `"taskIdentifier"` | `String(36)` | yes | **the correlation id** — carries `taskInfo.taskId`; indexed (`Task.java:62-63`) |
| `"taskDeferTime"` | `Instant` | yes | `timestamp(6) without time zone`; `@ColumnDefault("'0001-01-01 00:00:00'")` (`Task.java:64-66`) |
| `"taskStatus"` | `TaskStatus` | no | `@Enumerated(STRING)`: `CREATED`, `PICKED`, `COMPLETED`, `CANCELLED` |
| `"taskPayload"` | `byte[]` `@Lob` | yes | serialised `FunctionBlockTaskData` |
| `"metaInfo"` | `byte[]` `@Lob` | yes | serialised `MetaInfo` — the create envelope's routing path, stored unflipped (`AkstService.java:164-165`) |
| `"taskResponse"` | `byte[]` `@Lob` | yes | the worker's `fbPayload` on completion |
| `picked_date_time` | `Instant` | yes | **unquoted**, snake_case — the only column that is; written on dequeue (`AkstService.java:285`) |
| `filters` | `Map<String,String>` jsonb | yes | **unquoted** in `@Column`, but the dequeue SQL references it as `t."filters"`; `@JdbcTypeCode(SqlTypes.JSON)` (`Task.java:86-88`) |
| `created_date_time` | `Instant` | — | from `E5State` |
| `updated_date_time` | `Instant` | — | from `E5State`; used as the completion timestamp (`AkstService.java:94`, `:136`) |

Note the inconsistent quoting: twelve columns are declared with embedded double quotes (so Postgres
stores them case-sensitively — `"taskVQ"`, not `taskvq`), while `picked_date_time` and `filters` are
not. Any hand-written SQL must quote the former and must not quote the latter.

## Indexes

Declared on the entity (`Task.java:40-46`) — five, all single-column:

| Index | Column |
|---|---|
| `idx_task_priority` | `"priority"` |
| `idx_task_deadline` | `"deadline"` |
| `idx_task_createdat` | `created_date_time` |
| `idx_task_taskvq` | `"taskVQ"` |
| `idx_task_taskIdentifier` | `"taskIdentifier"` |

**There is no composite index matching the dequeue predicate** (`"taskVQ"`, `"taskStatus"`,
`"taskDeferTime"`) and **no index on `"taskStatus"` or `"taskDeferTime"` at all**. There is also no
GIN index on `filters`, which the dequeue uses with the jsonb containment operator `@>`. The one
index on `created_date_time` serves a column the dequeue never reads.

## The dequeue query

**Not in this repo.** It is built by `TaskProcessor.buildQuery(Map filters, boolean canTakeOther,
boolean hasUpdater, boolean isH2)` inside `e5.state:StateService:1.2.8`
(pinned at `build.gradle:238`), reached from
`AkstService.getTask` / `AkstService.isTaskAvailable`
(`src/main/java/com/e5/platform/core/akst/service/AkstService.java:283-286`, `:333`).
`TaskProcessor` refuses to run for any entity whose simple name is not `Task`
(`IllegalArgumentException("You are not allowed to use this class")`).

The string literals, quoted verbatim from the class constant pool:

```
"SELECT * FROM"  <"schema">  "."  "e5_task"  " t WHERE"
"(t.\"filters\" @> CAST(? AS jsonb))"
" OR"
"(t.\"filters\" IS NULL OR t.\"filters\" = '{}'::jsonb)"
" AND"
"t.\"taskVQ\" = ? AND t.\"taskStatus\" = ? AND t.\"taskDeferTime\" >= ?"
"ORDER BY CASE\n    WHEN t.\"filters\" @> CAST(? AS jsonb) THEN 0\n    ELSE 1\nEND,\nt.\"deadline\",\nt.\"priority\"\n"
"ORDER BY t.\"deadline\", t.\"priority\""
"LIMIT 1"
" FOR UPDATE SKIP LOCKED"
" FOR UPDATE"
```

Assembled, for Postgres with a non-empty `filters` map and `canTakeOther = true`:

```sql
SELECT * FROM "<schema>".e5_task t
WHERE (t."filters" @> CAST(? AS jsonb))
   OR (t."filters" IS NULL OR t."filters" = '{}'::jsonb)
  AND t."taskVQ" = ? AND t."taskStatus" = ? AND t."taskDeferTime" >= ?
ORDER BY CASE
    WHEN t."filters" @> CAST(? AS jsonb) THEN 0
    ELSE 1
END,
t."deadline",
t."priority"
LIMIT 1 FOR UPDATE SKIP LOCKED
```

Parameters are bound positionally in this order: filters JSON (only when the map is non-empty and
the database is not H2), `taskVQ`, `taskStatus`, `Instant.now()`, and — only when `canTakeOther` is
true and a filters param was bound — the filters JSON a second time for the `ORDER BY` CASE.

### What the ORDER BY actually is

**`[filters-affinity CASE,] deadline, priority`** — and nothing else. Three branches:

| Condition | `ORDER BY` |
|---|---|
| filters non-empty **and** `canTakeOther` **and** not H2 | affinity `CASE` → `deadline` → `priority` |
| filters non-empty **and** `canTakeOther` **and** H2 | `CASE WHEN CAST(t."filters" AS VARCHAR) LIKE '%"k":"v"%' [AND …] THEN 0 ELSE 1 END, t."deadline", t."priority"` (values string-interpolated, single quotes doubled) |
| otherwise | `ORDER BY t."deadline", t."priority"` |

- **`created_date_time` / arrival order is NOT in the `ORDER BY` in any branch.** There is no FIFO
  tiebreaker: two tasks with equal `deadline` and equal `priority` are returned in whatever order
  Postgres chooses. See akst-dequeue-order-is-undocumented (archived).
- **`deadline` outranks `priority`.** A task with a nearer deadline wins regardless of priority.
- **The affinity `CASE` outranks both.** See
  worker-affinity-filters-are-undocumented (archived).
- `FOR UPDATE SKIP LOCKED` is appended **only when an `EntityUpdater` is supplied** — i.e. for
  `getTask`, not for `isTaskAvailable`. On H2 it degrades to plain `FOR UPDATE` (no `SKIP LOCKED`).
- `LIMIT 1` — one task per RPC, always.

### `taskDeferTime >= now()` is a *validity window*, not a delay

The predicate is `>=`, so a row is only visible while its `taskDeferTime` is still in the **future**.
`AkstService.createTask` therefore defaults it to `9999-12-31T23:59:59Z`
(`AkstService.java:38`, `:344`) so untouched tasks are always eligible. The same `>`-into-the-future
convention appears in `AkstInMemoryTaskStore.getCreatedAndPickedTasks`
(`AkstInMemoryTaskStore.java:54`, `:57`). The column's own `@ColumnDefault` is
`'0001-01-01 00:00:00'`, which is the opposite. See
task-defer-time-default-is-contradictory (archived).

## Writers

| Operation | Code | Effect |
|---|---|---|
| insert | `AkstService.handleTaskCreate` → `E5StateService.insertOne` (`AkstService.java:145`) | status `CREATED` |
| dequeue | `TaskProcessor.getTask` + updater (`AkstService.java:283-286`) | `CREATED` → `PICKED`, sets `picked_date_time` |
| mutate | `AkstService.handleTaskUpdate` → `fetchAndUpdate` (`AkstService.java:177-194`) | `priority` / `deadline` / `taskDeferTime`, **guarded to `taskStatus = CREATED`** |
| complete | `AkstService.handleTaskComplete` → `E5StateService.updateOne` (`AkstService.java:98`) | `COMPLETED`, writes `taskResponse` |
| cancel | `AkstService.handleTaskCancel` → `E5StateService.updateOne` (`AkstService.java:238`) | `CANCELLED`, **guarded to `CREATED` only** (`AkstService.java:230`) |

No code anywhere deletes, archives or reaps a row. There is no retention policy.

## Readers outside AKST

None directly. `e5-task-reader` and `e5-ss-dynamic-scaling-service` both have **zero** JDBC/JPA on
their classpaths; they reach this data only through the `peekTask` RPC. See
[akst-task-service-grpc-api](../communication/akst-task-service-grpc-api.md).

**Known divergences:**
akst-dequeue-order-is-undocumented (archived) ·
worker-affinity-filters-are-undocumented (archived) ·
affinity-or-precedence-breaks-queue-isolation (archived) ·
task-defer-time-default-is-contradictory (archived) ·
picked-is-a-terminal-trap (archived)

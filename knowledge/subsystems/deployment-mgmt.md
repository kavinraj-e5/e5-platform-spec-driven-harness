---
type: subsystem
name: Deployment Management Service (DMS)
repo: e5-deployment-mgmt-service
repo_path: e5-deployment-mgmt-service
branch: release/v1.9.9
commit: f555f121
language: java
build: gradle
deployment: mgmt-service-pl-subsystem
namespace_pattern: platform-subsystem-{env}
etp_roles: []
owns_schema: true
ports: [50051]
health: grpc grpc.health.v1.Health/Check
calls: []
called_by: [interface, workflow-manager, monitoring-service, event-processor, credential-monitor, dynamic-scaling, devops-pipeline]
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.6.10
deployment_lag: release/v1.9.9 available, v1.6.10 running
prose_reviewed_at_commit: 75bbdb3
---

# Deployment Management Service (DMS)

The platform's **single schema owner** — a plain-Java gRPC server (no Spring) that is the only
subsystem holding JPA entities and Flyway migrations; every other subsystem's "state" is a row in
DMS's database read back over gRPC.

**Responsibility (what it owns):** The `e5_platform_support` PostgreSQL schema — **44 physical
tables** (38 JPA entities + 3 join tables + 3 migration-only tables) covering deployment
topology, workflow runs, schedules, webhooks/events, interface tasks, workflow-manager and
workflow-ops state, credential assignments and artifact versions. Plus one Quartz job that
materialises `workflow_run` rows from `schedule` cron expressions.

**Not its responsibility:** It **orchestrates nothing and calls nobody**. `calls: []` is literal —
there is no outbound gRPC stub, no Kafka producer and no HTTP client anywhere in `src/main/java`.
It also does **not** own webhook *delivery* state: that lives in [webhook-service](webhook-service.md)'s
own `destinations`/`webhook_queue` schema. DMS's `webhooks` table is only the registry.

**Entry points:** `Main.java:17` — `main()` builds the server, runs Flyway, then starts Quartz.
Only **three** gRPC services are registered (`Main.java:39-45`):
`DeploymentManagementServiceImpl`, `ProtoReflectionService`, `HealthServiceImpl`.

**Inbound:** gRPC on `grpc.service.port` (default `50051`).
**93 RPCs declared** on `DeploymentManagementService`, **91 implemented**, **3 effectively
UNIMPLEMENTED** — see [deployment-mgmt-grpc-api](deployment-mgmt-grpc-api.md).
The same `.proto` also declares `MonitoringService` (**zero rpcs**, `deployment_management.proto:315-316`)
and `PlatformInterface` (1 rpc `emitEvent`, `:266-268`) — **neither is registered in `Main.java`**,
so both are dead declarations in this repo. Total rpc lines in the file: **94**.

**Outbound:** **none.** Flyway + Hibernate JDBC to PostgreSQL is the only egress
(`Main.java:70-86`, `HibernateUtil.java:41-49`).

**Data owned:** 44 tables. Full catalogue, PKs and FKs in
[deployment-mgmt-grpc-api](deployment-mgmt-grpc-api.md#table-catalogue).

| Domain | Tables |
|---|---|
| Deployment topology | `deployment`, `deployment_version`, `deployment_config`, `deployment_state`, `deployment_app_status`, `infra_component`, `subscription`, `customer`, `runtime_config` |
| Catalogue / SKU | `workflow_sku`, `workflow_variant`, `variant`, `workflow_impl`, `application`, `application_impl`, `application_version`, `application_worker`, `aggregator` |
| Runs & schedules | [`workflow_run`](../tables/workflow_run.md), [`schedule`](../tables/schedule.md), `temporal_workflow`, [`scheduler_task`](../tables/scheduler_task.md) |
| Webhooks & events | [`webhooks`](../tables/webhook.md), [`events`](../tables/event.md), `tbl_event_status`, [`webhook_subscription`](../tables/webhook_subscription.md), `webhook_events` |
| Lifecycle state | [`workflow_manager_state`](../tables/workflow_manager_state.md), [`workflow_ops_state`](../tables/workflow_ops_state.md), [`interface_task`](../tables/interface_task.md), `fb_worker_pod` |
| Credentials | [`credential_assignment_registry`](../tables/credential_assignment_registry.md) |
| Artifact versions | [`task_sdk`](../tables/task_sdk.md), `platform_core`, `fb_core`, `common_util`, `state_service`, `notification_hub`, `non_rpa_worker_core`, `dependency` |
| Join tables | `deployment_version_application_worker`, `webhook_events`, `workflow_impl_application_impl` |
| Migration-only (no entity, no repository, **unreachable**) | `workflow_state`, `state_property`, `non_rpa_worker_core` |

**`task_route` does NOT exist at this commit** — see
task-route-absent-in-pinned-dms-commit (archived).

**Config keys:** read exclusively through `EnvUtil.getEnv(key, default)`; **all 11 keys are
k8s env vars, no `application.yml` exists in the repo**.
`db.connection.endpoint.url` · `db.connection.endpoint.port` · `db.name` ·
`db.connection.username` · `db.connection.password` · `db.connection.schema.name` ·
`db.connection.show_sql` · `db.connection.format_sql` · `grpc.service.port` ·
`cron.scheduler.populateRunRecords` · `populateRunRecords.isEnabled`.
**Blank in every committed deployment-config** (`deployment-configs/{dev,qa,test,prod}-config.yaml:11-25`):
`db.connection.endpoint.url`, `db.name`, `db.connection.username`, `db.connection.password`.
`db.connection.schema.name` is `public` in **all four** environments.
⚠ `src/main/resources/hibernate.properties:13-15` commits a **live dev RDS host, superuser name
and plaintext password** into git. That file is read by nothing at runtime (`HibernateUtil.java:41-49`
builds every property from env) — it is pure credential leakage. Values are never recorded here.

**ETP role:** none. DMS is not a task-initiator, router, queue, worker or reader — it is the
state store those roles read and write. It holds the `interface_task` rows that the ETP
`PerformTask`/`GetTaskStatus`/`UpdateTaskResponse` triad is built on, but never moves a task.

**Failure modes:**
- **Flyway failure is fatal and un-retried:** `Main.java:84` rethrows, so the pod crash-loops.
  But `buildServer()` runs *before* `flyWayMigration()` (`Main.java:19-20`) — the server object is
  built, then migration throws, so the port never opens. Callers see connection-refused, not UNAVAILABLE.
- **Health check is a constant.** `HealthServiceImpl.java:10` initialises `isHealthy = true` and
  `setHealthStatus()` is never called from anywhere. The gRPC health probe reports SERVING even
  with the database down.
- **`GetConfigValue` is broken outside a `dev` schema.** `DeploymentStateRepositoryImpl.java:23-24`
  hardcodes `FROM dev.deployment_state` / `JOIN dev.deployment_version` while every
  deployment-config sets `db.connection.schema.name: public` →
  [dms-hardcoded-dev-schema-in-native-query](../known-issues/dms-hardcoded-dev-schema-in-native-query.md).
- **Per-call repository and session construction.** Every use case does
  `RepositoryFactory.create…Repository()` and `HibernateSessionUtil.execute` opens a fresh
  session per call; there is no request-scoped unit of work, so multi-repository use cases
  (e.g. `SaveWorkflowManagerRunStatusUseCase`, 3 repositories) are **not atomic**.
- **`getPodEndpoints`, `ChangeWorkerStatus`, `MigrateAppWorker` return UNIMPLEMENTED** —
  the first calls `super.getPodEndpoints(...)` (`DeploymentManagementServiceImpl.java:113`),
  the other two have no `@Override` at all. monitoring-service and credential-monitor both
  still carry `getPodEndpoints` call paths.
- **Optimistic-lock contention on `workflow_ops_state`** — `@Version` at
  `WorkflowOpsStateEntity.java:46-48` is the only optimistic lock in the schema; concurrent
  `UpdateOpsTaskState` loses with `OptimisticLockException`.

**Code-level landmarks:**
- `src/main/proto/deployment_management.proto:2335-2516` — the whole 93-rpc service block
- `grpc/service/DeploymentManagementServiceImpl.java:27-1158` — one thin method per rpc,
  each delegating to `new <X>UseCase()` through `GrpcRequestHandler.handleRequest`
- `utils/HibernateUtil.java:61-109` — entity registration is a **hardcoded array of 48 entries
  containing 38 distinct classes** (`StateServiceEntity` appears 3×); adding an entity without
  editing this array silently excludes it from the SessionFactory
- `data/repository/DeploymentDetailsRepositoryImpl.java` — the widest repository; joins 10 tables
- `scheduler/task/BaseTask.java:23-25,30` — the only writer of `workflow_run` outside an rpc
- `data/repository/DeploymentStateRepositoryImpl.java:23-24` — the `dev.`-prefixed native query

**Known divergences:**
task-route-absent-in-pinned-dms-commit (archived) ·
dms-proto-forks-disagree-on-service-identity (archived) ·
[dms-hardcoded-dev-schema-in-native-query](../known-issues/dms-hardcoded-dev-schema-in-native-query.md) ·
webhook-delivery-suspended-is-not-a-dms-column (archived) ·
dms-declares-two-unserved-proto-services (archived) ·
dms-core-schema-predates-flyway (archived) ·
dms-committed-database-credentials (archived)

**Relevant runbooks:** none exist yet. Two are needed and the steps are non-obvious:
*add a DMS table* (because of the hardcoded entity array at `HibernateUtil.java:61-109` and the
missing Flyway baseline — see
dms-core-schema-predates-flyway (archived)) and
*add an RPC* (because three already ship as UNIMPLEMENTED and every consumer keeps its own
proto fork — see
dms-proto-forks-disagree-on-service-identity (archived)).

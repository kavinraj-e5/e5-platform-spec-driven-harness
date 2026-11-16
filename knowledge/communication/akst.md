---
type: subsystem
name: AKST (Asynchronous Kafka-backed Scheduled Tasks)
repo: e5-platform-core
repo_path: platform-whole-context/platform-workflow-building/platform-core
branch: release/v1.10.1
commit: 377cd53d
language: java
build: gradle
deployment: akst-app-v1 (workflow plane) / task-manager-pl-subsystem (subsystem plane)
namespace_pattern: "{client}-{workflow}-{variant}-{env} | platform-subsystem-{env}"
etp_roles: [task-queue, task-router]
owns_schema: true
ports: [akstService.akstServicePort]
health: grpc TaskService/healthCheck
calls: [kafka, postgres]
called_by: [interface, platform-core, rf-fb-worker-core, task-reader]
provenance: code
verified: 2026-10-04
prose_reviewed_at_commit: a5f3c031
---

# AKST

The platform's **task queue** — a Postgres-backed priority queue with a gRPC pull interface — and,
optionally, the **Workflow Router**. It is not a separate repo or a separate image: it is a
`WORKER_MODE` of the same `platform-core`-based JAR that runs the WF Engine, WF Worker and
Aggregator.

**Responsibility (what it owns):** The `e5_task` table and the lifecycle of a delegated task:
accept `TaskCreate`/`TaskUpdate`/`TaskCancel` off a Kafka command queue, persist, hand exactly one
task at a time to a pulling worker, and publish the matching response event. When
`routingTable.routingEnabled` is true it also decides, per envelope, whether this deployment owns the
destination virtual queue or must forward it.

**Not its responsibility:** Executing anything, and **keeping a task alive**. There is no lease, no
expiry, no heartbeat consumer and no reaper — `Command.TaskHeartbeat` exists in the SDK enum and
nothing in this code produces or consumes it. Routing *policy* is config, not code: the only
routing source implemented is static YAML.

**Entry points:**
- `src/main/java/com/e5/platform/core/akst/service/AkstServiceStarter.java:7-9` —
  `E5WorkflowEngine.forAkstService().start(args)`
- the real branch is `WORKER_MODE=AKST_SERVICE` in
  `src/main/java/com/e5/platform/core/wre/engine/E5WorkflowEngine.java:426-450`, which builds the
  `AKST` session factory over `com.e5.platform.core.akst.model` and calls `startAkstService()`
  (`:542-579`) — two fixed thread pools, one for the Kafka command-queue listener and one for the
  gRPC server.

**Inbound:**
- **Kafka** — one topic, `akstService.akstCmdQEndpoint`, consumed by `TaskActionWorker`
  (a `KafkaMicroworker`) with consumer group `akst_cmdQ_group` unless overridden by an env var of
  that name (`service/TaskActionWorker.java:12`, `utility/E5Utility.java:137-140`).
  Handler `TaskActionHandler.executeAction` (`service/TaskActionHandler.java:58-80`). Commands:
  `TaskCreate`, `TaskUpdate`, `TaskCancel`, `TaskComplete` (`service/AkstService.java:57-69`);
  anything else is logged as "Unsupported command" and dropped.
- **gRPC** — `TaskService` with `getTask`, `isTaskAvailable`, `healthCheck`, `peekTask`. See
  [akst-task-service-grpc-api](akst-task-service-grpc-api.md).

**Outbound:**
- Kafka response events through the single egress point `RoutingAwarePublisher`
  (`routing/RoutingAwarePublisher.java:35-66`) — `TaskQueued`, `TaskCreationFailed`, `TaskUpdated`,
  `TaskUpdateFailed`, `TaskCancelled`, `TaskCancellationFailed`, `TaskComplete`, and `TaskPicked`
  from the gRPC pull path (`service/AkstService.java:293-295`).
- Kafka forwards to `routingTable.forwardOnMiss.config.topic` (`TaskActionHandler.java:103-106`).
- Kafka route failures as `Command.TaskRouteNotFound` to the envelope's **source** endpoint
  (`routing/RouteFailureNotifier.java:75-81`).
- Kafka `TaskMetrics` events to `akstService.metricsEndpoint` on completion, only when
  `userMetaInfo.deploymentVersionId` is present (`metrics/MetricEventEmitter.java:14-17`,
  `AkstService.java:116-126`).
- Postgres via `e5.state:StateService` 1.2.8.

**Data owned:** `e5_task` — the only table. See [e5-task](../tables/e5-task.md). No migrations exist;
Hibernate creates it. Nothing deletes or archives rows.

**Config keys:** `akstService.{akstCmdQEndpoint, akstServicePort, workflowEndpoint, metricsEndpoint,
inMemoryTaskStoreEnabled, routingTable}` plus `dbconfig.*` and `kafka.*`. `AkstConfig.schema.json`
**is** enforced at runtime (`service/TaskActionImpl.java:29-37`), unlike `WorkerConfig`. See
[akst-routing-table](akst-routing-table.md).

**ETP role:** Task Queue always; Task Router additionally when `routingTable.routingEnabled: true`.
It is never Task Initiator and never Task Worker.

## The state machine

Four statuses — `CREATED`, `PICKED`, `COMPLETED`, `CANCELLED`
(`model/TaskStatus.java:3-5`). Five transitions:

| From | To | Trigger | Guard |
|---|---|---|---|
| — | `CREATED` | `TaskCreate` → `handleTaskCreate` (`AkstService.java:141-167`) | destination `vqName` must be present, else `IllegalArgumentException` (`:339-341`) |
| `CREATED` | `PICKED` | gRPC `getTask` → `TaskProcessor.getTask` updater (`AkstService.java:283-286`) | dequeue query filters `taskStatus = CREATED` |
| `PICKED` | `COMPLETED` | `TaskComplete` → `handleTaskComplete` (`:72-132`) | `responseEventType` must be `COMPLETE_DELEGATION_ACTION` (`:73`); **no status guard at all** — a `CREATED`, `COMPLETED` or `CANCELLED` task is completed again without complaint |
| `CREATED` | `CANCELLED` | `TaskCancel` → `handleTaskCancel` (`:216-250`) | **guarded to `CREATED` only** (`:230`); otherwise `TaskCancellationFailed` |
| `CREATED` | `CREATED` (mutate `priority`/`deadline`/`taskDeferTime`) | `TaskUpdate` → `handleTaskUpdate` (`:169-214`) | **guarded to `CREATED` only**, inside the filter: `.eq(Task.TASK_STATUS, TaskStatus.CREATED)` (`:179`); otherwise `TaskUpdateFailed` |

So **two commands are `CREATED`-only (`TaskUpdate`, `TaskCancel`), one is unguarded
(`TaskComplete`), and there is no transition out of `PICKED` other than completion.**

### `PICKED` is terminal if the worker dies

A `PICKED` task is unreachable by both recovery routes: the dequeue query filters
`t."taskStatus" = ?` bound to `CREATED` (`AkstService.java:283`), and cancel requires `CREATED`
(`:230`). There is no lease, no `picked_date_time` timeout check anywhere (`pickedDateTime` is read
only to compute a metric, `:134-139`), and no scheduled job of any kind in the AKST package. The
worker side confirms the other half: `e5-rf-fb-worker-core` emits **no** pick acknowledgement and
**no** heartbeat in AKST mode (`FBCore.py:362`, `:138`). See
picked-is-a-terminal-trap (archived).

## Dequeue, and the two undocumented dimensions

The selection rule is **not in this repo** — `TaskProcessor.buildQuery` lives in
`e5.state:StateService:1.2.8`. The real `ORDER BY` is
**`[filters-affinity CASE,] t."deadline", t."priority"`** with `LIMIT 1` and
`FOR UPDATE SKIP LOCKED`; `created_date_time` appears in no branch, so there is **no arrival-order
tiebreaker**. The `filters` / `canTakeOther` worker-affinity mechanism is the primary sort key when
in play. Full reconstruction and parameter binding: [e5-task](../tables/e5-task.md).

## Routing

Three classes matter. `RoutingTableFactory` picks the implementation once at boot and fails the
process on an invalid config (`routing/RoutingTableFactory.java:28-61`). `NoOpRoutingTable` accepts
everything locally and replies on `workflowEndpoint` (`routing/NoOpRoutingTable.java:13-21`).
`ConfigRoutingTable` is the Workflow Router (`routing/ConfigRoutingTable.java:94-190`).
`RoutingDecision.Type` is `LOCAL_ACCEPT` / `FORWARD` / `ROUTE_FAILED`
(`routing/RoutingDecision.java:8-15`). Resolution order and config keys:
[akst-routing-table](akst-routing-table.md).

Both inbound commands and outbound responses are gated: `TaskActionHandler.routeInbound`
(`:88-111`) and `RoutingAwarePublisher.publishResponse` (`:35-50`). Every AKST response is flipped
(`source` ↔ `destination`) before egress so `destination` names the initiator's reply endpoint
(`routing/TaskRoutingEndpoints.java:72-87`); the persisted envelope keeps the unflipped create form
(`AkstService.java:164-165`).

**Failure modes:**
- A `PICKED` task whose worker dies is stuck forever — no lease, no reaper, not cancellable.
- `getTask` on an empty queue hands `null` to `responseObserver.onNext`
  ([gettask-passes-null-to-onnext](../known-issues/gettask-passes-null-to-onnext.md)), so every
  client must probe with `isTaskAvailable` first and accept the race (the probe takes no row lock).
- `peekTask` returns an empty list when `inMemoryTaskStoreEnabled` is false, and downstream reads
  empty as "idle" and scales workers to minimum
  (peek-task-is-empty-unless-a-feature-flag-is-on (archived)).
- Any `fbPayload` value that is not `String`/`Integer`/`Double`/`Boolean`/`Map`/`List`/null makes the
  gRPC projection throw `IllegalArgumentException` (`TaskServiceGrpcImpl.java:293-295`).
- A task whose `taskDeferTime` window has closed stays `CREATED` but is permanently undequeueable
  (task-defer-time-default-is-contradictory (archived)).
- `TaskActionHandler.executeAction` catches every exception from `processTask`, logs the whole
  envelope and returns `null` (`:72-75`) — the Kafka message is acked and the command is lost with
  no DLQ.
- A `TaskComplete` whose `responseEventType` is `HUMAN_INTERVENTION` is silently dropped (`:73-76`).
- The in-memory store is an unbounded static Caffeine cache with no `maximumSize` and no expiry
  (`service/AkstInMemoryTaskStore.java:27-28`).
- Observability: `queued_tasks` / `inflight_tasks` up-down counters are **process-local and
  reconstructed from zero on restart** (`metrics/VirtualQueueMetrics.java`), so they drift from the
  table permanently after any AKST restart.

**Code-level landmarks:**
- `service/AkstService.java:50-70` — the command dispatch; everything starts here
- `service/AkstService.java:278-306` — the dequeue and `TaskPicked` publish
- `service/TaskActionHandler.java:88-111` — the inbound routing gate
- `routing/ConfigRoutingTable.java:94-115` — destination resolution
- `routing/TaskRoutingEndpoints.java:72-87` — `flipRoutingEndpoints`, the reply-address rule
- `wre/engine/E5WorkflowEngine.java:542-579` — `startAkstService`, the boot order

**Metrics emitted:** `created_tasks`, `picked_tasks`, `completed_tasks` (counters),
`queued_tasks`, `inflight_tasks` (up-down counters), `task_waiting_time`, `task_processing_time`,
`task_turn_around_time`, `task_message_size` (histograms) — all under instrumentation scope
`io.opentelemetry.example.metrics` (`metrics/VirtualQueueMetrics.java:6`), labelled by vqName and
taskName; plus `akst_routing_decisions_total`, `akst_routing_forward_total`,
`akst_routing_route_failed_total`, `akst_routing_latency_ms`
(`metrics/RoutingMetrics.java:29-71`). Note `task_message_size` declares
`setUnit("milliseconds")` for a byte count.

**Known divergences:**
akst-lives-in-platform-core-not-akst-service (archived) ·
akst-dequeue-order-is-undocumented (archived) ·
worker-affinity-filters-are-undocumented (archived) ·
affinity-or-precedence-breaks-queue-isolation (archived) ·
task-defer-time-default-is-contradictory (archived) ·
picked-is-a-terminal-trap (archived) ·
routing-table-source-subsystem-does-not-exist (archived) ·
local-router-emits-the-failure-the-spec-forbids (archived) ·
task-route-failed-command-name (archived) ·
shipped-schema-cannot-validate-taskcomplete (archived) ·
peek-task-is-empty-unless-a-feature-flag-is-on (archived) ·
[gettask-passes-null-to-onnext](../known-issues/gettask-passes-null-to-onnext.md) ·
three-divergent-copies-of-task-proto (archived)

**Relevant runbooks:** none yet — a `PICKED`-task recovery runbook is not writable, because no
recovery path exists.

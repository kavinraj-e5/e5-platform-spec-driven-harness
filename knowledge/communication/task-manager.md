---
type: subsystem
name: Task Manager (e5-akst-service)
component: task-manager
system: subsystem/control
repo: e5-akst-service
repo_path: e5-akst-service
branch: release/v1.3.5
commit: fddcffc
language: java
build: gradle
deployment: task-manager-pl-subsystem
image: e5-platform-ss-task-manager
deployed_in_qa: v1.2.2
deployment_lag: release/v1.3.5 available, v1.2.2 running
namespace_pattern: platform-subsystem-{env}
etp_roles: [task-queue]
owns_schema: false
ports: [akstService.akstServicePort]
health: none
calls: [kafka, postgres, deployment-mgmt]
called_by: [interface, workflow-manager, workflow-ops, monitoring-service, credential-monitor, fb-worker]
provenance: code
verified: 2026-10-05
also_published: com.e5:e5-akst-service
---

# Task Manager — the standalone AKST deployable

**This is the deployed subsystem `task-manager`**, and the platform's Global Router. It is a
JFrog-published library (`com.e5:e5-akst-service`) that *also* builds its own image, with
`Main-Class: com.e5.akst.core.service.AkstServiceStarter`.

It is a **sibling fork** of the AKST code inside `platform-core`, not a layer over it and not
a dead branch. Neither depends on the other: `platform-core/build.gradle` declares no
`e5-akst-service` coordinate, and both repos emit protos into the same Java package
(`com.e5.platform.core.akst.service`), so a single classpath could not hold both. Two
independently evolving AKST codebases, deployed in two different roles:

| | this repo | `platform-core` |
|---|---|---|
| role | the **subsystem** task-manager; Global Router | the **in-namespace** `akst`, `WORKER_MODE=AKST_SERVICE`; local router |
| deployed as | `e5-platform-ss-task-manager` | the variant image, one per workflow |
| routing | DMS-backed (`task_route.proto`, `TaskRouteClient`) | config-driven (`RoutingTable`, `ConfigRoutingTable`) |
| `TaskStatus` | 3 values | 4, including `CANCELLED` |
| proto RPCs | 2 | 4, incl. `healthCheck`, `peekTask` |

## CORRECTED 2026-10-05 — this card said the opposite of the truth

It was titled **"e5-akst-service — stale fork"**, headlined *"an abandoned fork … not the AKST
deployable"*, and set
`deployment: "none — no Dockerfile target in any pipeline; not referenced by any
deployment-config in this workspace"`.

Every one of those claims is false:

| previous claim | evidence against it |
|---|---|
| not deployed | cluster runs `task-manager` on `e5-platform-ss-task-manager:v1.2.2-RELEASE-AKST-04-02-2026-001` |
| no deployment-config references it | its own `deployment-configs/{dev,qa,test,prod}-config.yaml:1-5` set `name: task-manager` |
| no Dockerfile target | `Dockerfile` builds a fat jar and sets an `ENTRYPOINT` |
| not a published library | `build.gradle` applies `java-library` + `maven-publish` and publishes to the JFrog repo `automation-fabric-libs` |
| "the live AKST is platform-core" | both are live, in different roles — see the table above |

The root cause was the registry, not the analysis: it listed a `task-manager` repo pointing at
**platform-core** while filing this repo as `akst-service-fork`, `role: library`. So the
knowledge base had the two AKST repos swapped, and every conclusion downstream inherited the
swap. `registry.yaml` now carries the deployment identity here, and
`tools/test_no_duplicate_repo_entries` fails if one git tree is ever listed under two ids
again.

What the old card got *right* and is preserved below: the feature-by-feature divergence between
the two implementations. That comparison was accurate; only its verdict was wrong.

## The divergence from platform-core's AKST

Kept because it is accurate and it is what you need before editing either implementation.

Note on checkouts: the workspace holds **two identical copies** of this repo at
`release/v1.2.0` / `f13d4b8` — `e5-akst-service/` and
`platform-whole-context/prioritized_queue_akst/e5-akst-service/` — plus a third in
`local-dev/tmp/` that is four commits behind. The KB reads none of them; it indexes the bare
mirror at `release/v1.3.5`.

**Responsibility (what it owns):** the subsystem task queue and the Global Router. The same
Kafka-in / gRPC-out task
queue over `e5_task`, with `TaskCreate` and `TaskComplete` only.

**Not its responsibility:** the Workflow Router. `grep -rn "routingTable\|forwardOnMiss\|routingEnabled" src/`
returns **zero hits** (exit 1). None of the routing feature set exists here.

**Entry points:** `src/main/java/com/e5/akst/core/service/AkstServiceStarter.java` (70 lines, its
own `main`) and `src/main/java/com/e5/akst/core/service/AkstGRPCServer.java:44` (a second `main`).
Neither is reachable from `platform-core`'s `WORKER_MODE` switch.

**Inbound:** Kafka command queue via `TaskActionWorker`/`TaskActionHandler`
(`src/main/java/com/e5/akst/core/service/TaskActionHandler.java`, 29 lines — it has no routing gate,
unlike `platform-core`'s 112-line version); gRPC `TaskService` with **two** RPCs only
(`src/main/proto/task.proto:35-38`): `getTask`, `isTaskAvailable`. No `healthCheck`, no `peekTask`.

**Outbound:** Kafka to `akstService.workflowEndpoint` directly from `getTask`
(`service/AkstService.java:107-108`: `KafkaOutboundEndpoint.getInstance().send(null, taskTransport,
…getWorkflowEndpoint())`) — a raw send with a null key, with no routing table and no single egress
point.

**Data owned:** the same `e5_task` table name, with a **different and incompatible** entity
(`src/main/java/com/e5/akst/core/model/Task.java:28-64`): no `taskIdentifier`, no `taskDeferTime`,
no `picked_date_time`, four indexes instead of five, and `filters` mapped with the
`com.vladmihalcea` `JsonType` rather than `@JdbcTypeCode(SqlTypes.JSON)`. Were it ever pointed at a
live schema it would not see the columns the real AKST writes.

**Config keys:** `akstService.{akstCmdQEndpoint, akstServicePort, workflowEndpoint}` (three, versus
six in `platform-core` — no `metricsEndpoint`, no `inMemoryTaskStoreEnabled`, no `routingTable`),
plus `dbconfig.*` and `kafka.*`. Schemas: `AkstConfig.schema.json`, `DatabaseConfig.schema.json`,
`KafkaConfig.schema.json`. `deployment-configs/{dev,qa,test,prod}-config.yaml` exist but no pipeline
in this workspace consumes them.

**ETP role:** Task Queue, historically. It can never have played Task Router.

## What is missing relative to the live AKST

| Capability | Here | `platform-core` @ a5f3c031 |
|---|---|---|
| `TaskStatus` | `CREATED, PICKED, COMPLETED` (3) | `CREATED, PICKED, COMPLETED, CANCELLED` (4) |
| Commands handled | `TaskCreate`, `TaskComplete` — an `if / else if / else` (`AkstService.java:31-86`) | + `TaskUpdate`, `TaskCancel`, via a `switch` |
| Correlation | the surrogate `Long taskId` (`AkstService.java:35`) | the `String "taskIdentifier"` column |
| Routing | **absent** | 8 classes, `RoutingDecision`, `forwardOnMiss`, metrics |
| `peekTask` / `AkstWorkload` | **absent from the proto** | present — the autoscaling input |
| `healthCheck` | **absent** | Postgres + Kafka component health |
| In-memory store | absent | `AkstInMemoryTaskStore` (Caffeine) |
| Metrics | **none** | `VirtualQueueMetrics` (9 instruments), `RoutingMetrics` (4), `MetricEventEmitter` |
| Interventions | absent | `RequestIntervention` normalisation |
| Defer / expiry | absent | `taskDeferTime` + dequeue predicate |
| `canTakeOther` | plain `bool` — no presence bit, so unset ≡ `false` | `optional bool`, server defaults unset to **`true`** |
| `e5-task-sdk-java` | `0.6.4` | `0.7.3` |
| `e5.state:StateService` | `1.2.6` | `1.2.8` |
| `response` flip on complete | hand-rolled `Map.of("destination", source, "source", des)` (`AkstService.java:62-67`) | `TaskRoutingEndpoints.flipRoutingEndpoints`, null-safe and non-mutating |
| README | one line (`# e5-akst-service`) | — |

It also carries its own copy of the microworker and exception/util scaffolding
(`service/microworker/**`, `exception/**`, `util/**`) that `platform-core` has since absorbed, and a
`src/test/java/com/e5/akst/core/akst/AkstClient.java` test client.

**Failure modes:** not applicable while undeployed. The one live hazard is a **classpath
collision**: its `task.proto` declares `option java_package = 'com.e5.platform.core.akst.service';`
(`src/main/proto/task.proto:4`) — the same generated package as `platform-core`'s — and its own
`AkstService` imports `com.e5.platform.core.akst.service.TaskOuterClass`
(`src/main/java/com/e5/akst/core/service/AkstService.java:13`). Putting both artifacts on one
classpath shadows `TaskOuterClass` silently rather than failing to compile.

**Code-level landmarks:**
- `src/main/java/com/e5/akst/core/model/TaskStatus.java:3-5` — the three-value enum, the quickest
  proof this is the fork
- `src/main/java/com/e5/akst/core/service/AkstService.java:31-86` — the two-command `if/else` ladder
- `src/main/proto/task.proto:35-38` — the two-RPC service
- `build.gradle:26`, `:32` — the stale SDK and StateService pins

**Known divergences:**
akst-lives-in-platform-core-not-akst-service (archived) ·
three-divergent-copies-of-task-proto (archived)

**Relevant runbooks:** none written yet.

**Before you edit:** decide which AKST you mean. Work on the *subsystem* task queue belongs
here; work on the *in-namespace* queue a workflow's own roles poll belongs in
[akst](akst.md) (`platform-core`, `WORKER_MODE=AKST_SERVICE`). The two have diverged — see the
table at the top — so a fix applied to one does not reach the other, and that duplication is
itself worth raising rather than silently maintaining twice.

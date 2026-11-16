---
type: api
name: AKST TaskService (gRPC)
protocol: grpc
proto: platform-core src/main/proto/task.proto
java_package: com.e5.platform.core.akst.service
service: TaskService
server: akst-app-v1 (WORKER_MODE=AKST_SERVICE), port from akstService.akstServicePort
impl: platform-core src/main/java/com/e5/platform/core/akst/service/TaskServiceGrpcImpl.java
clients: [rf-fb-worker-core, task-reader, platform-core WF engine akst client]
provenance: code
verified: 2026-10-02
---

# AKST `TaskService` — the worker-pull surface

The **only** synchronous way to get a task out of AKST. Commands go *in* over Kafka
(`akstService.akstCmdQEndpoint`); tasks come *out* over this gRPC service. There is no REST
surface and no HTTP health endpoint — health is itself an RPC.

Server: `AkstGRPCServer.start()` — `ServerBuilder.forPort(port).addService(new TaskServiceGrpcImpl())`
(`src/main/java/com/e5/platform/core/akst/service/AkstGRPCServer.java:13-18`), port from
`akstService.akstServicePort` parsed as an int from a **string** config field. Plaintext only; no
TLS, no interceptors, no auth. Shutdown hook awaits 30 s (`AkstGRPCServer.java:36`).

## Service definition

`src/main/proto/task.proto:76-81`:

```proto
service TaskService {
  rpc getTask(TaskAvailableRequest) returns (Task);
  rpc isTaskAvailable(TaskAvailableRequest) returns (TaskAvailable);
  rpc healthCheck(HealthCheckRequest) returns (HealthCheckResponse);
  rpc peekTask(Empty) returns (PeekTaskResponse);
}
```

All four are unary. There is **no streaming, no long-poll, and no server-push**: clients poll.

## `getTask` — dequeue one task

**Request** `TaskAvailableRequest` (`task.proto:11-15`):

| Field | Type | Meaning |
|---|---|---|
| `vqName` | `string` | the virtual queue to pull from. Mandatory — `TaskProcessor` throws `IllegalArgumentException("taskVQ required")` on blank. |
| `filters` | `map<string,string>` | worker capability map, matched against `e5_task.filters` with jsonb containment |
| `canTakeOther` | `optional bool` | may this worker also take tasks that carry no filters? |

**`canTakeOther` defaults to `true` when the field is absent**, set explicitly by the server:
`request = request.hasCanTakeOther() ? request : request.toBuilder().setCanTakeOther(true).build()`
(`TaskServiceGrpcImpl.java:208-210`, and identically at `:309-311`). Because the field is
`optional` in proto3, "unset" and "false" are distinguishable — a client that wants strict affinity
must send `canTakeOther = false` explicitly.

**Response** `Task` (`task.proto:6-9`): `{ string version; TaskInfo taskInfo; }` where `version` is
set to the literal string `"v2"` (`TaskServiceGrpcImpl.java:259`) and `TaskInfo`
(`task.proto:23-30`) is `{ taskType, taskId, responseEventType, TaskPayload taskPayload,
map<string,string> filters }`. `TaskPayload` (`task.proto:17-21`) is
`{ fbName, userDefinedIdentifier, google.protobuf.Struct fbPayload }`.

Note what the gRPC `TaskInfo` does **not** carry that the Kafka envelope does: `priority`,
`deadline`, `namespace`, `taskDeferTime`, `metaInfo`/`routingPath`. A worker therefore cannot see
the scheduling attributes of the task it was handed, and cannot route its own reply from the task —
it must use its configured outbound topic.

**Side effects of `getTask`** (not just a read):
1. `CREATED → PICKED` and `picked_date_time = now`, inside the dequeue transaction
   (`AkstService.java:283-286`).
2. A `TaskPicked` `TaskTransport` is published to Kafka through `RoutingAwarePublisher`
   (`AkstService.java:293-295`) — so the pick is announced on the bus, not on this RPC.
3. The in-memory store is updated when enabled (`AkstService.java:297-299`).
4. Four virtual-queue metrics are recorded (`TaskServiceGrpcImpl.java:221-227`).

`fbPayload` is converted `Map → Struct` by `convertMapToStruct` (`TaskServiceGrpcImpl.java:266-305`),
which handles `String`, `Integer`, `Double`, `Boolean`, `Map`, `List`, null — and **throws
`IllegalArgumentException("Unsupported object type: …")` for anything else**, e.g. a `Long` or a
`BigDecimal` in the payload.

### `getTask` on an empty queue returns `null` to `onNext`

`prepareTaskToSend(null)` returns `null` (`TaskServiceGrpcImpl.java:231-233`) and that `null` is
handed straight to `responseObserver.onNext(...)` (`:219`). gRPC does not permit a null message.
See [gettask-passes-null-to-onnext](../known-issues/gettask-passes-null-to-onnext.md). This is
why every known client calls `isTaskAvailable` first.

## `isTaskAvailable` — non-destructive probe

Same `TaskAvailableRequest`, same `canTakeOther` defaulting. Response `TaskAvailable`
(`task.proto:46-48`): `{ bool available; }`.

Implemented as **the same dequeue query with a null updater** (`AkstService.java:333`), which has
two consequences worth knowing:
- no `FOR UPDATE SKIP LOCKED` clause is emitted, so the probe does not reserve anything and is
  inherently racy against concurrent `getTask` calls;
- it is a full `SELECT * … LIMIT 1` including the `@Lob` payload columns, not a `COUNT`.

No status transition, no Kafka publish, no metrics.

## `peekTask` — the whole pending workload, or nothing

Request `Empty` (`task.proto:73-74`). Response `PeekTaskResponse`
(`task.proto:32-34`) = `repeated AkstWorkload akstWorkload`, where `AkstWorkload`
(`task.proto:36-44`) is `{ taskId, int64 priority, int64 deadline, fbName, createdAt, taskStatus,
taskVq }`.

**It returns an empty list unless `akstService.inMemoryTaskStoreEnabled` is true**
(`TaskServiceGrpcImpl.java:167-172`) — it reads the Caffeine cache, never the database. The cache is
populated at boot (`AkstInMemoryTaskStore.onLoad`, called from `E5WorkflowEngine.java:548-550`) and
maintained on create / pick / update / complete / cancel. It is **unbounded** — `Caffeine.newBuilder().build()`
with no `maximumSize` and no expiry (`AkstInMemoryTaskStore.java:27-28`) — and `cache` is `static`
while the class is instantiated fresh at each call site.

`AkstWorkload.taskDeferTime` exists on the Java model (`model/AkstWorkload.java:22`) but is **not
copied into the proto message** (`prepareDssTaskInfoList`, `TaskServiceGrpcImpl.java:192-203`), so
consumers cannot see the defer window. `createdAt` is defensively defaulted to `""`.

This RPC is the entire input to the autoscaling pipeline, which makes the feature flag
load-bearing: see
peek-task-is-empty-unless-a-feature-flag-is-on (archived).

## `healthCheck`

Request `HealthCheckRequest` (empty). Response `HealthCheckResponse` → `PlatformService
{ name, HealthStatus status, map<string,ComponentStatus> components }`, `HealthStatus` ∈
`{HEALTHY, UNHEALTHY}`, `ComponentStatus { status, requestID }`.

`name` is the constant `"AKST_SERVICE"` (`TaskServiceGrpcImpl.java:34`). It checks Postgres with
`SELECT 1` (`:143`) under the component key `"postgres"`, merges Kafka component health from
`PlatformHealth.getHealthMetrics()`, and **reports UNHEALTHY when no metric components are found at
all** (`:78-88`). Every exception path still answers `onNext` + `onCompleted` with UNHEALTHY rather
than a gRPC error status (`:91-101`). No client in this scope calls it: `e5-task-reader` generates
the stub but never invokes it.

## Clients

| Client | RPCs used | Evidence |
|---|---|---|
| `e5-rf-fb-worker-core` (FB workers) | `isTaskAvailable`, then a load/fetch; plus `getFbStateTask` on the platform VQ | `api/keywords.py:659`, `:673`, `:513-515`, `:532-534` |
| `e5-task-reader` | **`peekTask` only** | `service/TaskReaderService.java:152` |
| `platform-core` WF engine | AKST client polling runtime | `E5WorkflowEngine.java:517-524`, `config/AkstClientConfig.java` |
| `e5-ss-dynamic-scaling-service` | **none** — it never calls AKST | verified: no `getTask`/`isTaskAvailable`/`peekTask` reference in the repo |

`getFbStateTask` — the RPC `e5-rf-fb-worker-core` uses for the platform virtual queue
(`api/keywords.py:532`) — **is not declared in any `task.proto` in this workspace**. It must exist
in a newer SDK/AKST pair than anything checked out here.

**Three divergent copies of `task.proto` exist**, all declaring
`option java_package = 'com.e5.platform.core.akst.service'`:
see three-divergent-copies-of-task-proto (archived).

## Client obligations

- Call `isTaskAvailable` before `getTask`.
- Treat `getTask` as a state transition: once it returns, the task is `PICKED` and **nothing will
  ever give it back** — there is no lease, no expiry and no reaper anywhere in AKST. See
  picked-is-a-terminal-trap (archived).
- Reply with `TaskComplete` on Kafka, keyed on `taskInfo.taskId`; the gRPC response gives you no
  reply endpoint.
- Set your own deadlines. Neither `e5-task-reader` nor `e5-rf-fb-worker-core` sets
  `withDeadlineAfter`, so a hung AKST stalls their single poll thread indefinitely.

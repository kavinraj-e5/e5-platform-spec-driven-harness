---
type: contract
name: Transport Task Protocol v2 (ETP v2)
ground_truth: e5-task-sdk-java 0.7.3 — jar schemas and compiled enums
jar: ~/.gradle/caches/modules-2/files-2.1/e5.task.sdk.java/e5-task-sdk-java/0.7.3/e6267e09b671ee0688b6ef0159f80af3de9c2262/e5-task-sdk-java-0.7.3.jar
pinned_by: e5-platform-core build.gradle:237 — api 'e5.task.sdk.java:e5-task-sdk-java:0.7.3'
transport: Kafka (JSON), plus a gRPC projection for the AKST worker pull
provenance: sdk-schema
verified: 2026-10-02
---

# Transport Task Protocol v2 — the envelope

The single message shape that crosses every Kafka hop in the platform: initiator → AKST command
queue → AKST → reply topic. **Ground truth is the SDK jar**, not any document: the schema
resources (`TaskTransport.schema.json`, `TaskInfo.schema.json`, …) and the compiled enums ship
inside `e5-task-sdk-java`, and `platform-core` pins **0.7.3**. Where the jar's schema and the jar's
own Java classes disagree, the classes are what is on the wire — the schemas are not applied at
runtime on the AKST path at all.

Per-repo SDK pins differ and that matters: `platform-core` 0.7.3, `e5-akst-service` **0.6.4**
(`e5-akst-service/build.gradle:26`), `e5-ss-dynamic-scaling-service` **0.6.4**
(`build.gradle:176`), `e5-task-reader` **0.6.2** (`build.gradle:48`),
`e5-rf-fb-worker-core` uses the Python SDK **`e5_task_sdk_python` 1.0.9** (`pyproject.toml:26`).

## `TaskTransport` — the top level

`com/e5/platform/sdk/tasks/models/TaskTransport.class`, four fields:

| Field | Type | Required by schema | Notes |
|---|---|---|---|
| `version` | `TaskProtocol` | yes | **builder default is `v2`** (`$default$version()` returns `TaskProtocol.v2`). Schema enum is `["v2"]` only, so a v1 envelope cannot validate against this schema — v1 has its own `TaskTransportV1.schema.json`. |
| `command` | `Command` | yes | see below |
| `taskInfo` | `TaskInfo` | yes | see below |
| `metaInfo` | `MetaInfo` | yes | **but real `TaskComplete` messages omit it** — see shipped-schema-cannot-validate-taskcomplete (archived) |

## `Command` — all 13 values

From the compiled enum, in declaration order:

`TaskCreate`, `TaskUpdate`, `TaskCancel`, `TaskPicked`, `TaskHeartbeat`, `TaskComplete`,
`TaskQueued`, `TaskCreationFailed`, `TaskRouteNotFound`, `TaskUpdateFailed`,
`TaskCancellationFailed`, `TaskUpdated`, `TaskCancelled`.

`TaskTransport.schema.json`'s `command` enum lists only **11** — it is missing
**`TaskCreationFailed`** and **`TaskRouteNotFound`**, both of which AKST emits
(`AkstService.java:162`, `RouteFailureNotifier.java:30`). See
task-route-failed-command-name (archived).

Which direction each command travels, and who produces it:

| Command | Direction | Producer |
|---|---|---|
| `TaskCreate` | initiator → AKST cmd queue | interface, WF engine (`RPADelegator`), `AkstInterventionDelegator` |
| `TaskUpdate` | initiator → AKST | payload is `UpdateTaskPayload` |
| `TaskCancel` | initiator → AKST | payload is `CancelTaskPayload` |
| `TaskQueued` | AKST → reply | `AkstService.java:159` on successful insert |
| `TaskCreationFailed` | AKST → reply | `AkstService.java:162` |
| `TaskUpdated` / `TaskUpdateFailed` | AKST → reply | `AkstService.java:207` / `:210` |
| `TaskCancelled` / `TaskCancellationFailed` | AKST → reply | `AkstService.java:239` / `:231`, `:246` |
| `TaskPicked` | AKST → reply | `AkstService.java:294`, emitted from the **gRPC pull path**, not the command queue |
| `TaskComplete` | worker → AKST, then AKST → reply | worker (`e5-rf-fb-worker-core`), then `AkstService.java:108` |
| `TaskRouteNotFound` | AKST → initiator's **source** endpoint | `RouteFailureNotifier.java:77` |
| `TaskHeartbeat` | worker → gateway topic | **only `e5-rf-fb-worker-core` in Kafka mode** (`FBCore.py:194`, gated by `FBCore.py:138`). **Nothing in `platform-core` produces or consumes it** — grep finds it only inside `src/main/schemas/TaskResources-schema.schema.json:20`. Under AKST mode the worker sends no heartbeat at all. |

## `TaskInfo`

`com/e5/platform/sdk/tasks/models/TaskInfo.class` — nine fields:

| Field | Type | In `TaskInfo.schema.json`? | Notes |
|---|---|---|---|
| `taskId` | `String` | yes (`["string","null"]`) | **the correlation id for v2.** Persisted to `e5_task."taskIdentifier"`. |
| `taskType` | `String` | yes, **required** | discriminator: `InvokeFunctionBlock`, `UpdateTask`, `CancelTask`, `RequestIntervention`, or a User-Initiated-Action name |
| `priority` | `Long` | yes (`["number","null"]`) | lower sorts first; AKST substitutes `Long.MAX_VALUE` when null |
| `deadline` | `Long` | yes (`["number","null"]`) | epoch millis; AKST substitutes `Long.MAX_VALUE` when null |
| `namespace` | `String` | yes, **required** | home namespace; the fallback routing key when `metaInfo` is absent |
| `taskDeferTime` | `Long` | **NO** | epoch millis; read at `AkstService.java:346` and persisted |
| `responseEventType` | `ResponseEventType` | yes | gate on `TaskComplete` (`AkstService.java:73`) |
| `taskPayload` | `Object` | yes, **required** | shape chosen by `taskType` (see below) |
| `filters` | `Map<String,String>` | **NO** | the worker-affinity map; read at `AkstService.java:357` and persisted to `e5_task.filters` |

**`filters` and `taskDeferTime` are absent from the shipped `TaskInfo.schema.json`** even though both
are load-bearing. See
taskinfo-schema-omits-filters-and-defer-time (archived).

### `taskPayload` by `taskType`

| `taskType` | Payload class | Fields |
|---|---|---|
| `InvokeFunctionBlock` | `FunctionBlockTaskData` | `fbName` (req), `userDefinedIdentifier`, `fbPayload: Map<String,Object>` (req) |
| `UpdateTask` | `UpdateTaskPayload` | `taskIdentifier` (req), `taskDeferTime`, `priority`, `deadline` |
| `CancelTask` | `CancelTaskPayload` | `taskIdentifier` (req) |
| `RequestIntervention` | `InterventionBlockTaskData` | `interventionName`, `userDefinedIdentifier`, `interventionContext`, `interventionResponse`, `targetMetaData: InterventionDestination{name: InterventionTarget, config}` |

AKST normalises `RequestIntervention` into `FunctionBlockTaskData` before persisting:
`interventionName → fbName`, `interventionContext → fbPayload`
(`AkstService.java:370-393`). An unknown `taskType` is logged and treated as
`FunctionBlockTaskData` (`AkstService.java:375-378`).

Note the mutation-payload keys are `taskIdentifier`, while the create/complete key is
`taskInfo.taskId`. Same value, two names.

## `MetaInfo`

| Field | Type | Notes |
|---|---|---|
| `userMetaInfo` | `Map<String,Object>` | free-form (`additionalProperties: true`); the schema names only `taskIdentifier` |
| `routingPath` | `RoutingPath` | nullable |

`UserMetaInfo` (the typed v2 shape) is `taskIdentifier`, `workflowId`, `workflowName`,
`transactionRunId`, `deploymentVersionId`. **`temporalToken` exists only on `UserMetaInfoV1`** — see
v2-envelope-uses-v1-correlation-field (archived).
AKST reads `userMetaInfo.deploymentVersionId` for metric emission (`AkstService.java:118`).

`RoutingPath` is `routingEndpoints: Map<String, Endpoint>` keyed by the literal strings
**`"source"`** and **`"destination"`** (`TaskRoutingEndpoints.java:18-19`); `e5-rf-fb-worker-core`
also reads a third key **`"gateway"`** on the Kafka path (`FBCore.py:171-173`).

`Endpoint`: `endpointID: String`, `type: EndpointType`, `config: Map<String,Object>`. Known
`config` keys: `vqName` (akst endpoints), `topic` (kafka endpoints), `namespace`
(`TaskRoutingEndpoints.java:20-23`).

**Flip semantics.** Every AKST response flips `source` and `destination` before egress
(`TaskRoutingEndpoints.flipRoutingEndpoints`, `TaskRoutingEndpoints.java:72-87`) so that
`destination` always names the initiator's reply endpoint. The flip returns a **new** `MetaInfo`;
the persisted envelope keeps the unflipped create form (`AkstService.java:164-165`). If a key is
absent it is removed rather than set to null (`putOrRemove`, `:89-95`).

## `EndpointType` — 4 values

`kafka`, `akst`, `http`, `uipath_queue`. The `WorkerConfig` schema restricts
`endpoints[].type` to the same four (`WorkerConfig.schema.json:180`), and
`TaskTransport.schema.json`'s `RoutingEndpointsSchema` repeats them. Only `kafka` and `akst` are
branched on in AKST code (`ConfigRoutingTable.java:108`).

## `ResponseEventType` — 3 values

`COMPLETE_DELEGATION_ACTION`, `HUMAN_INTERVENTION`, `UNKNOWN`
(`com/e5/platform/sdk/tasks/models/ResponseEventType.class`). AKST accepts **only**
`COMPLETE_DELEGATION_ACTION` on `TaskComplete` and drops anything else with a log line
(`AkstService.java:73-76`) — including `HUMAN_INTERVENTION`.

## `WorkerType` — 3 values

`Robotframework`, `UiPath`, `Human`. It is **not** a field of the v2 envelope: it lives on
`FunctionBlockTaskDataV1` (the v1 payload) and on `WorkerConfig.workerMappings[].workerType`
(`WorkerConfig.schema.json:61-64`). `e5-rf-fb-worker-core` writes the literal `"Robotframework"`
only on its **Kafka** completion path (`utils/message_utils.py:156`).

## `TaskStatus` — 4 values, and it is not on the wire

`CREATED`, `PICKED`, `COMPLETED`, `CANCELLED` —
`src/main/java/com/e5/platform/core/akst/model/TaskStatus.java:3-5`. This enum is **AKST-internal**:
it is not a field of `TaskTransport` or `TaskInfo`, it is not in any SDK schema, and nothing outside
`com.e5.platform.core.akst` references it. It escapes only as a plain string in
`AkstWorkload.taskStatus` on the `peekTask` RPC. The stale fork declares only **three** values — see
akst-lives-in-platform-core-not-akst-service (archived).

## `TaskProtocol` — 2 values

`v1`, `v2`. Two different defaults ship in the same jar:
`TaskTransport.version` defaults to **`v2`**, `TaskEndpoint.version` defaults to **`v1`**.
`WorkerConfig.taskProtocol` effectively defaults to v1 — see
[worker-config](../workflow-development/workflow-library/worker-config.md) and
task-protocol-silently-defaults-to-v1 (archived).

## The correlation-id field — answer

**`taskInfo.taskId` for v2**, mirrored into `metaInfo.userMetaInfo.taskIdentifier`, persisted as
`e5_task."taskIdentifier"`, and the key that `UpdateTaskPayload.taskIdentifier` /
`CancelTaskPayload.taskIdentifier` must match. Every AKST lookup goes through
`findTaskByTaskIdentifier` (`AkstService.java:257-271`) or an `eq(Task.TASK_IDENTIFIER, …)` filter —
**never** the surrogate `"taskId"` primary key. For v1 the correlation field is
`metaInfo.userMetaInfo.temporalToken` instead.

## Also in the jar, for completeness

`PayloadError` (`errorCode`, `errorMessage`, `fieldName`, `fieldValue`, `timeStamp`),
`TaskEndpoint` (`version`, `namespace`, `taskProducer`, `taskRouter`),
`TaskRouter` (`inboundEndpoint`, `supportedTasks`, `endpoints`),
`TaskName` enum (`RPA_GATEWAY`, `PLATFORM_CORE`),
`InterventionTarget` enum (`AutoHub`, `Neos`, `Slack`),
and the v1 mirror set `TaskTransportV1` / `TaskInfoV1` (`taskType`, `taskPayload` only) /
`MetaInfoV1` / `UserMetaInfoV1` / `FunctionBlockTaskDataV1`.

**Known divergences:**
task-route-failed-command-name (archived) ·
shipped-schema-cannot-validate-taskcomplete (archived) ·
v2-envelope-uses-v1-correlation-field (archived) ·
taskinfo-schema-omits-filters-and-defer-time (archived) ·
task-protocol-silently-defaults-to-v1 (archived)

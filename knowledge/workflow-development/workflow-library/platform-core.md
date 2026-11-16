---
type: subsystem
name: platform-core (the workflow SDK)
repo: e5-platform-core
repo_path: platform-whole-context/platform-workflow-building/platform-core
branch: release/v1.10.1
commit: 377cd53d
language: java
build: gradle
deployment: "not a deployable — linked into the variant image; runs as wf-engine-app-v1 / wf-worker-app-v1 / akst-app-v1 / wf-aggregator-{name}-app-v1"
namespace_pattern: "{client}-{workflow}-{variant}-{env}"
etp_roles: [task-initiator, task-queue, task-router]
owns_schema: true
ports: [akstService.akstServicePort]
health: "com.e5.common.utils.health.HealthService (all modes except AKST_SERVICE and WF_TRIGGER)"
calls: [temporal, kafka, postgres, dynamodb, s3, notification-hub]
called_by: [wf-scaffolding, variant-scaffolding, sample-workflow]
provenance: code
verified: 2026-10-04
prose_reviewed_at_commit: a5f3c031
---

# platform-core

The workflow SDK that workflow authors extend **and** the home of the live AKST implementation. A
library (`group 'platform.core'`, `version '1.9.2'`, `build.gradle:25-26`), not a deployable — but
the JAR it produces is the whole variant image, and one env var decides which of five roles that
image plays.

**Responsibility (what it owns):** the authoring surface (block taxonomy, `BaseWorkflow`,
`WorkflowContext`, signals, state service, the event/aggregator pipeline), the `WORKER_MODE`
entrypoint switch, the Temporal integration, the config loader and its JSON schemas, **and** the
AKST service (`com/e5/platform/core/akst/**`). It owns three tables: `e5_task`, `e5_token`,
`e5_converter`.

**Not its responsibility:** the wire format. That is `e5-task-sdk-java`
([transport-task-protocol-v2](../../contracts/transport-task-protocol-v2.md)). It also does not own the
dequeue SQL — that lives in `e5.state:StateService` (source: `e5-state-service`, found 2026-11-14; consumers pin the published jar).

**Entry points:** `com/e5/platform/core/wre/engine/E5WorkflowEngine.java` — static factories
`forVariant` (`:74`), `forWorkflowSandbox` (`:80`), `forWorkflowComponentSandbox` (`:93`),
`forRPAGateway` (`:306`), `forAkstService` (`:313`). Also
`com/e5/platform/core/akst/service/AkstServiceStarter.java:7-9` and
`com/e5/platform/core/rpagateway/RPAGateway.java:5-9`.

## `WORKER_MODE` — the one JAR, five roles

**The branch is `E5WorkflowEngine.start(String[])`, `wre/engine/E5WorkflowEngine.java:324-459`.**
It reads the mode from the **`.env` file via dotenv, not from `System.getenv`**:

```java
Dotenv dotenv = Dotenv.configure().ignoreIfMissing().ignoreIfMalformed().load();
String command = dotenv.get("WORKER_MODE");
if (command == null) throw new E5Exception(E5ErrorMessages.WORKER_MODE_MISSING);
```
(`:329-331`), then `switch (command.trim())` at `:346`:

| `WORKER_MODE` | Line | What starts | Session factories built |
|---|---|---|---|
| `WF_ENGINE` | `:347-374` | `TaskEngine.initializeInboundHandlers` on `engine.taskProtocol`, then `startAkstClientPoller()` and `startInboundActionHandlersContainer()`; `System.exit(0)` when the executors drain | `CORE_INTERNAL` (`com.e5.platform.core.converter`) |
| `WF_WORKER` | `:375-399` | `startTemporalWorkerContainer(variantInfo.workflowClass)` — the Temporal worker; `EventEmitter.init(KAFKA_APPENDER, worker.eventStreamOutboundEndpoint)` | `STATE` (package `e5`) + `CORE_INTERNAL` |
| `WF_AGGREGATOR` | `:400-408` | `startAggregatorContainer(dotenv.get("AGGREGATOR_NAME").trim())` | none |
| `WF_TRIGGER` | `:409-415` | one-shot `WorkflowTrigger.triggerWorkflow()` | none |
| `RPA_GATEWAY` | `:416-425` | throws `AKST_PROTOCOL_v2_PROVIDED` if `rpaGateway.taskProtocol == v2`, else the v1 gateway inbound handlers | none |
| `AKST_SERVICE` | `:426-450` | `startAkstService()` — Kafka command-queue listener + gRPC server | `AKST` (`com.e5.platform.core.akst.model`) |
| anything else | `:451-452` | `E5Exception(WORKER_MODE_INVALID)` | — |

Two side-effects of the mode are easy to miss: health is **not** started for `AKST_SERVICE` or
`WF_TRIGGER` (`:339-344` — AKST gets a bare `new PlatformHealth()` instead of
`HealthService().initiateHealthService()`), and the whole `start` body is wrapped in
`catch (Exception) { …; System.exit(1); }` (`:455-458`), so any startup failure is an exit code, not
a stack trace to a supervisor.

## Block taxonomy

Nine block kinds exist in the tree; **six are authoring surfaces**, three are not.

| Block | Base class / factory | Author implements | Registering annotation | Naming rule |
|---|---|---|---|---|
| Start | abstract hook on `BaseWorkflow` (`workflow/BaseWorkflow.java:334`); driver `decisionblock/startblock/StartBlock.java:24` | `IFunction<I,Enum> StartBlockFunction(I)` | none — mandatory abstract method | — |
| End | abstract hook `workflow/BaseWorkflow.java:375`; driver `endblock/EndBlock.java:17` | `IEndBlockFunction.end(UserContext)` (`common/IEndBlockFunction.java:9-11`) | none | — |
| Decision | `decisionblock/BaseDecisionBlock.java:37` | `IFunction<Object,Enum> decision()` (`:72`) | `@E5DecisionBlock` | class name ends `DB`, package-private |
| Transformation | `executionblock/transformationblock/BaseTransformationBlock.java:48` | `transformationFunction()` (`:186`) | `@E5TransformationBlock` | ends `TB` |
| Function | `executionblock/functionblock/BaseFunctionBlock.java:65` (932 lines) | **only** `List<Class<? extends IDelegator>> registerDelegatorFunction()` (`:916`) | `@E5FunctionBlock(delegators, enablePartialSuccess, canBreachIdempotency, pre/postValidationHook)` | ends `FB`, package-private |
| Intervention | `executionblock/interventionblock/BaseIntervention.java:74` | **nothing** — no abstract methods; delegator resolved from config (`:328`) | `@E5InterventionBlock(pre/postValidationHook)` | ends `IB`, package-private |
| Container | `childworkflow/containerblock/BaseContainerBlock.java:33` | `Container<I,R> containerGroup()` (`:103`) | `@E5Container` | — |
| Wait | `executionblock/waitblock/WaitBlock.java:38` — **`final`**, private ctor, `withContext(...)` factory (`:87`) | nothing — call `waitFor(millis)` | none | not extendable |
| Message | `executionblock/messageblock/MessageBlockold.java:17` | — | none | **legacy**, `old` suffix, unreferenced |
| createWorkflow | `executionblock/createWorkflowBlock/WorkflowBlock.java` | — | none | **dead** — every line commented out |

Gotchas worth knowing: `BaseFunctionBlock.getDelegator()` instantiates **only
`delegatorList.get(0)`** (`:545-552`), so a second registered delegator is ignored. `Stage` is **not**
a block — it is an enum the author supplies implementing `StageProperties`
(`stage/StageProperties.java:7-10`), driven manually via `Stage.start`/`Stage.end`
(`stage/Stage.java:32`, `:54`). Blocks do **not** declare or validate a stage, despite
`CLAUDE.md:40` saying they do.

Annotation processors live in `annotation/processor/`. Only **four** are auto-discovered via
`META-INF/services/javax.annotation.processing.Processor`: `E5FunctionBlockProcessor`,
`E5WFAnnotationProcessor`, `E5AggregatorProcessor`, `E5InterventionBlockProcessor`.
`E5ContainerAnnotationProcessor` is invoked explicitly by `container/RunAnnotation.java:26` and
`E5UserInitiatedActionAnnotationProcessor` by the `createUserAction` Gradle task — so a `@E5Container`
or `@E5UserInitiatedAction` class gets no codegen from a plain `./gradlew build`.
`@E5Aggregator` is the only **RUNTIME**-retained annotation and is read reflectively
(`event/receiver/Aggregator.java:73`).

## `BaseWorkflow` and `WorkflowContext`

`workflow/BaseWorkflow.java:57-58` —
`public abstract class BaseWorkflow<E extends Enum<E> & StageProperties, I> implements ITemporalWorkflow<I>`.
Four abstract members the author must supply: `registerAggregators()` (`:70`),
`registerApplicationsUsed()` (`:72`), `StartBlockFunction(I)` (`:332`),
`execute(Enum, WorkflowContext<I>)` (`:351`), `endBlockFunction()` (`:373`). The constructor calls
both registrars immediately (`:74-76`).

`startWorkflow` (`:80`) is the Temporal `@WorkflowMethod` (`workflow/ITemporalWorkflow.java:12`).
Its order: load configs → start-timeout guard that **silently returns** if exceeded (`:88-95`) →
`serializeAndSetWorkflowContext` (`:111`) → `new Stage<>` → `processWorkflowInputSignals` (`:115`)
→ emit `WORKFLOW_STARTED` → `startBlock` → termination check → `executeWorkflow` →
`asyncExecutor.closeAllHandlers()` → `endBlock()` → emit `WORKFLOW_ENDED`. The catch ladder maps
terminal states: `WorkflowComplete`, payload-type mismatch, `E5TerminationException` →
`WORKFLOW_TERMINATED`, `SoftTerminateException` → `WORKFLOW_SOFT_TERMINATED`, else
`WORKFLOW_FAILED` (`:158-222`). `stopWorkflow` is deliberately **private** with a comment saying the
feature is withheld from consumers until rollout (`:356-362`).

Per-execution context is `WorkflowContext<I>` (`workflow/WorkflowContext.java:11`) — there is no
`E5Context` or `ExecutionContext`. It holds a package-private `WorkflowInfo<I>`, a `UserContext`, and
a `@JsonIgnore softTerminateTriggered` flag. The real field carrier is `WorkflowInfo`
(`workflow/WorkflowInfo.java:20-52`, **all fields `final`**): workflow/variant identity, temporal
ids, `deploymentVersionId`, trigger info, `Set<String> signalAppState`, `sandboxPauseAppStates`,
`isWorkflowSandbox`, `terminateRequest`, and the typed `I input`. `setWorkflowInfo` is
package-private by design; UIAs use the documented factory
`WorkflowContext.forUserAction(WorkflowInfo)` (`:27`). The context is threaded into **every** block
through its constructor and crosses the child-workflow boundary as an explicit argument
(`workflow/IContainerTemporalWorkflow.java:15`).

User-initiated actions use a separate base, `workflow/BaseUserInitiatedActionWorkflow.java:55`,
which deliberately implements **no** `@WorkflowInterface` — each generated subclass gets its own
uniquely named interface so several actions can register on one Temporal worker (`:46-54`). Entry
`startAction(AkstClientTask)` (`:66`); single abstract `handleUserInitiatedAction(...)` (`:239`).

## Signals

Two types, four states: `SignalType` = `APP_SIGNAL`, `WORKFLOW_SIGNAL`
(`workflow/signal/SignalType.java:3-6`); `SignalState` = `PAUSE`, `RESUME`, `WORKFLOW_TERMINATE`,
`WORKFLOW_SOFT_TERMINATE` (`workflow/signal/SignalState.java:3-8`).

`processSignal` is the Temporal `@SignalMethod` (`ITemporalWorkflow.java:15`), implemented at
`BaseWorkflow.java:416` and delegating to `SignalProcessorUtil.processSignal`
(`workflow/signal/SignalProcessorUtil.java:50-59`) — an **if/else-if chain, not a switch**, with no
else. Second level: app signals mutate `workflowContext.{add,remove}AppToSignalState` then call
`updateTaskQueueAbtPauseSignal` (`:66-76`); workflow signals route terminate vs soft-terminate and
log "Unsupported signal state" for anything else (`:244-256`). **There is no `@QueryMethod` anywhere
in `src/main`** — workflow state cannot be queried, only signalled. Clients signal untyped:
`workflowStub.signal("processSignal", signalEvent)` (`wre/util/E5TemporalClient.java:307`, `:389`).

Pause/resume is implemented **against AKST**: a pause parks the queued task by setting
`taskDeferTime` to `INSTANT_LONG_FAR_FUTURE` and a resume un-parks it with
`INSTANT_LONG_FAR_PAST` (`SignalProcessorUtil.java:39-40`, `:373`). Terminate cancels only
`ActivityStatus.ACTIVE` delegations (`:127`, `:263`). Every Kafka/DB side effect is wrapped in
`io.temporal.workflow.Workflow.sideEffect` to stay replay-deterministic (`:102`, `:152`, `:289`,
`:331`, `:405`, `:420`, `:477`, `:507`, `:593`, `:642`).

## State persistence

`DBAccessType` = `STATE`, `AKST`, `CORE_INTERNAL` (`config/DBAccessType.java:3-5`), held in
`Configs.sessionFactoryMap`. Which factory exists depends on `WORKER_MODE` (table above) — so
`WFStateService`, which resolves `STATE` (`workflow/WFStateService.java:54`), **only works in
`WF_WORKER`**.

| Entity | Table | Purpose |
|---|---|---|
| `akst/model/Task.java` | `e5_task` | the task queue — [e5-task](../../tables/e5-task.md) |
| `converter/E5Token.java:15-25` | `e5_token` | delegation↔Temporal-token correlation: `temporalToken` (unique, 500), `workflowId`, `workflowRunId`, `activityStatus`, `signalReferenceId`, `appName`, `delegationId` (unique), `completionReceived`, `completionPayload`; five indexes |
| `converter/ConverterEntity.java:24-29` | `e5_converter` | overflow store for Temporal payloads |

Author-facing state goes through `WFStateService` (`workflow/WFStateService.java:40`), a singleton
over `e5.stateservice` with `find/insertOne/insertMany/updateOne/updateMany/deleteOne`, batch size
1000, results funnelled through `Workflow.sideEffect`.

The Temporal payload codec is `converter/E5Converter.java:28` (`PayloadConverter`, encoding
`json/plain`): JSON → gzip → base64, and **above `(int)(1.9 * 1024 * 1024)` bytes** the compressed
string is written to `e5_converter` and only a `referenceId` rides in the Temporal payload
(`:50-53`). That path needs the `CORE_INTERNAL` session factory, which is why `WF_ENGINE` and
`WF_WORKER` both build it.

## Event / aggregator pipeline

`EventEmitter` (`event/emitter/EventEmitter.java:31`) is a singleton with two `init` overloads —
`init(String outboundEndpoint)` (`:51`) and `init(AppenderType, String)` (`:55`). `send` emits
**inside `Workflow.sideEffect`** and bumps an `eventOrder` (`:60-66`). `AppenderType` =
`KAFKA_APPENDER`, `SQLITE_APPENDER`, `AGGREGATOR_DYNAMODB_APPENDER`, `AGGREGATOR_AUTOHUB_APPENDER`
(`event/emitter/eventsync/AppenderType.java:3-10`), but `EventSink.loadAppenderLists` only handles
the first two and throws `"Event stream outbound endpoint is mandatory."` on a null endpoint
(`event/emitter/eventsync/EventSink.java:28-37`). `KafkaAppender` sends with the literal key
`"key"` (`:32`) — no partition affinity.

`Event<B>` (`event/Event.java:13`) carries `eventSourceId, eventId, eventType, sequenceNo,
eventMetadata, payload, level, timestamp, workflowState`. `EventType`
(`event/emitter/EventType.java:8-113`) has **50 constants** on four levels: 0 workflow, 1 block,
2 sub-step, 3 delegatee; unknown names map to `UNKNOWN` (`:126`). Roughly 70 payload classes sit in
`event/`.

`Aggregator` (`event/receiver/Aggregator.java:42`) is abstract with **one** abstract method,
`handleEvent(Event, AggregatedState)` (`:44`). Its constructor requires `@E5Aggregator` at runtime
(`:73-76`), validates three configs, and cross-checks the annotation's `targetTypes()` against
`aggregatorOutbound[].targetType`, throwing `AGGREGATOR_TARGETS_CONFIG_MISSING` on a gap
(`:104-109`). `executeAction` (`:213`) keys state on `aggregatorName + "_" + workflowInstanceId`,
**skips `WORKFLOW_TRIGGERED` and `WORKFLOW_FAILED` entirely** (`:217-218`), and persists only when
the state actually changed (`:258-264`), enforcing a 380 KB compressed item limit (`:160-173`).
Store selection is `platformConfig.aggregatorStore` ∈ `SQLITE_STORE` / `DYNAMODB_STORE`, **defaulting
to DynamoDB when null** (`:92-97`) — and `aggregatorStore` is not in
`PlatformConfig.schema.json`, so it is never validated. Targets: `AutoHub`, `FreshDesk`, `Email`,
`Whitelabel` (`config/AggregatorTargetType.java:5-6`).

Registration is two-step: the workflow declares classes via `BaseWorkflow.registerAggregators()`,
and the **`AGGREGATOR_NAME` env var** picks the single one to run
(`E5WorkflowEngine.java:405-406` → `startAggregatorContainer`, `:587-613`), matched by
`getSimpleName()` against `workflowConfig.aggregatorsRegistered` and requiring an
`(AggregatorConfig, KafkaConfig, PlatformConfig)` constructor.

## Delegation: v1 vs v2

`executionblock/functionblock/delegator/RPADelegator.java:83` is the fork:

- **v2** → builds a `TaskTransport` (`TaskCreate`, `taskType InvokeFunctionBlock`) addressed to an
  **AKST** endpoint id resolved from `workerConfig.workerMappings[]`
  (`utility/TaskTransportUtil.java:29-44`), inserts an `E5Token` for correlation **before** routing
  (`:147-158`), and has a second sub-path that emits `TaskUpdate` when a token already exists
  (re-delegation after pause/resume, `:86-141`). The RPA Gateway is bypassed.
- **v1** (`else`, `:161-185`) → builds a `TaskTransportV1` with
  `FunctionBlockTaskDataV1{appName, workerType = WorkerType.Robotframework (hardcoded), fbPayload}`
  and calls the **no-endpoint** `routeTask` overload, so the SDK resolves the destination from the
  static `config/TaskProducerConfig.yaml` table → `gatewayOutbound` → the topic overwritten by
  `worker.gatewayEndpoint`. **No `E5Token` is inserted**, so there is no correlation row.

Either way the activity ends with `doNotCompleteOnReturn()` and `return null` (`:191-192`) — FB
activities always complete asynchronously. Intervention delegation
(`executionblock/interventionblock/delegator/AkstInterventionDelegator.java:75-77`) **hard-requires
v2** and is the one place that throws instead of silently degrading. `rpagateway/` is two files and
is v1-only by construction.

Because `taskProtocol` can be absent and nothing validates it, the v1 branch is the silent default:
task-protocol-silently-defaults-to-v1 (archived).

## The WF Engine's AKST client

In `WF_ENGINE`, `startAkstClientPoller()` (`E5WorkflowEngine.java:517-524`) builds a single daemon
thread named `akst-client-poller` (`wre/engine/akst/AkstClientPollingRuntimeFactory.java:41-45`).
It returns **`null`, silently, when `akstClientConfig` is absent or `enabled: false`**
(`:22-24`), and throws `AKST_CLIENT_CONFIG_INVALID` when `targetServer`/`vqName` are blank
(`:25-27`). The loop (`wre/engine/akst/AkstClientPoller.java:27-55`) is
`isTaskAvailable` → `getTask` → process, sleeping `pollIntervalMillis` (default **30 000 ms**) only
when nothing is available or a poll fails — **no delay on the success path**. A received task becomes
a **User-Initiated-Action Temporal workflow**, not a function block:
`EngineAkstClientTaskProcessor.process` → `E5TemporalClient.launchUserAction(task)`
(`wre/util/E5TemporalClient.java:213-231`), where the action name is `task.fbName()` and must appear
in `engineConfig.uiActionMappings` or it throws `USER_INITIATED_ACTION_UNKNOWN`.

**Inbound:** Kafka (`engine.inboundActionEndpoint`, the AKST command queue, the aggregator inbound
topic), Temporal task queue, gRPC `TaskService` in AKST mode.

**Outbound:** Kafka (`worker.eventStreamOutboundEndpoint`, `worker.akstEndpoint`,
`worker.gatewayEndpoint`, `akstService.workflowEndpoint`, `akstService.metricsEndpoint`,
`aggregatorsConfig[].aggregatorOutbound[].endpoint`, `workflowManager.endpoint`,
`trigger.outboundEndpoint`), Temporal, Postgres, DynamoDB, S3, Notification Hub, and gRPC to AKST
from the engine poller.

**Data owned:** `e5_task`, `e5_token`, `e5_converter` — all Hibernate-created, no migrations.

**Config keys:** `.env` is the config transport. `ConfigsLoader.loadEnvConfigs` (`:131-378`)
switches on top-level keys: `workflow`, `temporal`, `dbconfig`, `engine`, `trigger`, `worker`,
`aggregatorsConfig`, `kafka`, `rpaGateway`, `akstService`, `akstClientConfig`, `notificationHub`,
`workflowManager`, plus `fileStorage`. Direct env reads: `WORKER_MODE`, `AGGREGATOR_NAME`,
`WF_VARIANT_BUILD_NUMBER`, `DEVKIT_MODE`, `WORKFLOW_INPUT`, `namespace`. Three classpath YAMLs are
loaded and schema-validated at startup: `config/KafkaConfig.yaml`, `config/PlatformConfig.yaml`,
`config/TaskProducerConfig.yaml` (`:78-98`). 24 `*.schema.json` live in `src/main/schemas/`. See
[worker-config](./worker-config.md) and
[akst-routing-table](../../communication/akst-routing-table.md). Values are never recorded here.

**ETP role:** all of them, depending on mode — Task Initiator (`WF_WORKER` delegating),
Task Queue + Task Router (`AKST_SERVICE`), and a Task Reader-ish consumer (the `WF_ENGINE` poller).

**Failure modes:**
- **The aggregate config validation is dead.** `ConfigsLoader.validateConfigs` is commented out at
  both call sites (`:127`, `:393`), so most config errors surface as runtime NPEs rather than
  startup failures.
- Every `WORKER_MODE` failure path is `System.exit(1)` after logging (`:455-458`), and `WF_ENGINE`
  and `AKST_SERVICE` end with `System.exit(0)` when their executors drain (`:372`, `:447`).
- `WORKER_MODE` is read from a `.env` **file**, loaded with `ignoreIfMissing().ignoreIfMalformed()` —
  a missing or malformed file yields `WORKER_MODE_MISSING`, not a parse error.
- `startAkstClientPoller` returning null is indistinguishable from a healthy disabled poller.
- `BaseFunctionBlock.getDelegator()` silently ignores every delegator after the first.
- `EventType.getName` maps any unrecognised event name to `UNKNOWN` rather than failing.
- The aggregator defaults to DynamoDB when `platformConfig.aggregatorStore` is null, and that key is
  unvalidated.

**Code-level landmarks:**
- `wre/engine/E5WorkflowEngine.java:324-459` — the `WORKER_MODE` switch; read this first
- `workflow/BaseWorkflow.java:80-222` — the whole workflow body and its terminal-state ladder
- `config/ConfigsLoader.java:131-378` — the `.env` key switch and its load-time side effects
- `executionblock/functionblock/delegator/RPADelegator.java:83-185` — the v1/v2 delegation fork
- `workflow/signal/SignalProcessorUtil.java:50-76` — signal dispatch and the AKST pause mechanism
- `event/receiver/Aggregator.java:213-271` — `executeAction`, the aggregation loop

**Known divergences:**
task-protocol-silently-defaults-to-v1 (archived) ·
yamltoenv-task-protocol-fanout-is-dead-code (archived) ·
akst-lives-in-platform-core-not-akst-service (archived) ·
routing-table-source-subsystem-does-not-exist (archived) ·
local-router-emits-the-failure-the-spec-forbids (archived)

Plus five doc-only drifts not worth separate cards, all in repo-root markdown:
`CLAUDE.md:7` says version `1.7.3` (code: `1.9.2`); `CLAUDE.md:40` claims blocks declare and are
validated against a stage (**no block class references `Stage` at all**); `CLAUDE.md:92` omits
`@E5InterventionBlock` and `@E5UserInitiatedAction`; `CLAUDE.md:79-81` omits `forRPAGateway`;
`PauseResumeExample.md:13-21,41-42` shows `AppSignalConfig` as a nested class with a
`signalReferenceId` field and an `AppStatus.PAUSE` constant — the real class is top-level with two
fields (`workflow/AppSignalConfig.java:12-15`) and the enum is `PAUSED, RUNNING, PAUSING, RESUMING`
(`workflow/AppStatus.java:3-8`), so the documented example **would not compile**;
`UserInitiatedAction.md` documents `startAction(Task task)` with `task.getTaskPayload()` /
`getTaskIdentifier()`, but the signature is `startAction(AkstClientTask)` with `fbPayload()` /
`taskId()` (`workflow/BaseUserInitiatedActionWorkflow.java:68-71`).

**Relevant runbooks:** none yet.

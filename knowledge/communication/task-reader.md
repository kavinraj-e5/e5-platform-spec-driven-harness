---
type: subsystem
name: Task Reader
repo: e5-task-reader
repo_path: platform-whole-context/subsystem-workflow-interaction-support/e5-task-reader
branch: release/v1.0.5
commit: 83a1aad
language: java
build: gradle
deployment: "none in-repo — no Dockerfile target in a manifest, no deployment-configs/, no k8s yaml"
namespace_pattern: "{client}-{workflow}-{variant}-{env} (co-located with the AKST it polls; not declared in-repo)"
etp_roles: [task-reader]
owns_schema: false
ports: []
health: none
calls: [akst]
called_by: []
provenance: code
verified: 2026-10-02
prose_reviewed_at_commit: ed82af4
---

# Task Reader

A ~600-line, single-threaded bridge: it polls AKST's `peekTask` RPC and republishes the workload
snapshot onto one Kafka topic for the Dynamic Scaling Service. **Not Spring** — a plain `main()` and
a `while (true)` loop.

**Responsibility (what it owns):** exactly one hop. Call `peekTask`, map the response field-for-field,
stamp two meta flags, publish. Plus the degradation policy for "AKST is unreachable" and "the queue
looks empty".

**Not its responsibility:** reading the database (it has **no JDBC driver on the classpath** — no
`@Entity`, no SQL, nothing), making scaling decisions, and exposing any API. It has **no inbound
surface of any kind**: no REST, no gRPC server, no Kafka listener, no `@Scheduled`.

**Entry points:** `src/main/java/com/e5/taskreader/TaskReader.java:14-17` —
`ConfigsLoader.loadConfigs(); startTaskReader();`. Declared at `build.gradle:98`
(`mainClass = 'com.e5.taskreader.TaskReader'`) and in the jar manifest (`:110`).

**Inbound:** none. The loop is hand-rolled:
`src/main/java/com/e5/taskreader/service/TaskReaderService.java:113-135` —
one-time `applyBootWait()` (`taskReader.initialDelayMs`), then
`runPollIteration()` / `Thread.sleep(taskEmissionInterval)` forever, with a JVM shutdown hook that
closes the gRPC channel (`:86-108`).

**Outbound:**
- **gRPC → AKST `TaskService`**, plaintext, host/port from the `akstGrpc` config
  (`TaskReaderService.java:39-42`). It generates stubs for all four RPCs but **calls only
  `peekTask`** (`:151`) — `getTask`, `isTaskAvailable` and `healthCheck` are never invoked.
  **No deadline is set**, so a hung AKST stalls the single poll thread indefinitely.
- **Kafka → one topic**, `kafka.dssTopic` (`service/TaskAction.java:20`, send at `:31`
  `producer.send("", tasks, topic)` — note the **empty-string key**, so every record goes to one
  partition with no per-deployment affinity). Payload is
  `ScalingEstimatorRequest { List<AkstWorkload> akstWorkload; Map<String,Object> metaInfo; }`
  (`model/ScalingEstimatorRequest.java:15-18`).

**Data owned:** none. The only state is two in-memory counters,
`emptyTaskCount` and `grpcUnavailableCount` (`TaskReaderService.java:26-27`), lost on restart.

**Config keys:** read from a `.env` in the working directory, where **each env var's value is a whole
YAML/JSON map** parsed into a typed class (`config/ConfigsLoader.java:126-136`). Four composite
variables — nothing is a conventional one-key-per-var env:

| Env var | Nested keys |
|---|---|
| `kafka` | `bootStrapServer` (@NotBlank), `dssTopic` (@NotBlank) |
| `akstGrpc` | `host` (@NotBlank), `port` (@Positive) |
| `deploymentDetails` | `deploymentVersionId` (@NotBlank) |
| `taskReader` | `alwaysOverrideToMax`, `maxEmptyTaskCount` (@NotNull @Min 1), `grpcUnavailableEscalationThreshold` (@NotNull @Min 1), `taskEmissionInterval` (@NotNull @Min 1), `initialDelayMs` |

No `application.yml`, no `*.schema.json`, no `@Value`, no `@ConfigurationProperties`. Values are
never recorded here.

**ETP role:** Task Reader, and only that — it observes the queue and never mutates it. It is the one
service in this scope whose role name matches its behaviour exactly.

## The poll decision table

`TaskReaderService.runPollIteration` (`:140-191`), in evaluation order:

| Case | Behaviour |
|---|---|
| `taskReader.alwaysOverrideToMax == true` | skip gRPC entirely, reset `emptyTaskCount`, publish an **empty** workload with `overrideToMaxReplicas = true` (`:141-147`) |
| `peekTask` throws | reset `emptyTaskCount`, `grpcUnavailableCount++`; once `>= grpcUnavailableEscalationThreshold`, publish override-to-max (`:150-156`, `:181-191`) |
| non-empty workloads | map and publish, then reset **both** counters (`:162-168`) |
| empty workloads | publish an empty heartbeat with `overrideToMaxReplicas = false` for the first `maxEmptyTaskCount` iterations, then publish nothing (`:170-178`) |

`metaInfo` carries exactly two keys — `deploymentVersionId` and `overrideToMaxReplicas`
(`:243-248`) — which are exactly the two DSS reads
(`e5-ss-dynamic-scaling-service/.../ScalingEstimatorService.java:69`, `:237`). The workload mapping
(`convertToDssTaskInfo`, `:231-241`) copies `taskId, taskStatus, taskVq, createdAt, priority,
deadline, fbName`.

**Failure modes:**
- **`peekTask` returns an empty list when AKST's `inMemoryTaskStoreEnabled` is false** — and this
  service reports that as "empty queue", which DSS reads as "idle":
  peek-task-is-empty-unless-a-feature-flag-is-on (archived).
- **A Kafka publish failure silently drops that poll's snapshot.** `TaskAction.java:35-38` rethrows,
  `TaskReaderService.java:133-135` logs and continues. No retry, no DLQ, no buffering:
  scaling-signal-is-dropped-on-consumer-rebalance (archived).
- **No gRPC deadline, no circuit breaker, no retry library.** The escalation counter is the entire
  degradation strategy — and it is **never reset on the failure path**, nor on a successful-but-empty
  poll (`:170-178`), so once armed it stays armed.
- `InterruptedException` **breaks the loop permanently** (`:127-130`): the process falls out of
  `main()` and the container exits, relying on a k8s restart policy this repo does not configure.
- No signal handling and no graceful drain.
- The whole parsed config is logged at INFO on every startup, including broker addresses and
  deployment ids (`config/ConfigsLoader.java:130`).
- A missing `.env` fails late and unclearly: `loadDotenv` uses
  `.ignoreIfMissing().ignoreIfMalformed()` (`:28-33`), so the failure surfaces as a generic
  "Exception Occurred during read from Config File" or a bean-validation message. One wrapper even
  reports the wrong section — `throw new RuntimeException("Error in monitor Config Validation", …)`
  for the `akstGrpc` block (`:62`).
- The Dockerfile copies only `build.gradle` and `./src` (`Dockerfile:14-15`) — **not
  `settings.gradle`, `gradle/` or `gradlew`** — so the image builds with the base image's Gradle and
  no `rootProject.name`.
- `build.gradle:20` says `version '1.0.5'` on branch `release/v1.0.5` (it said 1.0.4 until commit 83a1aad fixed it).

**The contract with DSS is duplicated, not shared.** `ScalingEstimatorRequest` and `AkstWorkload`
exist as byte-identical classes in both repos
(`model/ScalingEstimatorRequest.java:15-18` vs
`e5-ss-dynamic-scaling-service/.../dto/ScalingEstimatorRequest.java:15-18`; same for `AkstWorkload`)
with no shared artifact and no schema. Its `task.proto` is also a third divergent copy of AKST's:
three-divergent-copies-of-task-proto (archived).

**Code-level landmarks:**
- `service/TaskReaderService.java:113-135` — the loop
- `service/TaskReaderService.java:141-192` — the four-case decision table
- `service/TaskReaderService.java:152` — the single `peekTask` call, no deadline
- `service/TaskAction.java:20-38` — the publish and its silent-loss path
- `config/ConfigsLoader.java:80-136` — the composite-env config mechanism
- `src/main/proto/task.proto:71-76` — its own copy of `TaskService`

**Known divergences:**
peek-task-is-empty-unless-a-feature-flag-is-on (archived) ·
scaling-signal-is-dropped-on-consumer-rebalance (archived) ·
three-divergent-copies-of-task-proto (archived)

README drifts not worth separate cards: `README.md:52,57` documents `alwaysOverrideToMax` under
`deploymentDetails`, but the field is on `taskReader` (`config/TaskReaderConfig.java:18-19`;
`DeploymentDetailsConfig.java:13-16` has only `deploymentVersionId`) — following the README verbatim
silently disables the flag; `README.md:53`'s own `taskReader` example **fails startup validation**
because it omits the mandatory `taskEmissionInterval`; `README.md:61` says the poll interval "is 60
seconds" when it is `taskReader.taskEmissionInterval` (60 000 appears only in a test-only
constructor, `:82`); `initialDelayMs` is undocumented entirely.

**Relevant runbooks:** none yet.

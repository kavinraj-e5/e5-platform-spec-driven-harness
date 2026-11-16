---
type: subsystem
name: Dynamic Scaling Service (DSS)
repo: e5-ss-dynamic-scaling-service
repo_path: platform-whole-context/subsystem-workflow-interaction-support/e5-ss-dynamic-scaling-service
branch: release/v1.3.2
commit: 6f9a27d
language: java
build: gradle
deployment: dynamic-scaling-service-ss
namespace_pattern: platform-subsystem-{env}
etp_roles: [task-initiator]
owns_schema: false
ports: [8091]
health: http /actuator/health (and /health/liveness, /health/readiness)
calls: [deployment-mgmt, task-manager, workflow-manager]
called_by: [task-reader]
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.1.2
deployment_lag: release/v1.3.2 available, v1.1.2 running
prose_reviewed_at_commit: b0faafd
---

# Dynamic Scaling Service

Turns AKST queue depth into a replica count, then **asks someone else to apply it**. Spring Boot,
Java 25 (`build.gradle:15`, `:32`), gRPC **client only**.

**Responsibility (what it owns):** the scaling *decision*. It bin-packs the pending task workload
against per-app execution times, credential availability and min/max replicas, writes the resulting
request to `deployment-mgmt`, and emits a `TaskTransport` command to the Task Manager.

**Not its responsibility:** performing the scale. **It never touches the Kubernetes API** — grep
for `KubernetesClient`, `io.fabric8`, `HorizontalPodAutoscaler`, `HPA` returns nothing. It also owns
no database: no `@Entity`, no `DataSource`, no JDBC, no Flyway, no SQL anywhere. And it **does not
call AKST** — no `getTask`, `isTaskAvailable` or `peekTask` reference exists in the repo.

**Entry points:** `src/main/java/e5/platform/ss/scaling/DynamicScalingService.java:25-33` —
`@SpringBootApplication(exclude = KafkaAutoConfiguration.class)`, `@EnableScheduling`.
Startup one-shot: `config/StartupScalingSequenceRunner.java:30` (`ApplicationRunner`, `@Order(0)`,
`@Profile("!test")`).

**Inbound:** no REST controllers and **no gRPC server** (`build.gradle:147-148` carries only
`grpc-client-spring-boot-starter`; zero `@GrpcService`). Three Kafka **batch** listeners and two
schedulers:

| Trigger | Where | Topic / interval key | Payload |
|---|---|---|---|
| `@KafkaListener` | `consumer/MetricsConsumer.java:85` | `akst.metrics-ingestion.kafka.topic-task-metrics` | `AkstMetric` |
| `@KafkaListener` | `consumer/ScalingEstimatorConsumer.java:64-67` | (topic is deploy-time configuration, not recorded) | `ScalingEstimatorRequest` — **from `task-reader`** |
| `@KafkaListener` | `consumer/TaskManagerBatchConsumer.java:38` | a topic config key | `com.e5.platform.sdk.tasks.models.TaskTransport` |
| `@Scheduled` fixedDelay | `scheduler/ScaleEngineScheduler.java:27-31` | `scale-engine.schedule.interval` (10 s), gated by `scale-engine.schedule.enabled` | — |
| `@Scheduled` fixedRate | `scheduler/CredentialAvailabilityCompute.java:64-68` | `app.credential-availability.schedule-interval-ms` (60 s) | — |

No cron expressions anywhere.

**Outbound:**
- gRPC → one target, `db-mgmt-service` (`grpc/DssGrpcClient.java:38-39`, address key
  `grpc.client.db-mgmt-service.address`). Eight RPCs: `sendAkstMetrics`, `getScalingInfo`,
  `createDssTask`, `updateDssTask`, `getDynamicScalingRequestsByStatus`, `getAppCredentialInfo`,
  `pollDynamicScalingRequests`, `checkDssSubscription`.
- Kafka → a topic config key (`kafka/service/KafkaProducerService.java:24`), plus
  two DLQ topics (`<topic>.DLQ`) for the metrics and task-manager containers.
- HTTP → one `WebClient` POST to `app.credential-availability.api-url`
  (`scheduler/CredentialAvailabilityCompute.java:138-143`), scheme-allowlisted to http/https
  (`client/HttpAPIClient.java:40`).

**Data owned:** none. The only in-process state is a static
`ConcurrentHashMap` credential cache (`service/CredentialAvailabilityInMemory.java:27`), keyed
`poolName + "-" + workflowName`.

**Config keys:** ~45, all in `src/main/resources/application.yaml`, almost all env-injected. Groups:
`logging.level.*`; `spring.kafka.bootstrap-servers`, `spring.kafka.listener.ack-mode`,
`spring.lifecycle.timeout-per-shutdown-phase`, `spring.server.shutdown`;
`akst.metrics-ingestion.kafka.{topic-task-metrics, consumer.group-id, consumer.max-poll-records,
consumer.auto-offset-reset}` and `akst.metrics-ingestion.retry.{max-attempts, initial-interval,
multiplier, max-interval}`; `akst.scaling-estimator.consumer.{topic, group-id, max-poll-records,
auto-offset-reset}`; `app.credential-availability.{api-url, schedule-interval-ms}`;
`app.http-api.{read-timeout-seconds, retry.max-attempts, retry.initial-backoff-ms}`;
`scale-engine.schedule.{enabled, interval}`, `scale-engine.poll.{max-row-count,
consecutive-scale-down-count, pending-requests-interval, existing-requests-interval}`,
`scale-engine.processing.thread-pool-size`; `task-manager.kafka.{consumer.*, producer.topic,
producer.workflow-manager-vqname, retry.*}`; `grpc.client.db-mgmt-service.{address, negotiationType}`;
`resilience4j.circuitbreaker.instances.dbMgmtService.*`; `management.*` (port, exposure, probes,
otlp, tracing). `@ConfigurationProperties` prefixes: `akst.metrics-ingestion`,
`akst.scaling-estimator.consumer`, `task-manager.kafka`. **No `*.schema.json`, no `env.yaml`.**
Values are never recorded here.

**ETP role:** Task Initiator. It mints `TaskTransport` with `taskInfo.taskType = E5_WF_MANAGER` and
`fbName` ∈ {`ENABLE_DEPLOYMENT`, `DISABLE_DEPLOYMENT`} and dispatches via the Task Manager. It is
never Queue, Router or Worker.

## The scaling algorithm

**Stage 1 — decide the replica count** (`service/ScalingEstimatorService.java:50`, triggered by the
the workload topic batch). Inputs: the workload list in the Kafka message, `GetScalingInfo` over gRPC
(current/min/max replicas, `vqName`, `appExecutionTime`, credential pool), the in-memory credential
cache, and `CheckDssSubscription`. Decision ladder, in order:

1. min == max ⇒ dynamic scaling disabled, skip (`:82-85`).
2. credential starvation: `totalResourceCount < currentReplicas && minReplicas < currentReplicas`
   ⇒ SCALE_DOWN to `max(minReplicas, totalResourceCount)` (`:93-110`).
3. `overrideToMaxReplicas` meta flag (set by `task-reader`) ⇒ go to `maxReplicas`, clamped by the
   credential cap (`:111-130`, `:271-284`).
4. **no tasks for the app ⇒ SCALE_DOWN to `minReplicas`** (`:134-151`).
5. otherwise `MinimalPodScheduler.calculatePod(...)` clamped to [min, max], then capped by available
   credentials (`:153-155`, `:286-328`).
6. direction = `targetPodCount > currentReplicas ? SCALE_UP : SCALE_DOWN`; equal ⇒ no-op. One
   `ProcessDssTaskRequest` per app with `status = SCALING_PENDING`, batched into one `createDssTask`
   (`:157-195`).

`MinimalPodScheduler` (`algorithm/MinimalPodScheduler.java`) binary-searches the number of extra
pods, checking feasibility with a backtracking pack keyed on
`latestStart = deadline - processingTime` (`:133`). Infeasible ⇒ it jumps straight to `maxReplicas`
(`:288`).

**Stage 2 — act** (`service/ScaleEngineService.java:78-149`, every 10 s). Poll
`pollDynamicScalingRequests`, skip unless there is a newly-transitioned `SCALING_IN_PROGRESS` row
with no payload, group by `(deploymentVersionId, appName)`, apply consecutive-scale-down smoothing
(`service/ScaleEngineProcessor.java:111-128`), compute a signed delta
(`:249-265`), then build a `TaskTransport` with
`fbName = scaleAction == SCALE_DOWN ? "DISABLE_DEPLOYMENT" : "ENABLE_DEPLOYMENT"` and
`fbPayload = {appName, deploymentVersionId, replicaCount}` (`:427-452`) and publish it to the Task
Manager. Completion returns on a topic config key, correlated by
**`metaInfo.userMetaInfo.scalingId`** (`ScaleEngineProcessor.java:453` →
`kafka/service/TaskManagerDssUpdateService.java:167`). DSS does **not** emit the v1
`temporalToken`: `resolveScalingId` prefers `scalingId` and falls back to `temporalToken`
only if it is absent (`:193-208`), rejecting a response carrying neither with
`"scalingId or temporalToken: required"` (`:169`). The v1-correlation trap belongs to
`interface`, not to DSS — see
v2-envelope-uses-v1-correlation-field (archived),
which is about `interface` only.

> Corrected 2026-10-12. The previous wording said DSS correlated on `temporalToken`, and
> cited lines 452-454 of a `ScaleEngineProcessor.java` that is 453 lines long. `e5 cite`
> found the overrun; re-reading the method found the claim had been wrong with it.

**Failure modes:**
- **The scheduler's deadline input is the processing time**, so deadline-aware packing does not
  happen: dss-deadline-is-overwritten-by-processing-time (archived).
- **The estimator consumer `seekToEnd`s on every assignment** and has no error handler and no DLQ,
  while its producer has no retry:
  scaling-signal-is-dropped-on-consumer-rebalance (archived).
- An **empty** workload list is read as "idle" and scales to `minReplicas` — which is also what
  AKST returns when `inMemoryTaskStoreEnabled` is false:
  peek-task-is-empty-unless-a-feature-flag-is-on (archived).
- **Two of eight gRPC calls have no deadline** — `getScalingInfo` (`DssGrpcClient.java:113`) and
  `getDynamicScalingRequestsByStatus` (`:265-266`) — so they can block a Kafka listener or the
  scheduler thread indefinitely. The other six use 30 s.
- The circuit breaker (`dbMgmtService`) is applied to **`sendMetrics` only**
  (`DssGrpcClient.java:50`), and opening it pauses **only the metrics container**
  (`consumer/CircuitBreakerStateListener.java:69`).
- Four RPCs return `null` on failure and callers continue (`DssGrpcClient.java:118`, `:217`, `:244`,
  `:271`, `:330`); the whole scheduled run is wrapped in a bare
  `catch (Exception e) { log.error(...) }` (`ScaleEngineService.java:146-148`).
- `TaskManagerDssUpdateService.java:236-240` maps **any unrecognised completion status to
  `SCALING_COMPLETED`** via its `default ->` branch.
- The k8s Service targets **port 50051** (`deployment-configs/*-config.yaml:16-17`) but the app binds
  no such port — the only listener is the actuator port, documented as 8091
  (`application.yaml:117`, `CLAUDE.md:58`) and overridden to **8090** in all four deployment-configs
  (`deployment-configs/dev-config.yaml:120-121`). A probe written against the documented port fails.
- `DB_MGMT_GRPC_PORT` has three different values across three files: `50054`
  (`application.yaml:94`), `50052` (test profile), `50051` (all deployment-configs).
- `prod-config.yaml` is a near-verbatim copy of dev — it still carries
  a topic env var: 'e5_dev_akst_metrics_endpoint'` (`:36`) and `-dev` consumer-group ids
  (`:38`, `:55`, `:88`) **in prod**.
- `app.credential-availability.api-url` is **empty in every shipped deployment-config**, so the
  credential HTTP call is skipped with a `log.error` and the credential caps are never populated
  (`scheduler/CredentialAvailabilityCompute.java:107-110`).
- `build.gradle:10` says `version = '1.3.2'` on branch `release/v1.3.2`.

**Code-level landmarks:**
- `service/ScalingEstimatorService.java:50-196` — the whole decision ladder
- `service/ScalingEstimatorService.java:392-402` — the deadline/processingTime defect
- `algorithm/MinimalPodScheduler.java:247-346` — the bin-packing search and its `maxReplicas` fallback
- `service/ScaleEngineProcessor.java:251-267`, `:428-456` — the delta formula and the emitted command
- `grpc/DssGrpcClient.java:38-39` — the single gRPC dependency
- `consumer/ScalingEstimatorConsumer.java:60` — the `seekToEnd`

**Known divergences:**
dss-deadline-is-overwritten-by-processing-time (archived) ·
scaling-signal-is-dropped-on-consumer-rebalance (archived) ·
peek-task-is-empty-unless-a-feature-flag-is-on (archived) ·
v2-envelope-uses-v1-correlation-field (archived)

README drifts not worth separate cards: `README.md:55` calls forwarding metrics its "sole
responsibility" while `CLAUDE.md:34` correctly says there are three flows; `README.md:83,97,176`
name a class `MetricsGrpcClient` that does not exist (it is `DssGrpcClient`); `README.md:178` names
a proto `scaling_service.proto` that does not exist; `README.md:125` claims the circuit breaker
pauses *all* Kafka containers; `README.md:151` and `:150` give the wrong defaults for the gRPC port
and the metrics topic; `README.md:149` documents a `spring.kafka.consumer.group-id` /
`KAFKA_CONSUMER_GROUP_ID` pair that **does not exist**; `README.md:144-155` lists 7 of ~45 config
keys; `README.md:160-188` omits half the packages on disk.

**Relevant runbooks:** none yet.

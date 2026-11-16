---
type: subsystem
name: Credential Monitor
repo: e5-platform-ss-credential-monitor
repo_path: platform-whole-context/subsystem-workflow-interaction-support/e5-platform-ss-credential-monitor
branch: release/v1.1.1
commit: 2c003da
language: java
build: gradle
deployment: e5-platform-ss-credential-monitor-pl-subsystem
namespace_pattern: platform-subsystem-{env}
etp_roles: [task-worker]
owns_schema: false
ports: [50051]
health: none
calls: [deployment-mgmt, workflow-ops, event-store, task-manager]
called_by: [workflow-ops]
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.1.0
prose_reviewed_at_commit: d60378f
deployment_lag: release/v1.1.1 available, v1.1.0 running
---

# Credential Monitor

A **fork of `e5-monitoring-service`**, renamed, whose *only* added purpose is to keep
[deployment-mgmt](deployment-mgmt.md)'s `credential_assignment_registry` ledger truthful and to
reclaim credential locks held by dead `fb-worker` pods. **This service had zero documentation
anywhere before this card** (independently noted at `../../constitution/ownership.md:61-62`);
its only Markdown file is unmodified Spring Initializr boilerplate describing frameworks it
does not use.

**Responsibility (what it owns):** Two credential loops and nothing else that is credential-related:
1. **Ledger mirroring** — consume worker-emitted `FETCHED`/`RELEASED` credential events from
   Kafka and write them to DMS.
2. **Leak reclamation** — watch Kubernetes Pod events, and when an `fb-worker` pod dies or
   restarts, ask SQS to unlock every credential it was holding.

**Not its responsibility:** It does **not distribute, lease or unlock credentials**. The actual
unlock is performed by the downstream SQS consumer; the release *action* belongs to
workflow-manager. It owns **no database** — every byte of credential state lives in DMS. It is
also not a health service, despite ~70% of the forked code being Kafka/Temporal/Postgres health
probing that still runs if the matching job name is enabled.

**Entry points:** `MonitoringEngine.java:9-13` — `loadConfigs()` → `initiateClient()` →
`initiateServer()`. The `Thread.sleep(5s)` at `:15` is **unreachable** because
`GrpcServer.startServer()` blocks on `server.awaitTermination()` (`GrpcServer.java:73`).
Plain Java 17, **no Spring**. Fat-jar `Main-Class` is `com.e5.MonitoringEngine`
(`build.gradle:21`); the committed `META-INF/MANIFEST.MF:1` says `com.e5.ClientMain`, **a class
that does not exist** — the Gradle manifest block wins.

**Inbound:**
- gRPC on **hardcoded port 50051** (`GrpcServer.java:23`), exactly **one** service registered:
  `MonitoringService` with **one rpc** — `reInitiateUpdateWfStatusJob(Empty) → Empty`
  (`monitor.proto:43-45`, impl `MonitoringServiceImpl.java:315-322`). Called by workflow-ops.
  No health service, no reflection service.
- Kafka Streams × 3 (all via `InboundKafkaStreams.listenStream`): credential events on
  `ccsMonitorConfig.credentialEventsTopic` with hardcoded `application.id`
  `"update-credential-status"` (`CCSCredentialMonitor.java:110`); all platform event topics,
  topic list fetched over gRPC from DMS `getEventsTopic` (`MonitoringServiceImpl.java:149`);
  the task-manager response topic (`:269-272`).
- AKST virtual queue **pull** (not a listener) on hardcoded `"monitoring_service_vq"`
  (`MonitoringServiceImpl.java:200`); only `fbName == "HEALTH"` is accepted, anything else
  throws (`:207-213`).
- **No REST endpoints at all.**

**Outbound:**
- gRPC → DMS: **15 rpcs reachable through `MonitoringClientImpl`, 3 of which are the
  credential contract**: `addAssignedCredentialToStore` (`MonitoringClientImpl.java:245`),
  `updateReleaseStatusToStore` (`:252`), `fetchActiveCredentialsFromStore` (`:259`).
  The other 12 (`getWFRunListForDeploymentID`, `updateWfRunStatus`, `getKafkaDetails`,
  `getConfigValue`, `performTask`, …) are **inherited fork baggage** from monitoring-service and
  carry no credential meaning. `performTask` (`:187`) sets `.setDeploymentId(null)` → would NPE,
  but `addTaskToDB` has no callers.
- gRPC → workflow-ops `getWorkflowComponents` (`:133,137,211`); event-store `sendEvent` (`:116`);
  AKST `isTaskAvailable` (`:120`), `getTask` (`:125`)
- gRPC → Temporal `grpc.health.v1.Health/Check` and `WorkflowService/DescribeNamespace` over an
  ad-hoc channel (`HealthMonitor.java:282,310,333`)
- Kafka produce → `taskManager.topics.command` (`TaskHandler.java:50,109`); source topic
  hardcoded `"e5-platform-ss-interface-inbound"` (`SourceDestinationResolver.java:15`)
- Kubernetes API → `CoreV1Api.listNamespacedEventCall(..., fieldSelector="involvedObject.kind=Pod",
  watch=true)` wrapped in `Watch.createWatch` (`KubernetesPodsMonitor.java:265-284`), **SSL
  verification disabled** (`:85`)
- AWS SQS → `sendMessage` to `ccsMonitorConfig.sqsQueueName` (`:243`)
- JDBC → a **read-only liveness probe against someone else's Postgres**
  (`HealthMonitor.java:368-393`); credentials come from DMS `getConfigValue(…,["dbconfig"])`,
  not from this service's config
- HTTP → `GET http://<podIp>:<port>/health` via raw `HttpURLConnection` with **no timeouts**
  (`HealthMonitor.java:58-85`); the only caller is commented out (`MonitoringServiceImpl.java:250-251`)

**Data owned:** **none.** No `@Entity`, no `@Table`, no JPA/Hibernate, no repositories, no
Flyway, no SQL migrations. All credential-assignment state is
[`credential_assignment_registry`](../tables/credential_assignment_registry.md) in DMS, reached
only through the three rpcs above.

**Config keys:** no `application.yml`. Config is **four JSON/YAML blobs injected as environment
variables** and read via `java-dotenv`, each validated against a JSON Schema
(`ConfigsLoader.java:58-111`). Top-level names: `kafka`, `monitor`, `taskManager`,
`ccsMonitorConfig`, plus `POD_HOST_IP` (injected from `status.hostIP`, **read by nothing**).
Nested: `kafka.bootStrapServer`, `kafka.stream[].{streamName,groupId}`;
`monitor.rpcServers[].{serviceType,ecrServiceName,servicePort}`;
`monitor.jobs.timedScheduler[].{name,delayInMinutes,intervalInMinutes,enabled}`;
`monitor.jobs.oneTimeScheduler[].{name,delayInMinutes,enabled}`;
`taskManager.topics.{command,response}`; `taskManager.queues[].{subSystemName,queueName}`
(**loaded, validated, never read**);
`ccsMonitorConfig.{kubeEndpoint,kubeApiToken,kubeNamespace,credentialEventsTopic,sqsQueueName}`.
`kubeEndpoint`/`kubeApiToken` are declared `transient` (`CCSCredentialMonitorConfig.java:18-21`)
so they are excluded from config serialization — deliberate secret hygiene.
**Blank / placeholder in committed deployment-configs:** `kubeEndpoint`, `kubeApiToken`,
`kubeNamespace` are `""` in dev/qa/prod; `sqsQueueName` is `""` in prod;
`credentialEventsTopic` is `[""]` in prod; the whole `kafka`/`monitor`/`taskManager` blocks are
`"value"`/`"TEST"`/`123` placeholders. Because the schemas set `minLength: 1`, **the committed
prod config would fail schema validation as-is** — real values must come from the pipeline.
`gradle.properties` commits blank `ARTIFACTORY_CREDS_USR`/`ARTIFACTORY_CREDS_PSW` and is
`.gitignore`d yet tracked. Values are never recorded here.
⚠ `ConfigsLoader.java:105` logs the full parsed config at INFO, and `LoggerUtil.logError` is
called with the Postgres config object (`HealthMonitor.java:390`) and the Temporal config map
(`:303`) — **DB credentials and tokens can reach stdout**.

**ETP role:** Task Worker, and only for one task type: it pulls from `monitoring_service_vq` and
accepts only `fbName == "HEALTH"`. It is never Initiator, Router, Queue or Reader.

**The credential algorithm — the thing to understand first.** There is **no lease TTL anywhere**.
Staleness is decided purely by comparing timestamps:

1. Watch `CoreV1Event` in `ccsMonitorConfig.kubeNamespace`, filtered to Pod-involved events,
   resuming from `lastResourceVersion` (`KubernetesPodsMonitor.java:117,265-284`).
2. Eligibility (`:205-209`): pod name **contains `"fb-worker"`** AND `event.reason` ∈
   {`BackOff`, `Killing`, `SuccessfulDelete`, `Pulled`, `Pulling`}
   (`KubeMonitorEventsEnum.java:4-8`). `Pulled`/`Pulling` fire on **(re)start**, which is the
   intended "the pod came back, its predecessor's locks are orphaned" signal.
3. `eventTime = event.getLastTimestamp()` (`:178`, `Objects.requireNonNull` → NPE if absent).
4. Ask DMS `fetchActiveCredentialsFromStore(podName)` (`:219`).
5. **Staleness rule** (`:217-225`): keep every assignment whose
   `Instant.parse(fetchedTime).isBefore(eventTime)` and collect its `lockId`.
6. Send **one** SQS message `{"type":"POD_UNHEALTHY","lockId":"<id1,id2,…>"}` (`:187,306-313`).
7. *Then* record intent in DMS via `updateReleaseStatusToStore(..., RELEASE_REQUESTED)` (`:188-192`).

Sharp edges: the comma-joined lock list is written into the **single** `lock_id` field, so
`"a,b,c"` lands in one column; SQS fires **before** the DMS write, so a DMS failure leaves an
untracked release; there is no idempotency key and `Pulled`/`Pulling` repeat, so duplicate
release requests are expected and must be tolerated downstream.

**Failure modes:**
- **Silent death of the reclamation loop.** After 3 failed watch reconnects (5 min apart for
  `ApiException`, 3 min for anything else) the loop throws `PlatformMonitorException`
  (`KubernetesPodsMonitor.java:148,151-159`), the exception escapes the `Runnable`
  (`SchedulerTasks.java:84`), `ScheduledExecutorService` swallows it, **nothing restarts the
  watch, and there is no probe to notice** — see next bullet. HTTP 410 Gone is handled by
  resetting `resourceVersion` and reconnecting immediately (`:140-143`).
- **No health check exists.** No HTTP endpoint, no gRPC health service, and the shared Helm
  chart defines **no `livenessProbe` and no `readinessProbe`**. A dead credential loop in a
  live pod is invisible.
- **Missing job name ⇒ NPE at startup.** Jobs are wired unconditionally from
  `monitor.jobs.*` maps (`GrpcServer.java:49,53,57,61,65,70-71`); a name present in code but
  absent from config NPEs before `awaitTermination()`. The `enabled` flag is only checked
  *inside* the Runnable, at execution time.
- **Credential events are dropped on error, with no retry and no DLQ**
  (`CCSCredentialMonitor.java:100-106`); deserialization poison pills are dropped by
  `LogAndContinueExceptionHandler` (`InboundKafkaStreams.java:44`); `auto.offset.reset=latest`
  is hardcoded with a `// todo : config` (`:49`), so events arriving while the pod is down are
  **permanently lost**.
- **No gRPC deadlines or retry policy anywhere** (`ChannelFactory.java:31-44`) — a hung DMS
  blocks the calling job thread forever.
- `getAllEventsTopic` swallows the gRPC error then dereferences the null result → NPE
  (`MonitoringClientImpl.java:111`).
- `MonitoringClientImpl.getInstance()` is a **non-thread-safe lazy singleton** (`:41-46`) called
  from multiple scheduler threads.
- **Metrics: none.** No Micrometer/Prometheus/StatsD dependency. Observability is log-only, and
  every log line is emitted through a single logger named `LoggerUtil`, so **no line carries its
  originating class** (`LoggerUtil.java:9`).
- **Every `@Test` in the repo is commented out** (`HealthTest.java:43,51,57`,
  `MonitorServiceImplTest.java:48`). Effective coverage: zero.

**Code-level landmarks:**
- `service/monitors/KubernetesPodsMonitor.java:111-225` — the entire reclamation algorithm
- `service/monitors/CCSCredentialMonitor.java:87-112` — the ledger-mirroring stream
- `client/MonitoringClientImpl.java:225-259` — the three DMS credential rpcs
- `grpc/GrpcServer.java:34-73` — job wiring; the only place `enabled`/`delayInMinutes` are consumed
- `service/scheduler/ScheduleMonitor.java:12-24` — the 20-thread `ScheduledExecutorService`
- `config/ConfigsLoader.java:58-111` — env-var → JSON-schema → POJO config loading

**Known divergences:**
credential-monitor-is-an-unpruned-monitoring-service-fork (archived) ·
credential-monitor-has-no-health-probe (archived) ·
dms-proto-forks-disagree-on-service-identity (archived)

**Relevant runbooks:** none yet.

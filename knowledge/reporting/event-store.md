---
type: subsystem
name: Event Store
repo: e5-platform-ss-event-store
repo_path: platform-whole-context/subsystem-workflow-interaction-support/event-strore
branch: release/v1.1.4
commit: cac7bb6
language: java
build: gradle
deployment: event-store
namespace_pattern: platform-subsystem-{env}
etp_roles: []
owns_schema: false
ports: [50051]
health: none
calls: []
called_by: [monitoring-service, credential-monitor, eventstore-client-sdk]
provenance: code
verified: 2026-10-02
deployed_in_qa: v1.1.3
deployment_lag: release/v1.1.4 available, v1.1.3 running
prose_reviewed_at_commit: 82b8e81
---

# Event Store

A **stateless protobuf→CloudEvents translator** — despite the name it stores nothing; there is no database, no entity and no Kafka state store in the repo.

**Responsibility (what it owns):** Converting a `PlatformEvent` protobuf into a flat CloudEvents v1.0 JSON envelope (`specversion/type/source/id/time/datacontenttype/data`) and publishing it to one outbound Kafka topic. It mints the CloudEvent `id` (`PlatformEventServiceImpl.java:51`) and `time` (`:52`), and strips the protobuf `Any` `@type` discriminator (`PlatformEventStoreImpl.java:56-58`).

**Not its responsibility:** persistence/replay of events (no table, no state store), webhook subscription lookup, delivery, retry, or ordering. It does not fan out to multiple topics — the "fan-out" is a single `to(outboundTopic)` (`KafkaStreamService.java:57`).

**Entry points:** `E5PlatformSsEventStoreApplication.java:9` (Spring Boot main). Two independent ingress paths: gRPC `PlatformEventStoreImpl.java:44` and a Kafka Streams topology started from `@PostConstruct` in `KafkaStreamService.java:39-60`.

**Inbound:** gRPC `com.e5.platform.ss.proto.EventStoreService/sendEvent` on port `GRPC_SERVER_PORT` → `grpc.server.port` (`event_store.proto:47`, impl `PlatformEventStoreImpl.java:44`). Kafka Streams source topic `EVENT_STORE_INBOUND_TOPIC` → `kafka.consumer.event-topic` (`KafkaStreamService.java:27-28,43`).

**Outbound:** Kafka topic `EVENT_STORE_OUTBOUND_TOPIC` → `kafka.producer.event-topic`, written twice: by `KafkaProducerService.sendEvent` for the gRPC path (`KafkaProducerService.java:18,34`) and by the Streams sink for the topic path (`KafkaStreamService.java:30-31,57`). No gRPC clients, no VQs. See [platform-cloudevent-v1](../contracts/platform-cloudevent-v1.md).

**Data owned:** none — the service is stateless; downstream state lives in [event-processor](event-processor.md) + deployment-mgmt.

**Config keys:** `grpc.server.port`, `kafka.bootstrap-servers`, `kafka.key-serializer`, `kafka.value-serializer`, `kafka.producer.event-topic`, `kafka.consumer.event-topic`, `kafka.stream.application-id`, `kafka.stream.auto-offset-reset` (`application.yaml:1-18`). **Env-injected and blank in every committed config** (`deployment-configs/*-config.yaml:13-18`): `EVENT_STORE_OUTBOUND_TOPIC`, `EVENT_STORE_INBOUND_TOPIC`, `KAFKA_BOOTSTRAP_SERVERS`. `kafka.bootstrap-servers` has no fallback default, so an unset value fails bean creation in `KafkaConfig.java:15`.

**ETP role:** none — it is event transport, not a transport-task-protocol participant. It plays no task-initiator/router/queue/worker/reader role in any deploy mode.

**Failure modes:**
- **gRPC errors are swallowed.** Any exception in `sendEvent` is logged and `onCompleted()` is still called without `onNext` or `onError` (`PlatformEventStoreImpl.java:65-68`) → the caller sees a unary call that returned no message, never the cause. See [../../disagreements/event-store-grpc-swallows-errors.md](../known-issues/event-store-grpc-swallows-errors.md).
- **Stream records are dropped silently.** Mapper returns `null` on any exception and the `filter` discards it (`KafkaStreamService.java:52-56`); malformed records are also dropped by `LogAndContinueExceptionHandler` (`KafkaStreamConfig.java:37`). No DLQ anywhere in the repo.
- **Producer sends are fire-and-forget** — `kafkaTemplate.send` with no callback (`KafkaProducerService.java:34`), so broker rejection is invisible.
- **No health surface:** no actuator dependency (`build.gradle:36-60`), no `HealthIndicator`, and no probe stanza in any `deployment-configs/*.yaml` — liveness cannot distinguish "running" from "Streams thread dead".
- **Non-idempotent ids:** a fresh `UUID.randomUUID()` per conversion (`PlatformEventServiceImpl.java:51`) means a retried `sendEvent` produces a *new* CloudEvent id, defeating downstream dedup.

**Code-level landmarks:** `PlatformEventStoreImpl.java:44` (gRPC entry), `PlatformEventServiceImpl.java:38-56` (CloudEvent construction), `KafkaStreamService.java:43-57` (topology), `KafkaStreamConfig.java:29-39` (Streams props), `event_store.proto:16-48` (wire contract).

**Known divergences:** [event-store-grpc-swallows-errors](../known-issues/event-store-grpc-swallows-errors.md), event-store-msk-iam-comment-vs-code (archived), event-store-prod-uses-dev-stream-id (archived). `build.gradle:10` declares `version = '1.1.4'` on `release/v1.1.4`.

**Relevant runbooks:** none exist. Operational handle is the Streams `application.id` consumer group (`kafka.stream.application-id`) — reset it to replay the inbound topic.

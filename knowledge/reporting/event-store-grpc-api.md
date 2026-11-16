---
type: api
name: EventStoreService (gRPC)
protocol: grpc
owner: event-store
repo_path: platform-whole-context/subsystem-workflow-interaction-support/event-strore
proto: src/main/proto/event_store.proto
package: com.e5.platform.ss.proto
java_package: e5.platform.ss.event.store.proto
port: 50051
auth: none (plaintext, no interceptor)
provenance: code
verified: 2026-10-02
---

# event-store gRPC API

One service, one RPC. Owned by [event-store](event-store.md); the generated stub is also republished through `e5-eventstore-client-sdk`.

## Operations

### Event ingestion
| RPC | Request → Response | Touches |
|---|---|---|
| `EventStoreService/sendEvent` (`event_store.proto:47`, impl `PlatformEventStoreImpl.java:44`) | `PlatformEvent` → `PlatformEvent` (echoes the request unchanged, `:64`) | Kafka topic `kafka.producer.event-topic` (`EVENT_STORE_OUTBOUND_TOPIC`) via `KafkaProducerService.java:34`. No table. |

There are **no** read, query, list or delete RPCs — the API is write-only and the response carries no server-assigned id. The CloudEvent `id` that downstream consumers key on is generated server-side (`PlatformEventServiceImpl.java:51`) and is **not returned to the caller**.

## Request shape
`PlatformEvent` (`event_store.proto:16-23`): `eventtype`, `source`, `timestamp`, `payload` (`google.protobuf.Any`), `metadata` (`MetaData{Struct}`). Only `eventtype` and `source` are mandatory in practice — both are dereferenced without a presence check (`PlatformEventServiceImpl.java:49-50`). `payload` is typically a packed `PlatformData` (`:25-35`) or `Struct`; the `TypeRegistry` at `PlatformEventStoreImpl.java:77-83` only knows `PlatformEvent`, `PlatformData`, `Schedule`, `MetaData`, `Struct` — any other packed type throws and the call returns empty.

## Error semantics — read this before integrating
`sendEvent` **never calls `onError`**. Every exception is logged and `onCompleted()` is invoked without `onNext` (`PlatformEventStoreImpl.java:65-68`), so a blocking stub sees a unary call that completed with no value rather than a status describing the failure. Treat a missing response message as "event rejected, cause only in server logs". See [../../disagreements/event-store-grpc-swallows-errors.md](../known-issues/event-store-grpc-swallows-errors.md).

## Transport
`net.devh:grpc-server-spring-boot-starter` (`build.gradle:50`), no TLS config, no auth interceptor, no deadline enforcement in the repo. Port from `grpc.server.port` ← `GRPC_SERVER_PORT` (default 50051, `application.yaml:4-6`); k8s Service is ClusterIP 50051→50051.

Contract detail: [event-store-service-proto](../contracts/event-store-service-proto.md). Output envelope: [platform-cloudevent-v1](../contracts/platform-cloudevent-v1.md).

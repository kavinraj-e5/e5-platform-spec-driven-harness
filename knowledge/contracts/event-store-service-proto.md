---
type: contract
name: EventStoreService / PlatformEvent (proto3)
kind: grpc-proto
owner: event-store
source: platform-whole-context/subsystem-workflow-interaction-support/event-strore/src/main/proto/event_store.proto
proto_package: com.e5.platform.ss.proto
java_package: e5.platform.ss.event.store.proto
producers: [monitoring-service, credential-monitor, eventstore-client-sdk]
consumers: [event-store]
provenance: code
verified: 2026-10-02
---

# EventStoreService / PlatformEvent

The protobuf front door of [event-store](../reporting/event-store.md). One RPC, one message family, no versioning marker — breaking changes are only detectable by field number.

## Service
`service EventStoreService { rpc sendEvent(PlatformEvent) returns (PlatformEvent); }` (`event_store.proto:46-48`). Server impl: [event-store-grpc-api](../reporting/event-store-grpc-api.md).

## Messages
| Message | Fields (number: name) | Notes |
|---|---|---|
| `PlatformEvent` (`:16`) | 1 `eventtype` · 2 `source` · 3 `timestamp` (`Timestamp`) · 4 `payload` (`Any`) · 5 `metadata` (`MetaData`) | `eventtype` becomes the CloudEvent `type`; `source` becomes `source`. Field 3 `timestamp` is **ignored** — the CloudEvent `time` is re-stamped from `Instant.now()` (`PlatformEventServiceImpl.java:52`) |
| `PlatformData` (`:25`) | 1 `deploymentVersionId` · 2 `workflowRunId` · 3 `invocationType` · 4 `scheduledStartTime` · 5 `timestamp` · 6 `inputPayload` · 7 `reason` · 8 `schedule` · 9 `workflowTriggerInfo` (`Struct`) | the usual `payload` packed type; `deploymentVersionId` is what [event-processor](../reporting/event-processor.md) later keys subscription lookup on |
| `Schedule` (`:37`) | 1 `scheduleId` · 2 `scheduleName` | — |
| `MetaData` (`:42`) | 1 `metadata` (`Struct`) | **dropped on the floor** — nothing in event-store copies `metadata` into the CloudEvent (`PlatformEventServiceImpl.java:38-56`) |
| `WorkflowInvocationType` enum (`:11`) | `TIME_BASED=0`, `MANUAL=1` | — |

## Compatibility rules this contract actually enforces
- `payload` must be an `Any` packing one of `PlatformEvent`, `PlatformData`, `Schedule`, `MetaData`, `Struct` — the `TypeRegistry` at `PlatformEventStoreImpl.java:77-83` knows no others, and an unknown type makes `JsonFormat.printer().print()` throw, which the handler swallows (the event is dropped, the caller sees an empty response).
- `eventtype` and `source` must be present and non-null; they are read with `jsonNode.get(...).asText()` with no presence check (`PlatformEventServiceImpl.java:49-50`), so omitting either NPEs and drops the event.
- The `@type` discriminator inside the serialised `payload` is stripped before conversion (`PlatformEventStoreImpl.java:56-58`), so consumers must not rely on it.
- The response is the **unmodified request** (`:64`); it carries no id, no offset and no acknowledgement semantics.

## Downstream shape
Each accepted `PlatformEvent` becomes exactly one record on `EVENT_STORE_OUTBOUND_TOPIC` in the [platform-cloudevent-v1](platform-cloudevent-v1.md) envelope. Mapping: `eventtype→type`, `source→source`, `payload` (or `payload.value` when the `Any` wraps a `Struct`) `→data`, plus a freshly minted `id` and `time`.

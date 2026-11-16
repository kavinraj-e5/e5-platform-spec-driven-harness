---
type: disagreement
severity: high
subsystem: event-store
sources:
  - code: event-strore/src/main/java/e5/platform/ss/event/store/service/impl/PlatformEventStoreImpl.java:43-69
  - doc: same file, Javadoc at :32-42 ("notifies the response observer with the original request")
  - expectation: gRPC unary contract — a failed call terminates with onError(Status)
verified: 2026-10-02
---

# event-store `sendEvent` reports success-shaped failure

**The Javadoc claims** the method "processes the incoming PlatformEvent request … sends the cloud event to the Kafka producer service and notifies the response observer with the original request" — i.e. the response means the event was accepted.

**The gRPC contract expects** any failure to terminate the call with `responseObserver.onError(...)` carrying a `Status`.

**The code proves** neither holds. `PlatformEventStoreImpl.java:65-67` catches `Exception`, logs `"Error processing event data: {}"` with only `getMessage()` (no stack trace), and then falls through to `responseObserver.onCompleted()` at `:68` — which runs on **both** paths. `onNext(request)` at `:64` is inside the `try`, so on failure the caller gets a completed unary call with **zero messages** and no status description.

Everything in the try block can fail this way: `JsonFormat` printing an `Any` whose type is absent from the `TypeRegistry` (`:76-90`), `mapper.readTree` on malformed JSON, and the unchecked NPE in `PlatformEventServiceImpl.java:49-50` when `eventtype` or `source` is missing. The Kafka send at `:63` is itself fire-and-forget (`KafkaProducerService.java:34`), so a broker failure is not even observed inside the try.

**Consequence.** Publishers (`e5-monitoring-service`, `e5-platform-ss-credential-monitor`, anything using `e5-eventstore-client-sdk`) cannot distinguish "event published" from "event dropped". A blocking stub surfaces a generic io.grpc internal error about a missing value rather than the real cause, which is only in event-store's pod logs; an async stub sees `onCompleted()` with nothing delivered and will most likely treat it as success. Combined with the absence of any DLQ and the `LogAndContinueExceptionHandler` on the Streams path (`KafkaStreamConfig.java:37`), **every event-store rejection is silent data loss**.

**Fix shape:** wrap the body so failures call `responseObserver.onError(Status.INVALID_ARGUMENT/INTERNAL.withDescription(...).asRuntimeException())`, move `onCompleted()` into the success branch, and attach a `SendResult` callback to the Kafka send so broker rejection also fails the RPC.

Referenced from [event-store](../reporting/event-store.md) and [event-store-grpc-api](../reporting/event-store-grpc-api.md).

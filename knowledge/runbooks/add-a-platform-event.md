---
type: runbook
name: Add or change a platform event
applies_to: [event-store, event-processor, deployment-mgmt, workflow-ops, monitoring-service]
triggers: ["emit a new event", "add a field to an event", "e5.platform.event.*"]
provenance: code
verified: 2026-10-03
---

# Add or change a platform event

**The pipeline is fixed; membership is structural, not textual.** event-store and
event-processor handle `PlatformEvent` generically and never mention a specific event name — so
grepping for your event name will not find them, and they still change.

```
producer --gRPC sendEvent--> event-store --Kafka--> event-processor
                                                        |
                        deployment-mgmt (subscription lookup) <--+
                                                        |
                                    webhook-dispatcher (AWS Lambda) --HTTP--> customer
```

**This is NOT the webhook-service chain.** webhook-service serves `workflow-api` and requires
CloudEvents with mandatory `destinationid` and `idempotencykey`. Sending it a platform event
payload is rejected at the first parse. See
two-webhook-chains-not-one (archived).

## Steps

1. **Change the proto**, and change **every vendored copy**. `PlatformEvent` / `PlatformData` are
   declared in several repos; `buf breaking --against '.git#branch=release/vX.Y.Z'` per repo.
2. **Producer** emits via its platform event client.
3. **event-store** — ingestion and forwarding. It is a *forwarder*: no database, no entity, no
   state store, despite the name.
4. **event-processor** — subscription matching and dispatch.
5. **deployment-mgmt** — webhook subscription lookup, if the event is subscribable.
6. Declare under `contracts_added` in the release document.

## Traps

- **event-store's `sendEvent` never calls `onError`.** It catches, logs the message only, and
  still calls `onCompleted()`. Combined with `LogAndContinueExceptionHandler` and no DLQ, **every
  rejection is silent data loss** — invisible to monitoring and to the client SDK. Validate
  before you send; you will get no signal back.
- **Prod may use the dev Kafka Streams application id**, which is the consumer group id.
- Run `e5 impact "<your event> field"` — the vendored-proto rule finds repos that never name
  your event. The golden test for this exact scenario reaches 100% recall.

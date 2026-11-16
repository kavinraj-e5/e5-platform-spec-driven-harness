---
type: contract
name: Platform CloudEvent v1.0 (event-store outbound)
kind: kafka-event-envelope
owner: event-store
producers: [event-store]
consumers: [event-processor]
serialization: JSON string key + JSON string value (StringSerializer both sides)
provenance: code
verified: 2026-10-02
---

# Platform CloudEvent v1.0

The envelope [event-store](../reporting/event-store.md) puts on the wire and [event-processor](../reporting/event-processor.md) consumes. It is a **hand-rolled** CloudEvent — no CloudEvents SDK, no Kafka `ce_*` binary headers, no extension attributes. Do not confuse it with the stricter envelope on the webhook-request topic ([webhook-request message](webhook-request-message.md)) or the audit-event topic ([audit-event message](audit-event-message.md)).

## Envelope (`CloudEvent.java:16-37`, built at `PlatformEventServiceImpl.java:47-55`)
| JSON field | Value | Source |
|---|---|---|
| `specversion` | always `"1.0"` | hardcoded (`:48`) |
| `type` | the proto `eventtype` | caller-supplied |
| `source` | the proto `source` | caller-supplied |
| `id` | `UUID.randomUUID()` | **minted per conversion** (`:51`) — never derived from the input, so a client retry yields a new id |
| `time` | `Instant.now()` | re-stamped, the proto `timestamp` is discarded (`:52`) |
| `datacontenttype` | always `"application/json"` | hardcoded (`:53`) |
| `data` | the unpacked `payload`, or `payload.value` when the `Any` wraps a `Struct` | `:39-45` |

There is **no `destinationid`**, **no `idempotencykey`**, no `subject` and no `traceparent`. Serialisation is plain Jackson over the POJO, so absent values appear as `null` rather than being omitted.

## Message key
None. Both the gRPC path (`KafkaProducerService.java:34`, `send(topic, message)`) and the Streams path (`KafkaStreamService.java:57`, key passed through from the source record) leave the key null in practice, so **there is no partition affinity and no per-entity ordering guarantee**.

## Consumer-side expectations
`event-processor` reads the envelope with `path()` lookups, so a missing field degrades to `""` rather than failing: `id` → `eventId` (`EventProcessorServiceImpl.java:52`), `type` → subscription lookup key (`:52`), `data.deploymentVersionId` (`:57`), else `data.{workflowName,variantName,tenantName}` (`:58-60`). `EventRequestDTO.java:15` is `@JsonIgnoreProperties(ignoreUnknown = true)`, so adding fields is backward compatible; renaming `type` or `data.deploymentVersionId` is not.

## Dual-write hazard
The same topic is written by two code paths with the **same** envelope but different failure behaviour: the gRPC path (fire-and-forget `KafkaTemplate`) and the Kafka Streams path (`EVENT_STORE_INBOUND_TOPIC → EVENT_STORE_OUTBOUND_TOPIC`, at-least-once with `LogAndContinueExceptionHandler`). Because `id` is minted at conversion time, replaying the inbound topic produces **new** ids for the same business event — downstream dedup on `id` cannot work.

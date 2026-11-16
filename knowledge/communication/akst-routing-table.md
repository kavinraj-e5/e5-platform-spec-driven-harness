---
type: config
name: akstService.routingTable (Workflow Router)
repo: e5-platform-core
repo_path: platform-whole-context/platform-workflow-building/platform-core
branch: release/v1.10.1
commit: 377cd53d
pojo: src/main/java/com/e5/platform/core/config/routing/RoutingTableConfig.java
schema: src/main/schemas/AkstConfig.schema.json
consumed_by: [AKST_SERVICE mode]
provenance: code
verified: 2026-10-04
prose_reviewed_at_commit: a5f3c031
---

# `akstService.routingTable` — the Workflow Router config

The block that turns an AKST deployment into a **Workflow Router**. Optional: when it is absent,
null, or `routingEnabled` is not exactly `true`, AKST behaves exactly as it did before routing
existed. Key names only — values live in `e5-platform-workflow-config`.

Built once at boot by `RoutingTableFactory.init()`, called from `startAkstService()` before any
listener starts, deliberately so that a bad routing config fails the process rather than the first
message (`E5WorkflowEngine.java:545-547`).

## Parent block — `akstService`

`AkstConfig.java:13-20`, schema `AkstConfig.schema.json`.
`required: ["akstCmdQEndpoint","akstServicePort","workflowEndpoint","metricsEndpoint","inMemoryTaskStoreEnabled"]`.

| Key | Type | Notes |
|---|---|---|
| `akstCmdQEndpoint` | string, minLength 1 | the Kafka command topic AKST listens on (`TaskActionImpl.java:52`) |
| `akstServicePort` | **string**, minLength 1 | gRPC port, parsed with `Integer.parseInt` at startup |
| `workflowEndpoint` | string, minLength 1 | default reply topic for every AKST response |
| `metricsEndpoint` | string, minLength 1 | Kafka topic for `TaskMetrics` events (`MetricEventEmitter.java:12`) |
| `inMemoryTaskStoreEnabled` | boolean | gates the Caffeine store **and** the `peekTask` RPC |
| `routingTable` | object \| null | this card |

Unlike `WorkerConfig`, `AkstConfig.schema.json` **is** enforced at runtime:
`TaskActionImpl.validateInputs` validates it plus `KafkaConfig.schema.json` and throws
`CONFIG_LOAD_CONFIGS_FAILED` (`TaskActionImpl.java:29-37`).

## `routingTable` keys

| Key | Type | Required | Notes |
|---|---|---|---|
| `routingEnabled` | boolean | — | **must be exactly `true`**; null or false ⇒ `NoOpRoutingTable` (`RoutingTableConfig.isRoutingEnabled`, `:53-55`) |
| `source` | string | required when enabled | **schema enum is `["config"]` and nothing else** (`AkstConfig.schema.json:31-34`) |
| `namespace` | string, minLength 1 | required when enabled | this router's **home namespace** |
| `virtualQueues[]` | array of `{ vqName }` | required when enabled | the VQs this AKST owns; `vqName` is the item's only declared property and is `required` |
| `forwardOnMiss` | `{ endpointID?, type, config }` | required when enabled | next hop for destinations this AKST does not own. The **topic is read from `forwardOnMiss.config.topic`** (`ConfigRoutingTable.java:32-33`) — and `config` is typed only as `"object"`, so a `forwardOnMiss` with no `topic` passes the schema and degrades to route-failure. |

The conditional is expressed as JSON-Schema `if/then`: when `routingEnabled` is `const: true`, then
`source`, `namespace`, `virtualQueues` and `forwardOnMiss` all become required
(`AkstConfig.schema.json:69-79`).

## `source` — only `config` is implemented

```java
if (!RoutingTableConfig.SOURCE_CONFIG.equals(source)) {
    return failFast("Unsupported routingTable.source: " + source
            + ". Only '" + RoutingTableConfig.SOURCE_CONFIG + "' is supported.");
}
```
`RoutingTableFactory.java:56-59` — `failFast` logs `AKST_ROUTING_CONFIG_INVALID` and throws
`E5Exception`, so the process dies at boot. `SOURCE_CONFIG = "config"`
(`RoutingTableConfig.java:26`).

The field's own Javadoc says it "is retained for forward compatibility with the `subsystem` source
in the technical specification" (`RoutingTableConfig.java:31-33`), and the specification documents
`source: config | subsystem` plus a `SubsystemRoutingTable` class. **Neither the enum value nor the
class exists.** See
routing-table-source-subsystem-does-not-exist (archived).

Implementations that do exist: `ConfigRoutingTable` (`source: config`) and `NoOpRoutingTable`
(routing disabled). That is all — `RoutingTableFactory.build` has exactly those two outcomes plus
fail-fast.

## `virtualQueues[]` carries no namespace

`VirtualQueueEntry` has **one** field, `vqName`, and its Javadoc is explicit: "Deliberately carries
no namespace: an Akst deployment serves a single namespace" (`VirtualQueueEntry.java:8-20`). The
technical specification's example writes `akstNamespace` on each entry
(`docs/WorkflowRouter_Technical_Specification.md:129-130`); the schema does not set
`additionalProperties: false` on the item, so that key is parsed and **silently discarded**.

## Resolution order (what the config actually does)

`ConfigRoutingTable.resolveByDestination` (`ConfigRoutingTable.java:94-115`), used for both inbound
commands and outbound responses:

1. `metaInfo.routingPath.routingEndpoints.destination.config.vqName` ∈ `virtualQueues` ⇒
   **LOCAL_ACCEPT** (responses additionally get `workflowEndpoint` as the target topic).
2. destination `type == kafka` ⇒ compare `destination.config.namespace`, falling back to
   `taskInfo.namespace`, against `routingTable.namespace`. Match ⇒ LOCAL_ACCEPT on
   `workflowEndpoint`; no match ⇒ step 4.
3. No destination endpoint at all ⇒ **only `TaskComplete`** is rescued, by matching
   `taskInfo.namespace` against the home namespace (`:140-157`). Any other command ⇒ step 4.
4. `forwardOnMiss.config.topic` present ⇒ **FORWARD** verbatim; absent ⇒ **ROUTE_FAILED**
   (`forwardOrFail`, `:185-190`).

`RoutingDecision.Type` has exactly three values — `LOCAL_ACCEPT`, `FORWARD`, `ROUTE_FAILED`
(`RoutingDecision.java:8-15`). `ROUTE_FAILED` is not silent: it publishes
`Command.TaskRouteNotFound` back on the **source** endpoint's topic
(`TaskActionHandler.java:107-109` → `RouteFailureNotifier.java:59-83`), which the specification says
the local router must never do. See
local-router-emits-the-failure-the-spec-forbids (archived).

Observability: every decision increments `akst_routing_decisions_total` with a `decision` label of
`local` / `forward` / `route_failed`, plus `akst_routing_forward_total`,
`akst_routing_route_failed_total`, and the `akst_routing_latency_ms` histogram
(`metrics/RoutingMetrics.java:29-71`).

## See also

- [worker-config](worker-config.md)
- [akst subsystem card](akst.md)

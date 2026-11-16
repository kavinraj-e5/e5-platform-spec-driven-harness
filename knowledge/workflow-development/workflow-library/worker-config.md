---
type: config
name: WorkerConfig (the `worker:` block)
repo: e5-platform-core
repo_path: platform-whole-context/platform-workflow-building/platform-core
branch: release/v1.10.1
commit: 377cd53d
pojo: src/main/java/com/e5/platform/core/config/WorkerConfig.java
schema: src/main/schemas/WorkerConfig.schema.json
consumed_by: [WF_WORKER, WF_ENGINE, RPA_GATEWAY modes]
provenance: code
verified: 2026-10-04
prose_reviewed_at_commit: a5f3c031
---

# `worker:` — WorkerConfig

The deployment-config block that decides **whether a workflow delegates through AKST or through the
legacy RPA Gateway**, and which virtual queue each function block lands on. Values live in
`e5-platform-workflow-config` and are never recorded here — key names only.

Loaded by `ConfigsLoader.loadWorkerConfig` (`ConfigsLoader.java:408-419`) with
`yaml.loadAs(yaml.dump(workerMap), WorkerConfig.class)` — SnakeYAML, via the no-arg constructor and
setters. **Lombok `@Builder.Default` never fires on this path**, which is the whole story behind
`taskProtocol`.

## Keys

| Key | Type | Required by schema | Notes |
|---|---|---|---|
| `taskProtocol` | `v1` \| `v2` | **yes** | the protocol switch. Java field default is `v1` (`WorkerConfig.java:27-28`) but see below. |
| `eventStreamOutboundEndpoint` | string | yes | Kafka topic for the event/aggregator pipeline |
| `functionBlockSourceEndpoint` | string | yes | delegation task endpoint |
| `gatewayEndpoint` | string | **required when `taskProtocol: v1`** | the legacy RPA Gateway topic |
| `akstEndpoint` | string | **required when `taskProtocol: v2`** | the AKST command-queue endpoint id |
| `namespace` | string | required when v2 | home namespace stamped onto `taskInfo.namespace` |
| `workerMappings[]` | array | required when v2 | `{ workerType, appName, destinationEndpoint }`; `workerType` ∈ `Robotframework` \| `UiPath` \| `Human` |
| `endpoints[]` | array | required when v2 | `{ endpointID, type, config }`; `type` ∈ `kafka` \| `http` \| `uipath_queue` \| `akst`. For `kafka`, `config` must be exactly `{topic}`; for `akst`, exactly `{vqName}` (both `additionalProperties: false`) |
| `enableAsyncExecutionthrottling` | boolean | no | note the lower-case `t` in `throttling` |
| `maxConcurrentAsyncExecutions` | integer | no | falls back to `E5Constants.RATE_LIMITER_MAX_PRODUCTION_COUNT` |
| `asyncExecutionWindowInSeconds` | integer | no | falls back to `E5Constants.RATE_LIMITER_PRODUCTION_WINDOW` |
| `interventionMappings[]` | array | no | `{ name, target, destinationEndpoint }`; duplicate `name`s are rejected at load (`ConfigsLoader.java:303-305`) |
| `interventionTargetConfig[]` | array | no | `{ name, config }`; `name` ∈ `AutoHub` \| `Neos` \| `Slack`, each with its own `$ref` schema |
| `uiActionMappings[]` | array of string | no | names of User-Initiated Actions this deployment may launch; matched against the task's `taskType` |
| `akstClientConfig` | object | no | **declared in the schema but never parsed from this key** — see below |

`additionalProperties: false` at the top level, so an unrecognised key under `worker:` would be a
validation error — if validation ran.

## `akst_client_config` — the engine's AKST poller

The nested block is read **before** `WorkerConfig` is parsed and removed from the map:
`workerMap.remove("akst_client_config")` (`ConfigsLoader.java:409`), deserialised into
`AkstClientConfig`. The YAML key is snake_case `akst_client_config`; the schema property is
camelCase `akstClientConfig` (`WorkerConfig.schema.json:36-38`). The two never meet.

`AkstClientConfig.schema.json` keys, `additionalProperties: false`,
`required: ["enabled","targetServer","vqName"]`:

| Key | Default if absent | Where |
|---|---|---|
| `enabled` | `false` | `AkstClientConfig.java:20-21` |
| `targetServer` | — | `host:port` of the AKST gRPC server |
| `vqName` | — | the virtual queue the engine pulls from |
| `filters` | empty map (`filtersOrEmpty()`) | worker-affinity map |
| `canTakeOther` | **`true`** (`canTakeOtherOrDefault()` treats null as true) | `AkstClientConfig.java:29-30`, `:50-52` |
| `pollIntervalMillis` | **30000** when null or ≤ 0 | `AkstClientConfig.java:17`, `:54-59` |
| `rpcTimeoutSeconds` | **5** when null or ≤ 0 | `AkstClientConfig.java:18`, `:61-66` |

## `taskProtocol` — the default is v1, and nothing stops you omitting it

Three facts together:

1. **SnakeYAML bypasses the Lombok default.** `@Builder.Default taskProtocol = TaskProtocol.v1`
   (`WorkerConfig.java:27-28`) only applies to builder-constructed instances. `yaml.loadAs(...)`
   leaves the field **null** when the key is absent.
2. **Every consumer tests `TaskProtocol.v2.equals(x)`**, so null falls to the v1 branch:
   `ConfigsLoader.java:296-302` (`else → updateRpaGatewayEndpoint(workerConfig.getGatewayEndpoint())`),
   `RPADelegator.java:83`, `AkstInterventionDelegator.java:75`. Two sites add an explicit
   `: TaskProtocol.v1` fallback (`E5WorkflowEngine.java:117-119`, `:224-226`).
3. **`WorkerConfig.schema.json` is not enforced on most paths.** `ConfigsLoader.validateConfigs(...)`
   — the generic routine that would apply it — is **commented out at both call sites**
   (`ConfigsLoader.java:127`, `:393`). The only production caller of
   `validateJsonData(..., "WorkerConfig.schema.json")` is `TemporalWorker.java:43`, reached in
   `WF_WORKER` mode only, and it runs *after* `ConfigsLoader` has already taken the v1 branch.

Net effect: a deployment config that omits `taskProtocol` silently routes function-block
delegations to the legacy RPA Gateway. See
task-protocol-silently-defaults-to-v1 (archived).

Related: `taskProtocol` is authored as a **top-level** key in the deployment YAML
(`WorkflowSandboxConfig-local.yaml:2`) and fanned out into the `worker:` / `engine:` / `rpaGateway:`
sections by `YamltoEnv.addTaskProtocol` (`YamltoEnv.java:60-90`) — which compares an enum to a
String and therefore never populates `engine:` or `rpaGateway:` at all. See
yamltoenv-task-protocol-fanout-is-dead-code (archived).

## See also

- [akst-routing-table](akst-routing-table.md) — the `akstService.routingTable` block
- [worker-config consumers](./platform-core.md)

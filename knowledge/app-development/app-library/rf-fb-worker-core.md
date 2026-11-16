---
type: subsystem
name: RF FB Worker Core
repo: e5-rf-fb-worker-core
repo_path: platform-whole-context/platform-workflow-app-building/e5-rf-fb-worker-core
branch: release/v1.1.14
commit: 582647f
language: python
build: poetry
deployment: "{app}-fb-worker-v1-01 — a separate image per app; scaled to 0 when idle"
namespace_pattern: "{client}-{workflow}-{variant}-{env}"
etp_roles: [task-worker]
owns_schema: false
ports: []
health: none
calls: [akst, ccs, s3, kubernetes]
called_by: []
provenance: code
verified: 2026-10-04
prose_reviewed_at_commit: e49cc7f
---

# RF FB Worker Core

The Python base library every Function Block worker app is built on: it owns the **polling loop,
the AKST pull, and the `TaskComplete` reply**, and leaves the actual RPA to Robot Framework keywords
the app supplies. Package `e5-rf-fb-worker-core` 1.1.10, Poetry, **Python pinned exactly to
`3.8.16`** (`pyproject.toml:41`).

**Responsibility (what it owns):** the worker end of the transport task protocol — fetch one task,
run it, report `TaskComplete` with a success/failure status and error list; plus pod lifecycle
(graceful-shutdown flags, Karpenter eviction protection) and OTel metrics.

**Not its responsibility:** cancellation (`"TaskCancel"` / `"CANCEL"` appear **nowhere** in the
repo), task liveness (no lease, no heartbeat in AKST mode), and concurrency — it processes exactly
one task at a time.

**Entry points:** `e5_rf_fb_worker_core/FBCore.py:647-660` — `start_fb_worker()`, which initialises
OTel metrics, clears shutdown flags, and hands control to Robot:
`robot.run("<pkg>/robots/FBInitializer.robot")`.
There is **no `console_scripts` block** (`pyproject.toml` has no `[tool.poetry.scripts]`).

## There is no base class to subclass

**The extension mechanism is Robot Framework duck-typing, not inheritance.** There is no
`BaseWorker` / `FunctionBlockWorker` — `e5_rf_fb_worker_core/api/interfaces.py:1` is entirely
commented out (`# Todo: to be implemented later`). The only ABCs in the package are OTel internals
(`integrations/otel/base.py:13,48,83,102`).

Instead, a worker app must supply these artefacts; every call site is a hard dependency:

| Required artefact | Required at |
|---|---|
| `./robots/init_fb_worker.robot` | `api/keywords.py:78` — **at module import time** |
| `initialize-fb` keyword | `FBCore.py:487` |
| `fb-pre-execution` / `fb-post-execution` keywords | `api/decorators.py:118`, `:128` |
| `teardown-start` keyword (takes `errMsg`) | `FBCore.py:402`, `api/decorators.py:145` |
| `fb-<FbName>-execute` keyword | `FBCore.py:296-300` — name built as `"fb-" + fb_name + "-execute"`, with a trailing `FB` stripped from `fb_name` |
| `./robots/functionblocks/*.robot` | `FBCore.py:462-468` |
| `./schema/configuration_schema.json` | `FBCore.py:417`, `file_utils.py:81` |
| `./yamls/`, `./resources/TaskResources.json` | `file_utils.py:139`, `:106-127` |

**Inbound:** none — it is a puller, not a server. No HTTP, no gRPC server, no port.

**Outbound:**
- **gRPC → AKST**, through the external `e5_task_sdk_python` 1.0.9 (`pyproject.toml:26`). There is
  **no `.proto` and no `_pb2.py` in this repo**. Two handlers are created at import time
  (`FBCore.py:74-75`): `taskHandler` is `KafkaTaskHandler()` or `AKSTTaskHandler(GRPC_URL)`
  depending on `TASK_QUEUE`, and `platform_task_handler` is always
  `AKSTTaskHandler(PLATFORM_GRPC_URL)`. Four SDK methods make up the whole surface:
  `isTaskAvailable` (`api/keywords.py:659`, `:513-515`), `load_task` (`:673`, `:286`),
  `getFbStateTask` (`:532-534`), `route_task` (all reply paths).
- **Kafka** — replies go to `FB_OUTBOUND_COMMAND_TOPIC` / `PLATFORM_FB_OUTBOUND_COMMAND_TOPIC`, or,
  on the Kafka path, to `metaInfo.routingPath.routingEndpoints.gateway.config.topic`
  (`FBCore.py:171-173`). Also a credential-events topic (`CREDENTIAL_EVENTS_TOPIC`).
- **HTTP/other** — CCS resource negotiator, S3, the Kubernetes API (for the eviction annotation).

**Data owned:** none. In-flight state is a set of **process-global `builtins` attributes**
(`builtins.fbName`, `taskInfoId`, `metaInfo_map`, `errors`, `version`, `asset_name`, …; reset in
`clear_global_variables`, `FBCore.py:209-220`) — which structurally forbids concurrent tasks.

**Config keys:** centralised in `e5_rf_fb_worker_core/conf/settings.py` — `TASK_QUEUE`,
`VIRTUAL_QNAME`, `FB_OUTBOUND_COMMAND_TOPIC`, `PLATFORM_FB_OUTBOUND_COMMAND_TOPIC`, `HOSTNAME`,
**`PLATFORM_VIRTUAL_QNAME`**, `METRICS_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`,
`METRICS_EXPORT_INTERVAL`, `SERVICE_NAME`, `FUNCTIONBLOCK_RELEASE_VERSION`, `APP_NAME`,
`DEPLOYMENT_ID`. Read elsewhere: `GRPC_URL`, `PLATFORM_GRPC_URL`, `VNC_PASSWD`,
`ENABLE_{INIT,EXECUTION,TEAR_DOWN}_VIDEO_RECORDING`, `COUNT_FOR_BROWSER_CONTEXT_REFRESH`,
`GRACEFUL_TERMINATION_ENABLED`, `WORKER_SHUTDOWN_{REQUEST,COMPLETE}_FLAG`,
`WORKER_PRESTOP_{MAX_WAIT,POLL_INTERVAL}_SECONDS`, `KARPENTER_EVICT_PROTECTION_ENABLED`,
`KARPENTER_DO_NOT_DISRUPT_ANNOTATION`, `EVICT_PROTECTION_METRICS_ENABLED`,
`EVICTION_PATCH_{MAX_RETRIES,RETRY_BACKOFF_SECONDS}`, `RF_LOGGING_LEVEL`, `E5_KEYWORD_TIMEOUT`,
`FTPS_CONN_TIMEOUT`, `SFTP_CONN_TIMEOUT`, `CCS_RESOURCE_NEGOTIATOR_URI`, `CCS_E5_API_ENDPOINT`,
`CCS_E5_API_ACCESS_TOKEN`, `CCS_CRED_POOL_NAME`, `CREDENTIAL_EVENTS_TOPIC`,
`KAFKA_BOOTSTRAP_SERVERS`, `AWS_{BUCKET_NAME,ACCESS_KEY,SECRET_ACCESS_KEY,REGION}`, `LOCATION_NAME`,
`CACHE_REFRESH_TIME`, `START_SYNC_JOB`, `KUBERNETES_SERVICE_{HOST,PORT}`. **No pydantic / no
`BaseSettings`.** Values are never recorded here.

**ETP role:** Task Worker, in two distinct lanes — its own business virtual queue (`VIRTUAL_QNAME`)
and the **platform control queue** (`PLATFORM_VIRTUAL_QNAME`), which carries pod scale-down and
credential-release tasks.

## Protocol details worth knowing

**There are no protocol enums in Python.** Every command, event type and worker type is a bare
string literal: `"TaskPicked"` (`FBCore.py:169`), `"TaskHeartbeat"` (`:194`), `"TaskComplete"`
(`:579`, `utils/message_utils.py:162`, …), `"COMPLETE_DELEGATION_ACTION"`, `"UNKNOWN"`,
`"Robotframework"`, `"InvokeFunctionBlock"`, `"E5_FB_SUCCESS"` / `"E5_FB_FAILED"`. The only `Enum`
classes in the package are `MetricType`, `ExecutionState` and `CloseWindowWildcard`.

**Envelope fields it reads** (`FBCore.py:240-252`): `version`, `taskInfo.taskId`,
`taskInfo.taskPayload.{appName, fbName, userDefinedIdentifier, fbPayload}`, the whole `metaInfo`
subtree, and — on the Kafka path only — `metaInfo.userMetaInfo.fbName`. Fields it **never** reads:
`taskVQ`, `vqName`, `priority`, `deadline`, `taskStatus`, `command`, `correlationId`, and even
`taskInfo.filters` (it only ever *sends* filters). The reply echoes the inbound `version` back
(`utils/message_utils.py:188`) except on the platform paths, which hardcode `"v2"`
(`FBCore.py:578`, `utils/response_utils.py:63`).

**`taskProtocol` / RPA Gateway:** `taskProtocol` → **0 hits**; `"v1"` → **0 hits**; any form of
"RPA gateway" → **0 hits**. There is no protocol negotiation and no v1 path. The only "gateway"
notion is a Kafka topic name in the routing path.

**Worker affinity:** the one filter key is `{"resourceName": asset_name}`
(`FBCore.py:86-101`), where `asset_name` is the CCS credential asset
(`integrations/ccs/CCSServices.py:105-108`, cleared at `:295-298`). It is sent **only to the platform
virtual queue** (`api/keywords.py:513-515`, `:532-534`). The business queue sends
`filters=None, canTakeOther=True` (`:659`, `:673`) while logging `"filters": {}` (`:655`), which is
not what goes on the wire. `canTakeOther` is hardcoded `True` at all four call sites and is never
configurable. `affinity` and `capabilities` → 0 hits. See
worker-affinity-filters-are-undocumented (archived).

**Lifecycle.** Two loop implementations select on `GRACEFUL_TERMINATION_ENABLED`
(`robots/FBInitializer.robot:14-16`): `poll-infinitely` (`:38-54`) or
`utils/worker_shutdown.py:103-107`. The only poll interval is a literal `time.sleep(30)` after
`"Waiting For Task..."` (`api/keywords.py:685`) — there is **no sleep on the success path** and no
env key for it. Concurrency: **single-threaded and synchronous** — no `asyncio`, no
`multiprocessing`; threads exist only for the Kafka heartbeat and screen recording.

**Shutdown is flag-file based, not signal-based.** `grep SIGTERM|SIGINT|signal` → **0 hits**. The
k8s preStop hook writes a request flag (`scripts/preStop.sh:12`) and polls for a completion flag for
up to `WORKER_PRESTOP_MAX_WAIT_SECONDS` (default 3600); the worker checks
`os.path.exists(...)` before each poll (`utils/worker_shutdown.py:39-45`,
`api/keywords.py:86-94`). It is **disabled by default** (`worker_shutdown.py:32`).

## No leasing, no heartbeat, no reclaim — confirmed

In **AKST mode** this worker contributes **zero liveness signal**:

- `grep -iE "lease|renew|expiry|reclaim|visibility"` → no protocol hits. All `lockId` references are
  CCS *credential* locks.
- The heartbeat thread is created only inside `if TASK_QUEUE == "KAFKA" :` (`FBCore.py:138-143`), and
  `submit_task_heartbeat_event` additionally depends on `builtins.taskResponseForPickedEvent`, set
  only by the Kafka-only `submit_task_polled_event`.
- **No pick acknowledgement** either: `FBCore.py:362` substitutes the identity function
  `get_a_command` for `submit_task_polled_event` when `TASK_QUEUE != "KAFKA"`.
- The worker exits mid-task **by design**: `sys.exit(0)` every
  `COUNT_FOR_BROWSER_CONTEXT_REFRESH` (default 7) requests (`FBCore.py:388`), plus `sys.exit()` on
  pre/post-execution failure (`api/decorators.py:122`, `:132`) and bad metaInfo
  (`utils/message_utils.py:38`, `:110`). None of those paths emits a completion.
- Graceful shutdown only means "take no new work"; there is no in-flight abandon or NACK, and a
  SIGKILL leaves the completion flag unwritten.

So the AKST-side half of
picked-is-a-terminal-trap (archived) is confirmed from the
worker side too: any reclaim must be implemented server-side, and nothing exists there either.

**Observability:** Datadog APM via `ddtrace` (`listeners/loglistener.py:6,28-74`) — note the
`@distributed_tracing` decorator is a **no-op** with its body commented out
(`api/decorators.py:49-58`). OTel exports **four** metrics over OTLP/gRPC
(`integrations/otel/definitions.py:12-41`): `invocation_count`, `fb_execution_time` (histogram, ms,
with a `status` attribute), `error_count` (emitted on every completion, value 0 when clean), and
`error_code_count` (one datapoint per distinct error code). Datapoint attributes: `app`,
`function_block`, `function_block_version`, `deployment_id`
(`integrations/otel/attributes.py:19-24`). A separate log-only channel prints
`fb_worker.eviction_patch.{attempts,success,failure}` (`utils/pod_eviction_manager.py:121-175`).

**Failure modes:**
- **The OTel package imports a module that does not exist.**
  `integrations/otel/emitters.py:10` — `from e5_rf_fb_worker_core.builtins import BuiltIn` — and
  there is no `e5_rf_fb_worker_core/builtins.py`; every other module imports
  `from robot.libraries.BuiltIn import BuiltIn`. The chain is unconditional at `FBCore` import time
  (`FBCore.py:29-32` → `otel/__init__.py:8` → `manager.py:22` → `factory.py:13` → `emitters.py:10`),
  so it raises `ModuleNotFoundError` **regardless of `METRICS_ENABLED`**. (Not executed here —
  `robotframework` is not installed in this sandbox — but the module is absent and the import is
  module-level.)
- The generic failure path always publishes through **`kafkaTaskHandler`**
  (`utils/response_utils.py:25`, `:205`) even when the message was built in AKST shape (`:156-178`).
- `builtins.taskResources` is dereferenced whenever `FB_OUTBOUND_COMMAND_TOPIC` is unset
  (`utils/message_utils.py:192`, `utils/response_utils.py:175`) but `load_task_resources`
  (`file_utils.py:106-127`) is **never called** → `AttributeError` on that fallback.
- After a platform scale-down or credential-release the process **parks forever** in
  `while True: time.sleep(30)` (`api/keywords.py:583-585`, `:619-621`), relying entirely on external
  pod deletion.
- `api/keywords.py:78` runs `BuiltIn().import_resource("./robots/init_fb_worker.robot")` at import
  time — a hard CWD dependency that breaks any non-Robot import.
- `grpcUnavailableCount`-style escalation does not exist here; a dead AKST just means the 30 s idle
  sleep repeats.
- Duplicated definitions with divergent semantics: `stop_heartbeat_event` three times
  (`api/keywords.py:97`, `utils/response_utils.py:181`, imported at `FBCore.py:58`), `executeTearDown`
  twice (`FBCore.py:390-408` returns a bool, `api/decorators.py:138-148` returns `None`).
- `FBCore.py:289` increments `builtins.requests_processed` (typo, never read) rather than
  `request_processed`, which is bumped at `:299`.

**Code-level landmarks:**
- `FBCore.py:74-75` — the three task handlers and the `TASK_QUEUE` switch
- `FBCore.py:86-101` — `get_task_filters`, the entire affinity mechanism
- `FBCore.py:240-252` — `transform_request_payload`, every envelope field it reads
- `api/keywords.py:629-691` — the AKST poll iteration (probe → fetch → 30 s idle sleep)
- `utils/message_utils.py:143-195` — the two `TaskComplete` shapes (Kafka and AKST)
- `utils/worker_shutdown.py:32-114` + `scripts/preStop.sh` — the flag-file shutdown protocol

**Known divergences:**
worker-affinity-filters-are-undocumented (archived) ·
picked-is-a-terminal-trap (archived) ·
three-divergent-copies-of-task-proto (archived)
(it calls `getFbStateTask`, which is in none of them)

Doc drifts not worth separate cards: `README.md:25-27` says install **Python 3.7** while
`pyproject.toml:41` pins `3.8.16` and CI uses `3.8.16`; `releases/release-1.1.0.rst:37-41` tells
deployers to set `PLATFORM_VIRTUAL_QUEUE`, but the env var is **`PLATFORM_VIRTUAL_QNAME`**
(`conf/settings.py:27`) — setting the documented name yields `""` and the whole platform phase
silently no-ops; `releases/release-0.3.0.rst:37` claims the worker emits `TaskPicked` and
`TaskHeartbeat`, true only in Kafka mode; `releases/release-1.1.10.rst:7` documents only the pod
lifecycle work while HEAD also shipped the entire `integrations/otel/` package with no version bump;
`tests/integrations/test_otel_metrics.py:57-64` asserts metric names `success_count`/`failure_count`
that do not exist and an attribute `deployment_version_id` that is really `deployment_id`, so those
tests cannot pass at this commit; `.github/workflows/pr-merge-check.yml:54,58` run
`pytest tests/unit` and `tests/integration`, neither of which exists (the real dirs are `tests/` and
`tests/integrations/`), so both steps collect nothing; `schema/env_schema.json:10-18` declares
`ENABLE_VIDEO_RECORDING` and `FB_WORKER_TIMEOUT_SECS`, neither of which live code reads.

**Relevant runbooks:** none yet.

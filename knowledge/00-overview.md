---
type: overview
layer: L0
loaded: always (SessionStart). Every other document is reached from here, by path.
verified: 2026-11-14
sources: knowledge/constitution/*, catalog/*, graph/build-facts.jsonl, owner statements, code at the indexed release
---

# E5 platform: the overall picture

**E5** automates post-acute healthcare work in the US (RCD, authorization, OASIS to iQIES, eligibility) with RPA, in clients' EHRs (HCHB through Citrix, WellSky, Waystar). The teams the platform serves:
- **Workflow team:** builds workflows (business orchestration)
- **App team:** builds function blocks (Robot Framework RPA)
- **DevOps:** infra and pipelines
- **Credential management:** the Resource Negotiator credential pool
- **Automation Hub / Neos:** reporting and workflow interaction
- **Support:** works through Neos

`context/`: the company, the teams, workflow vs app, how a workflow deploys and runs, why subsystems exist.

The platform is the base on which E5 builds RPA automation. It has three capabilities:
- **build workflows:** the workflow library
- **build apps:** the app library and ui-synthesizer
- **interact with running workflows and apps:** the subsystems

A platform change serves those teams; name the team it is for.

## Philosophy: the 7 rules (in full: `constitution/philosophy.md`)
1. **Subsystems talk event-driven, over Kafka.**
2. **All task traffic follows the transport task protocol v2:** one `TaskTransport` envelope (`command`, `taskInfo`, `metaInfo`), commands such as `TaskCreate`, `TaskPicked`, `TaskComplete`. Ground truth is the published `task-sdk-java` / `task-sdk-python` packages (source: `e5-task-sdk-java`), not any service's code. Read `contracts/transport-task-protocol-v2.md` before changing a task. Exception: `rpa-gateway` is pinned to v1.
3. **AKST is a mutable prioritised queue** (priority + deadline); tasks are non-idempotent.
4. **Roles before services.** Roles are defined by the protocol (Task Initiator, Router, Queue, Worker, Reader). Before adding a service, find the role that owns the work.
5. **UX first.** UX means what the user provides, gets back and configures, including SDK configs and function-block contracts. UX and the high-level design are settled together, before code.
6. **No shortcuts.** Never overload an enum or column to avoid a migration.
7. **Hide platform mechanics** (GraphQL, gRPC, Kafka, SQL) from workflow and app teams.

`release/vX.Y.Z` is the latest code; `main` is not.

## The map: where each part of the platform is documented

| Area | What it is | Read |
|---|---|---|
| Workflow | A running workflow: one variant image, several roles chosen by `WORKER_MODE` (wf-engine, wf-worker, wf-aggregator, wf-trigger, rpa-gateway, akst) | `workflow-development/README.md` |
| Workflow library | What workflow developers build with: `platform-core` (the SDK), `wf-scaffolding` (seed project), `variant-scaffolding` (composes the image) | `workflow-development/workflow-library/` |
| App | A running app: `fb-worker` = `rf-fb-base-image` + `rf-fb-worker-core` + a customer app. What an app must supply is in `app-development/app/fb-worker-app-contract.md` | `app-development/app/` |
| App library | `rf-fb-worker-core` (Python): poll loop, AKST pull, `TaskComplete` reply, Citrix helpers. `app-sdk` (AppSDK, the function blocks; source `app-lib-scaffolding`). `non-rpa-worker-core` (Java, for non-RPA workers) | `app-development/app-library/` |
| ui-synthesizer | Renders text to bitmaps so a worker can match controls in a Citrix session | `app-development/ui-synthesizer/` |
| Subsystems | The services that act on running workflows and apps (catalog name: workflow-interactions) | `subsystems/README.md` |
| Communication | The task fabric (AKST / task-manager) and the task SDKs that define the task envelope | `communication/README.md`, `contracts/transport-task-protocol-v2.md` |
| Reporting | Platform events, the HIPAA audit log, customer webhooks | `reporting/README.md` |
| Platform delivery | `workflow-config` (env -> workflow -> variant -> tenant -> version), `devops-pipeline` | `platform-delivery/README.md` |

**Cross-cutting documents:**
- `flows/`: task paths across subsystems
- `contracts/`, `tables/`: interfaces and schemas, by name
- `known-issues/`: defects that re-verify at the current release
- `runbooks/`
- `in-flight/`: designs **not released**; never describe them as current behaviour

## Dependencies (structure; exact versions in `dependencies.md`, generated from build files)
- **Workflow:** a variant = `platform-core` + `AppSDK` + the workflow jar, composed by `variant-scaffolding`. `wf-scaffolding` builds on the same two. `platform-core` depends on `task-sdk-java` and `StateService` (the AKST dequeue ordering). A `platform-core` change reaches production only when each variant bumps its pin.
- **App:** `rf-fb-worker-core` depends on `task-sdk-python`. An app image is built FROM `rf-fb-base-image`. ui-synthesizer and the base image have no extracted build facts.
- **Subsystems (Java):** each pins its own `task-sdk-java`, and the versions differ.
- **Consumers outside the platform:** 31 customer workflow, app and app-SDK repos (build files only). A library change is done only when they bump their pins too. `kb_dependencies("<library>")` lists each consumer, its pin, and whether it is behind.

## Subsystems: the overall picture
- **Orchestrators.** Only `workflow-manager`, `workflow-ops` and `monitoring-service` follow the full pattern: poll a task -> handler by task name -> hand sub-tasks to other subsystems -> consume their results -> resume.
  - `workflow-manager` owns the lifecycle of running workflows and apps.
  - `workflow-ops` owns infrastructure: pods, Jenkins, AWS, and the trigger toggle. It is the only service that changes infrastructure (credential-monitor only reads pod state).
  - `monitoring-service` owns health and self-heal.
- **Front door.** `interface` is the only entry for users and Neos (GraphQL). It records a task in deployment-mgmt (`performTask`), then sends the TaskCreate to task-manager, routed by task type. It never performs the action.
- **Store.** `deployment-mgmt` (DMS) is the central database for every subsystem, served over gRPC. It calls no other service.
- **Standalone services:**
  - `dynamic-scaling` (DSS) autoscales fb-worker pods from the tasks available. It records the scaling request in DMS (`createDssTask`) and sends a workflow-manager task (`ENABLE_DEPLOYMENT` / `DISABLE_DEPLOYMENT`) to task-manager itself. workflow-manager then has workflow-ops change the pods.
  - `cds` distributes shared credentials across workflows. It writes `max_replicas` per app to DMS (`UpdateFbWorkerPodDetails`). DSS reads that ceiling from DMS, which is how CDS makes DSS scale.
  - `ccs` holds the credentials (not indexed).
  - `fb-worker` is a leaf handler. `credential-monitor` (a monitoring-service fork) monitors credential assignment and, like monitoring-service, sends `RestartWorkflow` to workflow-ops.
  - `workflow-api` is the business REST API that starts workflows.
- **Task path.** interface or DSS records the task in DMS, then sends the TaskCreate to AKST (`task-manager`), which queues it for the owning subsystem. Routing: `flows/task-names.yaml`.

## Features that span areas (decide which areas, then read only those)

| If the change... | it touches |
|---|---|
| changes a library (platform-core, AppSDK, rf-fb-worker-core, task SDKs) | that library + every consumer from `kb_dependencies`, platform and customer repos, in pin-bump order |
| changes what a workflow developer writes or configures | workflow library (+ platform delivery if it changes a config level) |
| changes what a function block or app can do | app library + the app contract (+ communication if the task envelope changes) |
| acts on a running workflow or app | subsystems: interface (UX) -> the orchestrator that owns the action -> DMS for state |
| adds a task type | interface routing + the owning orchestrator's dispatcher + `task-sdk-*` if the envelope changes |
| persists new state | deployment-mgmt (the only schema owner) + every reader of that table |
| scales workers | DSS (decides) -> workflow-manager (`ENABLE_DEPLOYMENT` / `DISABLE_DEPLOYMENT`) -> workflow-ops (changes pods); cds if credentials limit it |
| emits or consumes platform events | reporting (event-store, event-processor) + the emitting service |
| notifies a customer endpoint (webhook) | reporting: `webhook-service` for workflow-api webhooks, `event-processor` for platform-event webhooks; they are separate chains |
| records what happened for compliance | reporting: `audit-service` + the service where the action happens |
| changes how tasks are queued, prioritised or ordered | communication: `task-manager` / `akst`, and `state-service` for dequeue ordering |

A feature often touches several rows.

## How to traverse
1. This overview: pick the areas.
2. Each area's `README.md`: pick the components.
3. The component document.
4. `flows/` for cross-subsystem paths (`kb_flow` also names the GraphQL operation that starts each task); `contracts/` and `tables/` by exact name.
5. A component's extracted facts: entry points, RPCs served and called, tables, dependencies (`kb_component`). One RPC's server and every caller: `kb_contract`.
6. Inside one handler: `kb_trace("Class#method")`, the method-level path to each RPC and send, with file:line.
7. Code: `platform/<system>/<repo>`, checked out at the indexed release by `/e5-sync`.

**Never guess.** If a document or extracted fact does not say it, report it as unknown.

## Names that collide
- **Workflow Manager is not WorkflowOps.** Task Manager = AKST.
- DSS = `dynamic-scaling`. DMS = `deployment-mgmt` (also db-mgmt-service). CDS = `cds`. CCS = `ccs`. event-store stores nothing; it forwards.
- "Trigger" means two things: `wf-trigger` (a scheduled run start) vs the trigger toggle (served by workflow-ops).

Kafka topic values are deploy-time config and are not recorded.

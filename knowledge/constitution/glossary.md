---
type: constitution
layer: L0
name: Glossary and Name Collisions
authority: authored here — no glossary exists in Confluence or any repo
verified: 2026-10-02
---

# Glossary and Name Collisions

No glossary existed anywhere before this file. "AKST" is mentioned on 36 Confluence pages and
**expanded on none of them.** The collisions below are the most common source of wrong answers
about this platform — resolve them before reasoning about any change.

## What AKST means

**Akst is an anagram of "Task".** Stated in PIP-1012 (`1400045570`) verbatim: *"Akst is just a
reordering of the word 'Task'."* It is not an acronym and the letters stand for nothing.

## Name collisions — read these before answering anything

| Term | What it actually is | What it is NOT |
|---|---|---|
| **Task Manager** | The AKST deployable running in **subsystem mode** (`task-manager-pl-subsystem`). Also the host of the **Global Router** role. | Not a separate product. Same codebase as AKST. |
| **AKST** | The mutable prioritized task queue. Live implementation is **`platform-core/src/main/java/com/e5/platform/core/akst`**. | **Not `e5-akst-service`** — that repo appears to be a stale fork. See [akst-ownership](../disagreements/) |
| **Workflow Manager** | `e5-workflow-manager-service`. Executes workflow **lifecycle actions** — pause, resume, terminate, scale, credential release. Polls an AKST virtual queue. | **Not WorkflowOps.** Different repo, different language, different remit. |
| **WorkflowOps** | `e5-platform-ss-workflow-ops`. Python. Does **infrastructure** work — K8s pod operations, deployment, scaling, workflow triggering via Lambda, EventBridge schedules. | **Not Workflow Manager.** Name similarity is coincidental. |
| **Workflow Router** | An AKST **deploy role** (`routingTable.source: config`) doing namespace-local routing. | Not a separate service or deployable. |
| **Global Router** | An AKST **deploy role** hosted on the existing Task Manager (`source: subsystem`). | Not a new service. Not the revived RPA Gateway. |
| **Interface** | `e5-platform-ss-interface`. The GraphQL façade. External actors enter here. | Not a UI. Neos is the UI; it calls Interface. |
| **Audit** | `e5-platform-ss-audit-service` — the HIPAA audit log. | Not the Face-to-Face *business* audit workflow, which is unrelated and shares the word. |
| **event-store** | An event **forwarder**. gRPC in, Kafka out. | **Not a store.** It has no database, no entity and no state store. The name is actively misleading. |
| **webhook-service** vs **webhook-dispatcher** | Two different deliverers on two disjoint chains. `webhook-service` serves the workflow-api chain; `e5-platform-ss-webhook-dispatcher` is an **AWS Lambda** serving the event-processor chain. | Not alternative names for one thing, and not a service plus its library. |
| **Task Reader** | An ETP v2 role that reads all queued tasks by criteria, feeding dynamic scaling. | Not the Task Poller, and not AKST itself. |

## Request-flow directions that are commonly got backwards

- **Interface → AKST → Workflow Manager.** Interface **never** calls Workflow Manager directly.
  It mints a `taskId` via deployment-mgmt gRPC, then publishes onto a Kafka topic that AKST
  routes to a virtual queue that Workflow Manager polls.
- **Both** Workflow Manager and Interface call WorkflowOps independently. Neither owns it.
- **deployment-mgmt (DMS) is the only schema owner among the subsystems.** Workflow Manager and
  Interface hold no tables of their own; their state lives in DMS.

## Structural vocabulary

| Term | Meaning |
|---|---|
| **Workflow** | Business-flow orchestration, built on platform-core (Temporal). Calls function blocks; contains no RPA. |
| **App** | The RPA automation itself, in Robot Framework. Runs separately, receives delegated tasks. |
| **Function block (FB)** | One business outcome inside an app (e.g. download a document). The unit of delegation. |
| **Variant** | The deployable workflow. Bundles wf-scaffolding and ships to an environment. |
| **Virtual queue (VQ)** | A string in the task table's `taskVQ` column. Not an entity — a VQ exists because a row names it and a worker polls it. |
| **WORKER_MODE** | The env var selecting which role one variant image runs as: WF Engine, WF Worker, AKST, or Aggregator. |

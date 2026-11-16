---
type: constitution
layer: L0
name: Service Ownership
authority: authored here — no service catalogue exists; verified against live dev02 cluster
verified: 2026-10-02
---

# Service Ownership — who owns what

One line each. **This is the table to consult when deciding which service a change belongs
to.** Detail lives in `../knowledge/subsystems/<id>.md`. If a change does not fit an existing
owner, that is the signal to ask whether a new service is justified — usually it is not,
because the work maps to an existing **protocol role** rather than a new deployable.

## Subsystem plane — namespace `platform-subsystem-{dev,qa,test,demo}`

13 deployments, suffix `-pl-subsystem`, labelled `app.kubernetes.io/name=<service>`.

| Service | Owns | Does not own |
|---|---|---|
| `interface` | The GraphQL façade. The **only** front door for external/UI actions. | Execution. It mints a task and dispatches; it never performs the action. |
| `deployment-mgmt` (DMS) | **All subsystem persistent state.** The only schema owner. gRPC-only, no REST. | Business logic. It is a registry and a task-state store. |
| `task-manager` | AKST in subsystem mode; hosts the **Global Router** role. | Routing policy — that is config, not code. |
| `workflow-manager` | **The signal plane.** Translates lifecycle intent (pause/resume, terminate, scale up/down, credential release) into Kafka events on the deployment's own trigger topic and counts acknowledgements. | Infrastructure — it holds **no cloud credentials at all**. Any schema. |
| `workflow-ops` | **The credential-holding actuator** — the only service with Kubernetes, Jenkins and AWS credentials. Owns pod restart and scaling; drives the safe-deployment state machine. | **Does not own deployment** (`DeployWorkflowVariant` returns 404 "Method not implemented" — Jenkins deploys) or **workflow triggering** (invokes a per-tenant Lambda). Owns EventBridge schedule *state* only — no create/delete. |
| `monitoring-service` | Health polling across Kafka/Temporal/Postgres/pods, and self-heal triggering. | Remediation itself — it delegates to workflow-ops. |
| `event-store` | Central event **ingestion and forwarding**. | **Stores nothing** — no DB, no entity, no state store, despite the name. Also does not fan out: one record in, one record out. |
| `event-processor` | Matching platform events to webhook subscriptions and dispatching to the **`e5-platform-ss-webhook-dispatcher` Lambda**, with retry bookkeeping. | Delivery. **Does not feed `webhook-service`** — see the two-chain note below. |
| `webhook-service` | **Outbound delivery for the `workflow-api` chain**: per-host circuit breaker, Postgres queue, backoff, DLQ. | Subscription resolution. **It is not downstream of event-processor.** |
| `audit-service` | The HIPAA audit log — hot Postgres tier, cold S3 Parquet tier. | Any non-audit query path. |
| `workflow-api` | External REST entry for workflow **submission**, with auth, rate limiting, validation, transformation. | Orchestration. |
| `dynamic-scaling` | Bridging AKST metrics to scaling decisions. | Performing the scale — workflow-ops does. |
| `credential-monitor` | Credential-assignment monitoring. | Credential distribution. |

## Workflow plane — namespace `{client}-{workflow}-{variant}-{env}`

One **variant image** runs four roles, selected by `WORKER_MODE`:

| Workload | Role |
|---|---|
| `wf-engine-app-v1` | Orchestration and delegation lifecycle; starts Temporal workflows |
| `wf-worker-app-v1` | Executes Temporal activities; requests delegation |
| `akst-app-v1` | The task queue, namespace-local; **Workflow Router** role |
| `wf-aggregator-{name}-app-v1` | Event aggregation; system of record for execution history |
| `{app}-fb-worker-v1-01` | Separate image per app. Executes RPA. **Scaled to 0 when idle.** The image is `e5-rf-fb-base` + `rf-fb-worker-core` + a **customer app's** Robot source; app repos are not platform deliverables and are out of scope. See [fb-worker-app-contract](../app-development/app/fb-worker-app-contract.md). |

## Libraries — not deployables

| Repo | Owns |
|---|---|
| `platform-core` | The workflow SDK **and the live AKST implementation**. Block taxonomy, signals, state. |
| `e5-task-sdk-java` / `-python` | The transport task protocol models and schemas. **Ground truth for the envelope.** |
| `e5-common-utils` | Shared infra, including the `HealthService` primitives on port 50051. |
| `rf-fb-worker-core` | Python base library for FB workers: the poll loop, the AKST pull, the `TaskComplete` reply, and the Citrix window/clipboard helpers. **Not the deployed artefact** — no entry point, no base class; an app supplies eight named Robot artefacts. |
| `rf-fb-base-image` | The container every fb-worker is `FROM`: Citrix Workspace App, Xvfb, icewm, VNC, xclip, tesseract, wmctrl, conda. Owns the Citrix client config. |
| `ui-synthesizer` | Renders text to a bitmap at request time so a worker can template-match it against a Citrix pixel stream. Named `HCHB.*` for historical reasons; not app-specific. |
| `e5-platform-workflow-config` | The YAML config hierarchy: env × workflow × variant × tenant. |
| `e5-platform-devops-pipeline` | Jenkins CI/CD; generates Helm values; provisions resources. |

## There are TWO webhook chains, not one

This is the most commonly mis-stated fact about the platform. `SUBSYSTEMS.md` and the ASCII
diagram in `platform_whole_context.md` both show `event-processor -> webhook-service`. **That
edge does not exist.** Verified: the webhook-request topic has exactly one producer in the entire
estate, `workflow-api`'s `WebhookRequestProducer`, and event-processor appears nowhere among
its producers. The two chains are payload-incompatible — webhook-service rejects anything
lacking the mandatory `destinationid` and `idempotencykey` CloudEvent extensions, and
event-processor emits a `WebhookKafkaDTO`.

| Chain | Path | Credential handling |
|---|---|---|
| **A — platform events** | event-store -> event-processor -> **`e5-platform-ss-webhook-dispatcher` Lambda** -> customer endpoint | destination URL **and shared secret** travel on the Kafka record and land in a DMS column |
| **B — workflow submissions** | **workflow-api** -> the webhook-request topic -> **webhook-service** -> customer endpoint | both kept only in Secrets Manager |

Chain A putting a shared secret on a Kafka record and into a database column is worth a
separate review.

## A correction worth recording

An earlier draft of this card credited `workflow-ops` with owning deployment, workflow
triggering and EventBridge schedules. That came from the design docs and **the code disproves
it.** The accurate framing: workflow-ops has a wide surface because it is where credentials are
centralised, not because it owns five domains. This is the kind of error the provenance ranking
exists to catch — `code` outranks `confluence-design`.

## Undocumented before this KB

`event-store`, `event-processor`, `credential-monitor` and `db-mgmt-service` had **no
documentation anywhere** — no Confluence page, no substantive README.

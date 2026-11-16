---
type: context
name: How a workflow is deployed and how it runs, end to end
provenance: owner-doc, checked against code where marked
sources: docs/important_context_doc/Platform Architecture.md, Platform-components.md (owner)
verified: 2026-11-14
---

# How a workflow is deployed and how it runs

## Deploy
1. **`workflow-config`** (YAML) holds the layered config: env -> workflow -> variant ->
   tenant -> version.
2. **`devops-pipeline`** (Python, owned by DevOps) provisions what the workflow needs:
   Kafka topics, RDS schemas, Temporal namespaces and Datadog monitors. It generates the
   Helm values.
3. **Helm deploys to Kubernetes:**
   - the variant image (variant-scaffolding), running as several roles chosen by
     `WORKER_MODE`: WF Engine, WF Worker, AKST, Aggregator, Trigger, RPA gateway. It is one
     image built from the platform-core jar, with a different entrypoint per role
     (verified: `E5WorkflowEngine` switches on WF_ENGINE, WF_WORKER, WF_AGGREGATOR,
     WF_TRIGGER, RPA_GATEWAY, AKST_SERVICE)
   - one **fb-worker** image per app
4. The **subsystems** (deployment-mgmt, monitoring-service, workflow-ops and others) are
   deployed separately, once per environment (see [why subsystems](why-subsystems.md)).

A `platform-core` change therefore reaches production only when each variant bumps its pin
and is rebuilt and redeployed.

## Run (one workflow execution)
| # | What happens | Who |
|---|---|---|
| 1 | A trigger event arrives (on a schedule from wf-trigger, or from an external system through workflow-api) | wf-trigger / workflow-api |
| 2 | The WF Engine starts the workflow in Temporal | WF Engine |
| 3 | Temporal schedules an activity; a WF Worker polls the task queue and runs it | WF Worker |
| 4 | If the activity needs automation (a function block), the worker sends a **delegation** request to the WF Engine | WF Worker -> WF Engine |
| 5 | The WF Engine creates a task in AKST | WF Engine -> AKST |
| 6 | AKST stores the task in Postgres and orders it by priority and deadline | AKST (+ StateService for the ordering) |
| 7 | An fb-worker polls AKST and claims the task | fb-worker |
| 8 | The fb-worker runs the Robot Framework function block against the EHR | fb-worker |
| 9 | The fb-worker sends the result (`TaskComplete`) back through AKST | fb-worker -> AKST |
| 10 | AKST publishes the completion event; the WF Engine receives it and signals Temporal to continue | AKST -> WF Engine |
| 11 | The workflow completes | Temporal |
| 12 | The aggregator consumes the workflow's events and updates DynamoDB and Automation Hub | Aggregator |

**Who owns which data:**
- **Temporal:** workflow state
- **WF Engine:** workflow business state
- **AKST:** the task queue and execution events
- **Aggregator:** execution history, and the data sent to Automation Hub
- **DynamoDB:** reporting data

## Keeping it healthy
- **monitoring-service** polls health on a schedule: Kafka connectivity, topics and lag;
  Temporal; Postgres; and each platform pod over HTTP and gRPC. It loads the pod endpoints
  from deployment-mgmt.
- On a problem it starts self-heal by sending `RestartWorkflow` to **workflow-ops**, which
  restarts the service and then re-checks (verified: `flows/task-flows.yaml`,
  restart-workflow-components).

For the subsystem side of the same picture (how support and Neos act on a running
workflow), see [why subsystems](why-subsystems.md).

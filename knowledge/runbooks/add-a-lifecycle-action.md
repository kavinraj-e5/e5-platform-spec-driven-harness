---
type: runbook
name: Add a lifecycle action
applies_to: [interface, deployment-mgmt, workflow-manager, workflow-ops, monitoring-service, task-manager]
triggers:
  - "let support pause/resume/terminate/scale/suspend X"
  - "add a new action Neos can perform on a running workflow"
  - "add a new task type"
provenance: code
verified: 2026-10-02
---

# Add a lifecycle action

Use this when an operator or external system needs to **act on** something running in the
platform. If the ask is only to *read* state, this is the wrong runbook — add a Query to
[interface-graphql-api](../subsystems/interface-graphql-api.md) and stop.

## The shape you are implementing

Lifecycle actions are **asynchronous by construction**. Nothing executes in the request:

```
GraphQL mutation
  -> gRPC PerformTask on deployment-mgmt          mints taskId, persists the task row
  -> (optional) gRPC validation guard             if it fails, write the failure and STOP
  -> Kafka produce to TASK_MANAGER_INBOUND_TOPIC  the transport-v2 envelope
  -> AKST routes on vqName
  -> the consuming service polls its virtual queue and dispatches on fbName
  -> consumer emits TaskComplete
  -> interface Kafka Streams topology correlates on taskIdentifier -> UpdateTaskResponse
  -> caller polls taskStatus until a response appears
```

Platform rules this satisfies: external actors enter via the façade; work is dispatched over
AKST per transport task protocol v2, never a synchronous cross-service call. See
[philosophy](../constitution/philosophy.md).

## Steps, in order

**1. Freeze the UX first.** What the operator supplies, what they get back, and what happens on
failure. The action is only reachable through [interface](../subsystems/interface.md),
so this is a GraphQL mutation signature plus an error contract.

**2. Decide the consumer by responsibility, not convenience.** Check
[ownership](../constitution/ownership.md):

| If the action is… | It belongs to | Virtual queue |
|---|---|---|
| workflow lifecycle — pause, resume, terminate, scale, credential release | `workflow-manager` (the signal plane) | `workflowManagerVQ` |
| infrastructure — pods, deployment steps, schedule toggles, state export/import | `workflow-ops` (the credential-holding actuator) | `workflowOpsVQ` |
| a health or self-heal probe | `monitoring-service` | `monitoringVQ` |

**3. Persist any new state in deployment-mgmt — nowhere else.** It is the only schema owner among
the subsystems. Add a Flyway migration plus entity, repository impl and use case. Do **not**
overload an existing status enum or column to avoid a migration; that is the "no shortcuts" rule.

**4. Register the routing key in BOTH switches.** This is the step that gets missed, and it fails
silently because neither switch has a `default:` branch:

| Switch | File | Sets |
|---|---|---|
| #1 | `TaskManagerHelperImpl.java` (~:120-159) | `taskInfo.taskType` and `taskPayload.fbName` |
| #2 | `TaskManagerProtocolBuilder.java` (~:83-87) | `routingPath…config.vqName` |

They are keyed on the same string but **written differently** — #1 uses named constants, #2 uses
raw string literals — so the compiler cannot relate them. Miss #2 and you get a `null` vqName and
the task is dispatched nowhere. See
unsynchronised-routing-switches (archived).

Then run `python3 tools/check_routing.py`. It will tell you if the two tables disagree.

**5. Add the consumer branch.** `fbName` is the handler key, and it is **not** the same string as
the routing `taskName` — the translation happens in switch #1. Add the branch to the consumer's
dispatch table and make sure the `else` path reports rather than silently succeeding.

**6. Add the response shape — two more places.** `taskStatus` is one SDL field with ~10 concrete
return types chosen by `taskType`:
- the branch in `TaskServiceImpl.getTaskStatus`
- the matching case in `StatusTypeResolver`

**So a lifecycle action is four edit sites in interface alone**, plus the consumer, plus DMS.

**7. Emit an audit event** to the audit-event topic if the action touches tenant-visible state.

**8. Declare it in the release document.** `task_types_added` is a required field precisely so CI
can verify both switches were updated:
```yaml
task_types_added:
  - name: SUSPEND_WEBHOOK
    task_type: E5_WF_MANAGER
    vq: workflowManagerVQ
```

## Failure modes you are inheriting

Know these before you ship; none is your bug but all will be blamed on your feature:

- **The two legs are not transactional.** A Kafka failure after a successful `PerformTask`
  orphans the task row; the exception is swallowed and an empty DTO returned.
- **A completion arriving while interface is down is lost forever** — `auto-offset-reset: latest`,
  no replay. `taskStatus` then polls a 202 indefinitely. See
  interface-streams-loses-completions (archived).
- **A `PICKED` task whose consumer dies is unrecoverable** — no lease, no expiry, no reaper, and
  cancel is guarded to `CREATED`. See
  picked-is-a-terminal-trap (archived).
- **There is no starvation bound.** If you set neither priority nor deadline, both default to
  `Long.MAX_VALUE` and ordering ties. See
  akst-scheduling-logic-lives-in-an-unindexable-jar (archived).

## Reference exemplar

`PAUSE_OR_RESUME_APP` is the cleanest existing end-to-end example: mutation → validation guard →
`E5_WF_MANAGER` / `PAUSE_RESUME_APP` → `workflowManagerVQ` → workflow-manager's dispatch →
Kafka signal event → acknowledgement count. Copy its shape.

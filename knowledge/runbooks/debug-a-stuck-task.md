---
type: runbook
name: Debug a stuck or lost task
applies_to: [task-manager, interface, workflow-manager, workflow-ops, monitoring-service]
triggers: ["task stuck", "taskStatus never completes", "202 forever", "task disappeared"]
provenance: code
verified: 2026-10-03
---

# Debug a stuck or lost task

Work the chain in order. Each step has a known failure mode that produces the *same* symptom —
a `taskStatus` that never resolves — so do not skip ahead.

## 1. Was the task created?

Query the task row by id in deployment-mgmt. If absent, the gRPC `PerformTask` leg failed.
**The gRPC and Kafka legs are not transactional** and the exception is swallowed, returning an
empty DTO — so a missing row with a successful-looking API response is expected behaviour, not
a mystery.

## 2. Is it `CREATED` and never picked?

Check the routing: `kb_flow(<task name>)` or `flows/task-names.yaml` says which subsystem handles each task, with the verified dispatch line.

- **Null VQ.** If a task type was added to one routing switch and not the other, `vqName` is
  `null` and the task was dispatched nowhere. Run `python3 tools/check_routing.py`.
- **Deferred past visibility.** The dequeue predicate is `taskDeferTime >= now()`. The Java
  default is `9999-12-31` but the column default is `0001-01-01` — a row inserted by anything
  other than `createTask` is **permanently invisible**.
- **Starvation.** The ordering is `[affinity,] deadline, priority` with **no arrival-time
  tiebreaker and no starvation bound**. Both fields default to `Long.MAX_VALUE`, so on a busy
  queue an unprioritised task can wait indefinitely. This is not a bug you can fix locally.

## 3. Is it `PICKED` and never completed?

**This is terminal.** There is no lease, no expiry and no reaper. Dequeue filters on `CREATED`
and cancel is guarded to `CREATED`, so a task whose worker died is unreachable by both paths.
The worker in AKST mode sends no heartbeat and no pick acknowledgement, and exits mid-task every
seven requests by design. See
picked-is-a-terminal-trap (archived).

Recovery today is a manual database update. There is no supported path.

## 4. Completed by the worker but `taskStatus` still polls?

The completion is lost. interface's Kafka Streams consumer uses `auto-offset-reset: latest`
with no replay — **a completion arriving while the interface pod was down is gone permanently**.
Also check whether a stream thread died: `StreamResponseHandler` declares its `@Bean` inside a
class Spring never registers, so the `REPLACE_THREAD` policy never took effect and one unhandled
exception stops correlation for the whole service.

## 5. Completed and correlated but the response is wrong shape?

`taskStatus` dispatches to ~10 concrete return types on `taskType`. A new task type needs a
branch in `getTaskStatus` **and** a case in `StatusTypeResolver`. Miss either and you get a
response that does not match the schema.

## Do not conclude "the platform lost it"

Four of the five causes above are recorded defects with cards. Cite the card.

---
type: disagreement
severity: medium
subject: getTask returns null to responseObserver.onNext when the queue is empty, which gRPC does not permit
sources:
  - code: platform-core src/main/java/com/e5/platform/core/akst/service/TaskServiceGrpcImpl.java:212-228, :231-233
  - code: e5-rf-fb-worker-core e5_rf_fb_worker_core/api/keywords.py:659, :673
verified: 2026-10-02
---

# `getTask` hands `null` to `onNext` on an empty queue

`TaskServiceGrpcImpl.getTask` (`:206-229`) does not special-case "no task":

```java
Task task = null;
try {
    task = AkstService.getTask(request);
} catch (Exception e) { … }
responseObserver.onNext(prepareTaskToSend(task));
```

and `prepareTaskToSend` returns `null` for a null argument (`:231-233`):

```java
TaskOuterClass.Task prepareTaskToSend(Task task) {
    if (task == null) {
        return null;
    }
```

`AkstService.getTask` returns `null` whenever the dequeue query finds nothing
(`AkstService.java:288-289`) — the normal state of an idle queue. gRPC's `StreamObserver` contract
does not allow a null message; the server-side stub dereferences it when marshalling, so the call
terminates with an internal error status rather than an empty response. The `catch` above it only
covers `AkstService.getTask`, not the `onNext`.

The same method also has a second null hazard: when a task *is* returned it unconditionally
dereferences the payload —
`((FunctionBlockTaskData) JsonUtils.toEntity(task.getTaskPayload(), …)).getFbName()` (`:222`) — and
`prepareTaskToSend` dereferences `taskPayload.getFbPayload()` into `convertMapToStruct`
(`:240`, `:246`), which throws `NullPointerException` on a null map and
`IllegalArgumentException("Unsupported object type: …")` on any `fbPayload` value that is not
`String`, `Integer`, `Double`, `Boolean`, `Map`, `List` or null (`:278-295`) — a `Long` or a
`BigDecimal` is enough.

## Consequence

A client cannot use `getTask` as a poll: it must call `isTaskAvailable` first and accept the race
between the two calls (the probe takes no row lock — see
[akst-dequeue-order-is-undocumented](akst-dequeue-order-is-undocumented.md)). Every known client
does exactly that — `e5-rf-fb-worker-core` gates its fetch on
`isTaskAvailable` (`api/keywords.py:659` then `:673`) — so the defect is masked by convention
rather than fixed, and it is a trap for any new client.

Worse, the race is not benign in the other direction: when `isTaskAvailable` says yes and a
concurrent worker wins the row, the losing `getTask` takes the null path and the caller sees a gRPC
error, not an empty answer. Idle queues and lost races are indistinguishable from real faults.

## Resolution

Return a default-instance `Task` (or add a `found` boolean / use `optional`) instead of `null`, and
move the `onNext` inside the `try`. A single `getTask` that is safe to call speculatively would also
remove the need for the racy two-call protocol.

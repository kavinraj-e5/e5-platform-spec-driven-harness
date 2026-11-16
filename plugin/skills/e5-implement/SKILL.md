---
name: e5-implement
user-invocable: false
description: Internal (invoked by e5-feature at Stage 5). Use when executing an approved E5 platform plan across multiple repos with isolated worktrees and per-repo subagents. Normally invoked by e5-feature at Stage 5 rather than chosen by the user; requires an approved plan and the PreToolUse gate enforces that.
---

# E5 multi-repo implementation

Requires an approved plan (`approved: true`). The PreToolUse gate enforces this.

## Per repo, in merge order

```
e5 workspace add <changeset> <repo>     # bare-mirror cache + git worktree
```

Dispatch **one subagent per repository**. Give it: the spec section for that repo, the service
card, and the worktree path. Require a bounded report back — a diff summary and test results,
never file dumps. This is the single largest token lever in the design; the orchestrator must
never hold five repos at once.

## Order matters

Contract owner → stub regeneration → consumers. Getting this wrong means a consumer compiles
against a proto that has not shipped.

## Before each PR

- `python3 tools/check_routing.py` if a task type changed — it compares both switches
- `buf breaking --against '.git#branch=release/vX.Y.Z'` for proto changes
- `atlas migrate lint` for migrations
- `./e5 check` (every gate), after `./e5 sync all --no-fetch` if the change moved code the knowledge describes

## Inherited failure modes to design around

These are recorded in `disagreements/` and will be blamed on your feature:
the gRPC and Kafka legs of a task dispatch are **not transactional**; a completion arriving while
interface is down is **lost** (`auto-offset-reset: latest`, no replay); a `PICKED` task whose
worker dies is **unrecoverable** (no lease, no reaper, cancel guarded to `CREATED`).

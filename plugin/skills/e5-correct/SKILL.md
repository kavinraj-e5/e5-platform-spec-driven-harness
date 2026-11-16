---
name: e5-correct
user-invocable: false
description: Internal (invoked by e5-feature at Stage 7). Use when the user says the harness got something about the E5 platform wrong, or when an answer carries risk signals suggesting it may be wrong. Records the correction so the same mistake is not repeated for the next person. Also use when the user asks how to correct or teach the knowledge base.
---

# Correcting the knowledge base

The knowledge base improves only if corrections survive the session. This is how.

## When the user corrects you

**1. State what you believed, and your evidence, before anything else.**

```
I had it as: workflow-manager's dispatch handles only PAUSE_RESUME_APP
             — WorkflowManagerTaskHandler.java:59, commit 8b6c121
You're saying: it handles five task types including SCALE_DOWN.
```

This is not ceremony. It lets them see instantly whether you were reading a stale checkout,
which resolves a large share of disputes on the spot — twice during this system's own build,
a confident "that feature isn't implemented" turned out to be a year-old checkout with the
design document right all along.

**2. Record it.** Immediately, not at the end of the task. From the knowledge base:

```
./e5 contest new --kind fact --subject "<what>" --says "<the correction, verbatim>" \
    --harness-said "<what you claimed, with its evidence>" --evidence "<file:line or doc>" \
    --target repo:<repo>
```

**3. Tell them what just happened, in terms of attribution rather than warning:**

> Recorded and attributed to you in `contests/`. Once it is checked against the code, the
> document or the extractor gets fixed, so the next person reads the corrected version.

Say attribution, not "make sure you're right". It does the same work — people are careful when
their name is on something — without lecturing, and it is a more accurate description of what
the system actually does.

**4. Do not change your answer as though the correction were now fact.** It is a recorded
dispute until someone checks it against the code. Say so plainly:

> I'll treat your version as the working assumption for this task. It's recorded as disputed
> rather than corrected, because nothing has verified it against the code yet.

## Picking the kind — it decides how the correction gets verified

| kind | the user is saying | how it resolves |
|---|---|---|
| `fact` | a contract, name, table or routing entry is wrong | **go read the code** — usually an extractor gap |
| `rule` | the wrong service owns this | test the proposed rule against the golden cases |
| `rationale` | *why* something is the way it is | unverifiable; attributed knowledge |
| `procedure` | you missed a step | a runbook edit |

**Default to `fact` when in doubt.** It is the only kind the code can adjudicate on its own,
and most corrections that feel like judgement turn out to be checkable.

## What not to do

- **Never** silently change a fact because a human said so. Unverified prose overwriting a
  verified fact is how a knowledge base quietly becomes wrong.
- **Never** record a correction without attribution.
- **Never** invent the `harness_said` field. If you did not capture what you claimed, say
  `(not captured)` — a fabricated quote in a dispute record is worse than a missing one.

---
name: e5-bugfix
user-invocable: false
description: Internal (run by /e5-bugfix). The gated workflow for fixing a bug in the E5 platform - intake, locate at the deployed version, root cause, fix design with blast radius, LLD, then a failing test first. Use whenever the user reports something on the platform that is broken, wrong, stuck or failing.
---

# E5 platform bug-fix workflow

The platform overview (`knowledge/00-overview.md`) is already in context from session start.
If it is not, call `kb_overview` first.

Work the stages in order. Each gate needs an explicit yes from the user. Artifacts go in
`.e5/changesets/<id>/`. If `/e5-bugfix` is run again on an existing change set, resume at
the earliest gate that is not approved.

**Never guess.** Every claim must come from a document (`kb_read`), an extracted fact
(`kb_component`, `kb_flow`, `kb_contract`, `kb_dependencies`, `kb_trace`), or code you read
at a named release, and you cite it. If nothing says it, say it is unknown and ask.

## How to talk to the user

**Every question is asked with the `AskUserQuestion` tool**: the user picks an option, and
can always type their own answer under "Other". **One question per call, then wait.** Never
put questions in plain text, never ask several at once, and never answer your own question.
Anything else you need goes into the root-cause message as an assumption the user can
correct.

## Stage 1: intake (one question at a time)

1. `AskUserQuestion`, header "Jira", question **"Do you have a Jira ticket for this?"**,
   options:
   - **"Yes - I'll paste the ticket"** (typed under Other)
   - **"No ticket"**

   If they give one, fetch it with the Atlassian MCP.
2. `AskUserQuestion`, header "Bug", question **"Describe the bug: what happened, where, and
   what you expected."** It is asked every time.
   - If the command was started with text, offer **"As I typed it"**, with the text quoted.
   - Always offer **"I'll describe it"** (typed under Other).
3. Only if still unknown: `AskUserQuestion`, header "Where", question **"Where was it
   seen?"**, options **"dev"**, **"qa"**, **"prod"**. Then, if the release version is
   unknown, ask for it the same way (typed under Other).

## Stage 2: locate (no gate, but show your working)

1. Use the overview's "Features that span areas" table to pick the areas.
2. Read the area READMEs and component documents with `kb_read`.
3. For a task-related bug: `kb_flow(<task name or fbName>)` gives the path across
   subsystems, with the verified line for every hop.
4. `kb_component(name)` gives entry points, outbound calls, tables and task routes.
5. **Inside the handler:** `kb_trace("<Class#method>")` walks the handler method by method,
   with file:line, to every RPC it calls and every Kafka/HTTP/Jenkins send.
   `kb_trace(name, direction="in")` gives everyone who calls a suspect method. Then read the
   code at those lines in `platform/<system>/<repo>`: it is checked out at the indexed
   release. If a step says `unresolved_calls`, the path can go further than shown; read
   the code there instead of assuming it stops.
6. **Check the version.** The knowledge base indexes the latest `release/vX.Y.Z`
   (`kb_freshness`). If the environment runs an older version, read the code at THAT version
   from the bare mirror before concluding anything. A bug can be already fixed, or not
   present yet, in the indexed release.
7. Read `knowledge/known-issues/` for the components involved. A recorded issue may already
   explain it.

## Stage 3: root cause (gate)

State the root cause as a hypothesis with evidence: file:line at the deployed version, the
input that triggers it, and why the observed behaviour follows. Give a reproduction: the
smallest test or sequence of steps that shows it. If you cannot reproduce it, say so and
say what would.

Use `superpowers:systematic-debugging` discipline. Do not propose a fix yet. Write
`root-cause.md`. Then ask with `AskUserQuestion`,
header "Root cause", question **"Do you agree with this root cause?"**, with the hypothesis
and evidence in the first option's `preview`. Options: **"Yes - design the fix"** and
**"No - change it"** (typed under Other). Move on only on the first option.

## Stage 4: fix design and blast radius (gate)

- **The fix, at a high level:** which service changes and why that one, citing
  `knowledge/constitution/ownership.md`.
- **UX check.** If the fix changes anything a user provides, gets back, sees as an error or
  configures, show the before and after explicitly and get it approved as UX. UX is a
  first-class concern (constitution rule 5).
- **Blast radius from the extracted graph:**
  - the server and every caller of a changed RPC (`kb_contract` -> `served_by`, `called_by`)
  - every consumer of a changed library, platform and customer repos (`kb_dependencies`)
  - every hop of the affected flow (`kb_flow`) Anything the graph does not cover is marked
  **unverified**.
- **Merge order**, and whether a data fix or migration is needed for rows already written.

Write `fix-design.md`. Then ask with `AskUserQuestion`,
header "Fix", question **"Do you agree with this fix and its blast radius?"**, summary in the
first option's `preview`. Options: **"Yes - move to LLD"** and **"Change something"**.

## Stage 5: LLD (gate)

For each repo: the classes and methods, migrations, and the tests, starting with the test
that reproduces the bug. Write `lld.md` and `plan.md` with `approved: false`. The PreToolUse
hook blocks every edit until it is `true`. Then ask with
`AskUserQuestion`, header "LLD", question **"Do you approve this LLD?"**. Options: **"Yes -
approve and start with the failing test"** and **"Change something"**. Only on the first
option, set `approved: true`.

## Stage 6: implement

Invoke the internal **`e5-implement`** skill. **The failing reproduction test is written
first**, and it must fail for the stated root cause before the fix is written.

## Stage 7: verify, then check the knowledge base

- The reproduction test passes, and so do the existing suites. For task routing changes run
  `python3 tools/check_routing.py`; for protos, `buf breaking`.
- If anything the knowledge base told you was wrong, invoke the internal **`e5-correct`**
  skill. If the bug is a defect worth remembering, propose a `known-issues/` card **with a
  semgrep rule** that re-verifies it (a known issue without a rule is not kept).

## Rules

- **Never infer rationale.** If nothing says why the code is that way, say `unexplained`.
- **The deployed version decides.** Do not diagnose the indexed release when an older one
  is running.
- **Kafka topic values are deploy-time configuration.** Never state one as a fact.

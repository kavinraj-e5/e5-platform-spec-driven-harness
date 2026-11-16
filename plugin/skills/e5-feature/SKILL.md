---
name: e5-feature
description: THE entry point for any change to the E5 platform. Use whenever the user describes something they want the platform to do, stop doing, or do differently - "I want X to happen when Y", "can we add", "customers need" - even if they never say the word feature. Orchestrates the whole gated workflow and invokes the internal e5 skills itself. Use BEFORE opening any repository.
---

# E5 platform feature workflow

The platform overview (`knowledge/00-overview.md`) is already in context from session start.
If it is not, call `kb_overview` first.

Work the stages in order. Each gate needs an explicit yes from the user. Artifacts go in
`.e5/changesets/<id>/`. If `/e5-feature` is run again on an existing change set, resume at
the earliest gate that is not approved.

**Never guess.** Every claim about the platform must come from a document you read
(`kb_read`) or an extracted fact (`kb_component`, `kb_flow`, `kb_contract`,
`kb_dependencies`, `kb_trace`), and you cite it. If nothing says it, say it is unknown and ask.

## How to talk to the user

**Every question is asked with the `AskUserQuestion` tool**: the user picks an option, and
can always type their own answer under "Other". **One question per call, then wait for the
answer.** Never put questions in plain text, never ask several at once, and never answer
your own question. Anything else you need goes into the UX + HLD proposal as an assumption
the user can correct. The dialog may be all the user sees, so make each question stand on
its own.

## Stage 0: intake (exactly two questions, one at a time)

1. `AskUserQuestion`, header "Confluence", question **"Do you have a Confluence doc for
   this?"**, options:
   - **"Yes - I'll paste the link"**: description "Type the link or page title under Other"
   - **"No doc"**: description "Go on without one"

   If they give a link or title (under Other, or in their next message), fetch it with the
   Atlassian MCP (`getConfluencePage`, or search Confluence by title). Do not summarise it
   back yet.
2. `AskUserQuestion`, header "Request", question **"Describe what you want me to do."**
   This is asked every time, even when a doc was given.
   - If the command was started with text, offer it as the first option:
     **"As I typed it"**, with the text quoted in the description. Add a second option,
     **"I'll describe it differently"**, telling them to type under Other.
   - With no text, offer **"I'll type it"** and **"Use the Confluence doc as the
     description"** (the second only if a doc was given). The user types under Other.

   The command's text is context; the answer to this question is the description.

Do not ask anything else in Stage 0.

## Stage 1: read (silent, no message to the user)

Use the overview's **"Features that span areas"** table to decide which areas the change
touches; a feature often touches several. Then read **only** those areas, top-down:

1. `kb_read("<system>/README.md")`, then the area README: pick the components.
2. `kb_read` the component documents those READMEs link to.
3. `kb_component(name)` for each candidate: role, documents, extracted entry points, RPCs,
   outbound calls, tables, task routes and dependencies.
4. `kb_flow(task)` for any task path the change rides on. Use `kb_contract(identifier)` for
   any RPC, message, table or event the story names, and `kb_dependencies` for any library.
5. `kb_trace("<Class#method>")` for a handler the change goes through: the method-level
   path to every RPC and send it makes, with file:line. This is how "how it works today"
   is told from the code rather than from a guess.
6. `kb_freshness` before describing anything as current behaviour.

Do not open a repository yet. Do not message the user during this stage; go straight to
Stage 2.

## Stage 2: UX + HLD (one gate)

UX and HLD are designed together, because each changes the other. Present them in ONE
message and write the same content to `design.md`:

- **Today:** how it works now, in a few lines, citing sources.
- **What changes:** the goal and who it is for, in the user's terms.
- **Assumptions:** anything you had to assume, each one line, each with its source or
  marked "assumed". This is where open questions go. Do not ask them as separate questions.
- **UX**, per case:
  - what the user provides, what they get back, the errors and the configs
  - **show the current UX first**, from the documents and extracted GraphQL/gRPC surface
  - for library changes, the current SDK configs, config levels and function-block contracts
  - follow `knowledge/constitution/philosophy.md` rule 5 and the UX definition in the overview
- **HLD:** which system and service does what, and **why that one and not the others**,
  citing `knowledge/constitution/ownership.md`. Say whether a new service is needed (usually
  not: find the protocol role). High level only, no code.
- **Drafting order:**
  - draft the UX first, then the HLD that serves it
  - if the HLD forces a UX change, show that change explicitly
  - if the user changes the UX, redo the HLD
  - UX leads; an HLD convenience never silently changes the UX

Then ask with `AskUserQuestion`, header "UX + HLD", question **"Do you agree with this UX
and HLD?"**. Give a short summary (UX in 3-5 lines, HLD in 3-5 lines, assumptions) in the
`preview` of the first option. Options:
- **"Yes - approve and move to blast radius"**
- **"Change the UX"**: they type what to change under Other
- **"Change the HLD"**: same
- **"Change an assumption"**: same

- If the user asks for changes, revise both sections and ask the same question again.
- Move to Stage 3 **only** on an explicit yes.

## Stage 3: blast radius (gate)

List every repo that changes and every consumer that must be checked. Build the list by
**following the extracted graph, not by guessing**:
- the outbound calls and task routes in `kb_component`
- for a changed RPC: its server and **every caller** (`kb_contract` -> `served_by`,
  `called_by`, with file:line). `declared` alone is not enough: repos copy protos
- for a changed message or enum: every declarer (`kb_contract` -> `declared`)
- for a changed library: **every consumer, platform and customer repos**, with its pin and
  whether it is behind (`kb_dependencies` -> `used_by`, `inherited_by`); each consumer must
  bump and rebuild
- every hop of the affected flow (`kb_flow`)
- inside each changed handler or method: every caller (`kb_trace(name, direction="in")`)
  and everything it reaches (`kb_trace(name)`), across the files of that repo
- for a table: deployment-mgmt plus every reader of that table

For each repo give the reason and its evidence. Give the merge order: the contract owner
first (deployment-mgmt schema and protos), then stub regeneration, then consumers. Name the
deployment impact.

Anything the graph does not cover (for example, a step `kb_trace` marks `unresolved_calls`, or a csharp or yaml repo with no call graph),
mark as **unverified** and say what would confirm it. Write `blast-radius.md`. Then ask with `AskUserQuestion`,
header "Blast radius", question **"Do you agree with this blast radius?"**. Put the repo list
and merge order in the first option's `preview`. Options: **"Yes - approve and move to LLD"**
and **"Change something"** (typed under Other). Move on only on the approve option.

## Stage 4: LLD (gate)

Per repo: the classes and methods, proto, schema and migrations, and tests. Name each
method that changes from `kb_trace` and read it in `platform/<system>/<repo>` (the indexed
release) before writing its change. Use the team's
PIP template: `Status` / `Motivation` / `Interfaces Changes` / `Proposed Changes` /
`Compatibility, Deprecation, and Migration Plan` / `Rejected Alternatives`.

Write `lld.md` and `plan.md` (the per-repo task DAG) with `approved: false` in the
frontmatter. The PreToolUse hook blocks every edit until it is `true`. Then ask with `AskUserQuestion`,
header "LLD", question **"Do you approve this LLD?"**, with the per-repo summary in the first
option's `preview`. Options: **"Yes - approve and start coding"** and **"Change something"**.
Only on the approve option, set `approved: true`.

## Stage 5: code

Invoke the internal **`e5-implement`** skill: one worktree per repo from the bare mirrors,
TDD, and merge order.

## Stage 6: verify and ship

- `python3 tools/check_routing.py` if a task type changed
- `buf breaking` for protos
- `atlas migrate lint` for migrations
- coordinated PRs, in merge order

## Stage 7: was the knowledge base right?

If anything you read turned out wrong, invoke the internal **`e5-correct`** skill before you
finish.

## Rules that are not negotiable

- **Never infer rationale.** If no document gives the reason, write `rationale: unexplained`.
- **`release/vX.Y.Z` is the latest code; `main` is not.**
- **Kafka topic values are deploy-time configuration.** Never state one as a fact.
- **`knowledge/in-flight/` is not released behaviour.** Use it only as design input, and say so.

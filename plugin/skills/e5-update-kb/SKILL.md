---
name: e5-update-kb
user-invocable: false
description: Internal (run by /e5-update-knowledge-base). Onboards a new service, a new component or a new/in-flight feature into the E5 knowledge base from its documents and the extracted code, or records a correction. Writes only authored files, and only after the user approves the diff.
---

# Updating the E5 knowledge base

The knowledge base is read top-down by path, never searched (see `knowledge/README.md`). So
anything you add must sit in the right folder and be linked from the level above it, or it
will never be read.

**Never guess.** Content comes from the documents the user gives you, from code at a named
release, or from extracted facts, and each claim cites its source. A reason nobody gave you
is recorded as `rationale: unexplained`.

## Stage 1: what is being added

Ask every question with the `AskUserQuestion` option dialog, **one per call**, and wait for
the answer; never as plain text and never several at once.

1. `AskUserQuestion`, header "What", question **"What are you adding to the knowledge
   base?"**, options:
   - **"A new service"** (a new repository)
   - **"A new component"** (in an existing repo, e.g. a WORKER_MODE role or a library module)
   - **"A new or in-flight feature"**
   - **"A correction"** (the knowledge base said something wrong). Invoke the internal
     `e5-correct` skill and stop here.
2. `AskUserQuestion`, header "Docs", question **"Do you have documents for it
   (implementation, HLD, UX)?"**, options **"Yes - Confluence link"**, **"Yes - I'll paste
   them"**, **"No documents"**. Fetch Confluence pages with the Atlassian MCP.
3. `AskUserQuestion`, header "Describe", question **"Describe it in your own words."** It is
   always asked; the user types under Other.

## Stage 2: classify (confirm with one `AskUserQuestion`: approve, or change)

- **System and area.** Pick from the overview map: workflow, workflow library, app,
  app library, ui-synthesizer, subsystems, communication, reporting, platform delivery.
- **Kind:** Library / Deployable / Role / BaseImage / Config / Tooling.
- **Pattern:**
  - an orchestrator: polls tasks, dispatches by task name, hands sub-tasks out, consumes
    results
  - a leaf handler
  - a standalone service: store, control loop, event consumer, API
  - a library
- **Who it is for:** which platform users.

## Stage 3: register and extract (new service or component)

1. For a new repo, add a row to `registry.yaml` (id, remote, language, build). `/e5-init`
   then mirrors it at its latest `release/vX.Y.Z`.
2. `./e5 probe --repo <id>` proposes a profile; show it to the user, never apply it blindly.
3. Add the catalog entry under `catalog/NN-<system>.yaml`, with `role:` written from the
   documents and verified against the code.
4. If it handles tasks, add one row per task to `knowledge/flows/task-names.yaml`:
   - the fields: `task_name`, `task_type`, `fb_name`, `produced_by` / `produced_at`,
     `handled_by` / `site` / `line`
   - take each one from the code
   - `python3 tools/evidence.py task-names` must pass
5. If a new multi-hop path exists, add a flow to `knowledge/flows/task-flows.yaml`, with a
   `shows:` token on every step. `python3 tools/evidence.py flows` must pass.
6. Run `/e5-sync` for that component so its facts are extracted.

## Stage 4: write the documents (show the diff, get a yes)

- **The component document:** `knowledge/<system>/[<area>/]<component>.md`, from
  `templates/component.md`:
  - purpose, what it owns and does NOT own
  - its UX surface (API, configs, function-block contracts)
  - entry points and outbound calls (from `kb_component`)
  - the gotchas, each claim citing its source
- **A new area:** add it to `AREAS` in `tools/gen_system_readmes.py`.
- **An alias** people use: add it to `ALIASES` in `tools/kb_nav.py`.
- **Regenerate the READMEs:** `python3 tools/gen_system_readmes.py`.
- **If the overall picture changed** (a new system, a new orchestrator, a new kind of change
  in the "features that span areas" table), propose the edit to `knowledge/00-overview.md`.
  It must stay under 2.5k tokens.
- **In-flight features** go only under `knowledge/in-flight/<feature>/` and are never
  described as current behaviour.

**Show the full diff. Nothing is written until the user says yes.**

## Stage 5: verify

Run:
- `python3 -m pytest tools/test_knowledge_structure.py tools/test_kb_nav.py`
- `python3 tools/task_flows.py check`
- `python3 tools/citations.py check`

Report the result as it is, including failures.

## Rules

- Never write extracted facts by hand; only the extractors write `graph/`.
- Never record a deploy-time value: Kafka topic names, environment values, virtual queue
  names from config.

# User guide

## The rule
Nothing is written until you have approved a design. Every question is a multiple-choice
dialog (pick an option, or type your own under "Other"), one at a time. Every answer about the
platform comes from a document or from facts extracted from the code, with a file and line, and
the harness says "unknown" rather than guess.

## /e5-feature
1. **Intake.** "Do you have a Confluence doc?" (optional), then always "Describe what you want".
   The doc never replaces your description.
2. **Understand (silent).** It reads the overview, the areas the change touches, the flows and
   the code facts. You see nothing yet.
3. **UX + HLD gate (one gate).** One proposal with two linked parts:
   - *UX*: what the user provides, gets back and configures (an API field, an SDK config, a
     function-block contract). UX leads; if the design forces a UX change it says so.
   - *HLD*: which service does what, and why that one and not another.

   Approve, or change something. Changing either re-runs the other.
4. **Blast-radius gate.** Every repo, RPC caller, flow hop, table reader and library consumer
   the change reaches, plus merge order. Anything the graph does not cover is marked unverified.
5. **LLD gate.** Per repo: classes and methods, proto, migrations, tests. Approving it sets
   `approved: true` in `.e5/changesets/<id>/plan.md`; until then a hook blocks every edit.
6. **Code.** One worktree per repo, tests first, contract owner merged first.
7. **Verify, then a knowledge check.** If the knowledge was wrong, the correction is recorded.

Run `/e5-feature` again on the same change and it resumes at the first unapproved gate.

## /e5-bugfix
Jira is optional; a description of the bug is always asked. Then locate (flow, handler, version
deployed), **root-cause gate** (hypothesis with file:line), **fix + blast-radius gate**, **LLD
gate**, a failing test first, verify.

## /e5-update-knowledge-base
Add a new service, component or in-flight feature (from its docs), or record a correction.
Nothing is written until you approve the diff, and only authored files are touched.

## Looking things up yourself
The tools Claude uses are also in the shell: `./e5 kb overview`, `./e5 kb component <name>`,
`./e5 kb flow <task>`, `./e5 kb contract <rpc>`, `./e5 kb dependencies <lib>`,
`./e5 kb trace "Class#method"` (what a handler does inside its service). Exact names only;
there is no search, by design.

## Keeping current
`/e5-sync` after the team ships; `./e5 update` for a newer harness. A cited line that moved
shows up as a gate warning; read it, do not "bless" it away. Corrections you approve are files
in this repo: open a pull request.

## Things that surprise people
- **`main` is not latest.** Releases are `release/vX.Y.Z`.
- **Do not edit under `platform/`.** Those are read-only copies at the indexed release; sync
  skips any with local edits. Changes are made in change-set worktrees.
- **Kafka topics and environment values are not in the knowledge.** They are deploy-time
  configuration, not facts about the code.
- **Workflow Manager is not WorkflowOps.** Task Manager is AKST.

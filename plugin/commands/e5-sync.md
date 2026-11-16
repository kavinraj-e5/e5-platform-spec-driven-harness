---
description: Clone or update every platform repo under platform/, compile, extract and rebuild the E5 knowledge base
---
Bring the knowledge base up to date with the repositories.

Locate the knowledge base: prefer `$E5_KB_ROOT`; otherwise read the path from
`${CLAUDE_PLUGIN_ROOT}/hooks/kb_root.txt`. Then, from that directory:

1. **Show the scope first:** `./e5 sync status`. It lists, per component, whether its
   release moved since the last sync. If nothing moved, the repos are already under
   `platform/`, and the user did not ask for a full refresh, say so and stop. On a machine
   that has never synced, skip this and go straight to step 2.
2. **Run the update:** `./e5 sync all`. Run it in the background and report each `===` step
   as it finishes; the first run is about 1 GB and 15-30 minutes, later runs a few minutes.
   In order, it:
   - fetches the bare mirrors into `~/.e5/cache` (clones any that are missing, with the
     user's own credentials; no token is written to disk)
   - checks every repo out under `platform/<system>/<repo>` at the release the knowledge
     base indexes (`subsystems/workflow-ops`, `workflow-development/workflow-library/
     platform-core`, `app-development/app-library/rf-fb-worker-core`, ...)
   - compiles the JVM repos (gradle or maven, JFrog for private dependencies) and reads
     their bytecode; only repos whose commit moved are rebuilt
   - runs the declared extractors per component profile, then code facts, task routes, RPC
     servers and callers, GraphQL-to-task links
   - builds the method-level call graph (tree-sitter for Java, pyright for Python) that
     `kb_trace` reads
   - extracts build dependencies, customer consumer repos and the newest library releases
   - regenerates the READMEs and `dependencies.md`, and runs every gate

   It stops at the first failing extractor. The checkout, compile and call-graph steps are
   advisory: a repo that does not compile is reported and the sync carries on. Flags:
   `--no-fetch` skips fetching, `--no-build` skips compiling, `--force` re-reads every repo.

   **A checkout with local edits is never touched** (reported as DIRTY), and nothing is read
   from it: the facts always describe the indexed release. Changes are made in change-set
   worktrees by `/e5-feature` and `/e5-bugfix`, not in `platform/`.
3. **Report what the gates said, as they said it.** The important ones:
   - **task-flows:** this includes the CONTENT check. A routing row or flow step whose cited
     line no longer shows its hop is an error. Do not edit the evidence to make it pass;
     the code moved or changed, and someone has to read it. Report the hop and the line.
   - **citations:** errors mean a card cites a line that does not exist. Run
     `./e5 cite drift` for the cards whose cited lines changed since they were verified.
     That list is a re-verification worklist for a person, not something to bless away.
     The pipeline's "citations whose code moved" step shows which ones are MOVE (the cited
     text still exists exactly once; `python3 tools/cite_relocate.py --apply` fixes the
     number) and which are READ (the code changed; a person re-reads the claim).
   - **N/A violation** from the declared extractors: a component's profile asserts "no gRPC
     here" (or no GraphQL, or no schema) and one appeared. Do not widen the profile to make
     it pass; the component may belong to a different system.
   - **library-skew, staleness, estate-drift, card-patterns:** advisory; report them, do not
     fail on them.

Never edit `graph/` by hand, and never record a deploy-time value (Kafka topic names,
environment values) to make something pass.

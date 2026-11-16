"""The extractor REGISTRY. No LLM, no inference, no network.

Every fact carries provenance (which extractor, which file, which line) so that
conflicting sources can be ranked rather than silently merged.

All eleven extractor BODIES now live in `extract/declared/`, behind real parsers. What
is left here is the registry and the `(repo_id, root)` signature `tools/sync.py` calls,
so the pipeline did not have to change for any of the ports. `fact()` stays because it
is the schema, and `extract.core.fact` is kept byte-identical to it.

`walk()` and `read()` are gone with the last regex extractor: a source is now a
`core.Repo` (a bare mirror) or a `core.DirRepo` (a tree), and neither front end knows
what a filesystem is.
"""

def fact(kind, name, repo, path, line, provenance, confidence="EXTRACTED", **props):
    """A fact is an OCCURRENCE; a contract is an IDENTITY.

    `id` must be unique per occurrence - 419 ids previously collided because a shared
    proto vendored into nine repos produced nine facts with the same id.
    `contract_key` is the repo-independent identity, and is what release diffs and
    cross-repo joins key on.
    """
    return {"id": f"{kind}:{repo}:{path}:{line}:{name}", "contract_key": f"{kind}:{name}",
            "kind": kind, "name": name, "repo": repo,
            "path": path, "line": line, "provenance": provenance,
            "confidence": confidence, "props": props}



# ============================================================================
# WORKFLOW-PLANE EXTRACTORS
#
# These exist because the first eight extractors were all written for
# proto/SQL/GraphQL subsystems, and the four workflow-plane repos consequently
# produced 12 facts out of 3,852 - 0.3% - for the part of the platform that does
# the actual RPA automation. Nothing was broken; the questions simply were not
# being asked. These ask them.
#
# The workflow plane has no protos and no GraphQL. Its contracts are: the
# WORKER_MODE role split, the handler classes each role registers, and the
# published SDK packages it imports. All three are statically extractable, and all
# three now live in `extract/declared/runtime.py`, `deps.py` and `rf.py`.
#
# The fb-worker is a Robot Framework library: `rf` in rf-fb-worker-core. Its contract
# with a workflow is the KEYWORD SET - the automation primitives an RPA step can call.
# There is no proto, no schema and no annotation; the surface is the public functions
# of the keyword module. `^def\s+([a-z][a-z0-9_]*)\s*\(` found 47 of them; `ast` finds
# 65, because that name class stops at the first uppercase letter and the FTPS and SFTP
# families are spelled `e5_SFTP_download_file`.
# ============================================================================


# ---------------------------------------------- kafka topics and virtual queues: GONE
#
# `extract_kafka` and `extract_virtual_queues` used to live here. They are deleted, not
# repaired, because what they extracted cannot be right: a topic or queue NAME is
# injected by the devops pipeline at deploy time, and every repo is written to run with
# whatever value it is handed.
#
# Measured before deleting them:
#
#   * `workflow-manager/deployment-configs/qa-config.yaml` ships
#     `"taskVirtualQueueName": "value"` - the literal word `value`. Its `.env` says
#     `wmq`. `dynamic-scaling`'s Spring default for the very env var the cluster sets
#     says `workflow_manager_vq`. The cluster says `wfmanager_service_vq`. One queue,
#     four names, three fictional.
#   * 7 of the 10 queue literals in source name a queue the platform does not run, and
#     31% of the facts came from JUnit fixtures (`any_vq`, `other_VQ`, `tenant_app_VQ`).
#   * 140 facts across the whole knowledge base carried an injected-value identity -
#     1.7% of 8,448. Deleting all of them costs almost nothing.
#
# THE LINK THESE WERE TRYING TO MAKE IS AVAILABLE WITHOUT ANY VALUE. `interface` routes
# `PAUSE_OR_RESUME_APP` in a switch at TaskManagerProtocolBuilder.java:84, and
# `workflow-manager` dispatches `PAUSE_RESUME_APP` at WorkflowManagerTaskHandler.java:90.
# Two independent code sites agree on the TASK NAME, and no queue name is involved. That
# is `extract_routing` plus the authored `_reference/joins/task-names.yaml`, which is why
# those survive.
#
# Where no code-level link exists, the answer is written down by a human instead of
# guessed by a tool: `knowledge/flows/task-and-event-flows.md`.



# ---------------------------------------------------------------------------------
# PORTED TO extract/ - ALL ELEVEN now delegate to a parser. This module is now the
# registry and the call signature `(repo_id, root)` that tools/sync.py uses, and
# nothing else: every extractor body lives in extract/declared/.
#
# This module keeps the registry and the signature `(repo_id, root)` that
# `tools/sync.py` calls, so the pipeline is unchanged. The bodies moved to
# `extract/declared/`, where a tree-sitter front end reads Java and the standard
# library's `ast` reads Python.
#
# Verified by `python3 -m extract.cli diff`, which runs the parser and the regex side by
# side over all 23 mirrors. Every difference is signed off in extract/PORTING.md:
#
#   db_bindings   identical
#   worker_roles  identical
#   handlers      2 FEWER, both regex false positives. `public @interface E5Aggregator`
#                 is an annotation TYPE and the regex matched the token `interface`
#                 inside it; `AutoHubAggregatorHandlerTest.java` is a file in which
#                 EVERY line is commented out, and the regex matched
#                 `//class AutoHubAggregatorHandlerTest {`.
#   rf_keywords   18 MORE - 27% of that component's public surface. The name class
#                 `[a-z][a-z0-9_]*` ends the match at the first uppercase letter, so the
#                 whole FTPS and SFTP keyword families were invisible.
#   sdk_imports   155 line numbers CORRECTED. `^\s*` under re.M consumes the preceding
#                 blank line, so every import after a blank line was cited one line
#                 early - and the line number is what `./e5 cite check` verifies.
#   routing       3 MORE. Two arms were unreachable to the regex because its label
#                 character class had no `.`, so `case InterfaceConstants.X ->` matched
#                 nothing; and a SECOND routing switch with no default arm was being
#                 suppressed because the old check asked whether the word `default`
#                 appeared anywhere in the FILE. 6 destinations also stop bleeding in
#                 from the next arm - the old one read a 400-character text window.
#   events        8 FEWER and 1 MORE. The 8 are `e5.audit.*` / `e5.webhook.*`, which are
#                 KAFKA TOPIC NAMES, not event types: every occurrence in source is a
#                 Spring `${VAR:default}` default, a Javadoc comment or a test, and qa
#                 runs e5_qa_audit_events / e5_qa_webhook_requests instead. The 1 is a
#                 JSON Schema `enum` the old suffix list could not reach.
#   config        1,836 occurrences -> 400 facts, SAME 397 keys plus 3 the regex could
#                 not see: `- name: "client_id"` is QUOTED and the key character class
#                 had no `"`. The collapse is the point - the duplication was 5 copies
#                 of every key, one per environment file, and the v3 plan's worked
#                 example found one of them cited four times as "evidence". SsmPath is
#                 deleted: its 3 facts were a Jenkins job path read out of a `value:`.
#   proto         1,431 line numbers CORRECTED and 44 enums added. `^\s*message` under
#                 re.M ate the preceding blank line (the sdk_imports defect again), and
#                 every rpc was cited at its SERVICE's line - so all 126 rpcs of
#                 deployment_management.proto pointed at one blank line. Request and
#                 response types are now FULLY QUALIFIED, which is what makes them
#                 joinable across repos, and nested messages are scoped.
#   graphql       119 line numbers CORRECTED - the same two defects as proto: `^\s*`
#                 ate the preceding blank line, and every root field was cited at its
#                 `type Query {` line. Plus `scalar JSON`, which the kind list omitted.
#   migrations    sqlglot PLUS a COUNTED regex fallback, because sqlglot degrades 46 of
#                 this estate's statements to an opaque `Command` - 11 of them CREATE
#                 TABLEs. Found: 4 DROP COLUMNs the AST path first missed, 2 columns
#                 the regex could never see (`ADD COLUMN a, ADD COLUMN b` in one
#                 statement), and one phantom table matched inside a PL/pgSQL
#                 `format('CREATE TABLE audit.%I ...')` string.
# ---------------------------------------------------------------------------------


def _ported(name):
    """Call the parser-backed extractor with a filesystem-rooted source."""
    def run(repo, root):
        import sys as _sys
        from pathlib import Path as _Path
        kb = _Path(__file__).resolve().parent.parent
        if str(kb) not in _sys.path:
            _sys.path.insert(0, str(kb))
        from extract.core import DirRepo
        from extract.declared import PORTED
        return PORTED[name](DirRepo(id=repo, root=_Path(root)))
    run.__name__ = f"extract_{name}"
    run.__doc__ = f"Delegates to extract.declared.{name}. See the note above this."
    # `extract.cli diff` reads this. Once an extractor is switched over there is no
    # regex left to compare against, so the diff compares the parser with itself and
    # reports "identical" - which is true and completely uninformative. The flag is
    # what lets the harness say so instead of looking like a passing check.
    run.ported = True
    return run


extract_worker_roles = _ported("worker_roles")
extract_handlers = _ported("handlers")
extract_sdk_imports = _ported("sdk_imports")
extract_db_bindings = _ported("db_bindings")
extract_rf_keywords = _ported("rf_keywords")
extract_routing = _ported("routing")
extract_events = _ported("events")
extract_config = _ported("config")
extract_proto = _ported("proto")
extract_graphql = _ported("graphql")
extract_migrations = _ported("migrations")


# `kafka` and `virtual_queues` are deliberately absent - see the block comment above
# `_EVENT`. They extracted deploy-time VALUES, so they could not be made correct.
EXTRACTORS = {
    "proto": extract_proto, "graphql": extract_graphql,
    "events": extract_events,
    "routing": extract_routing, "migrations": extract_migrations, "config": extract_config,
    # workflow-plane: see the block comment above extract_worker_roles
    "worker_roles": extract_worker_roles, "handlers": extract_handlers,
    "sdk_imports": extract_sdk_imports, "db_bindings": extract_db_bindings,
    "rf_keywords": extract_rf_keywords,
}

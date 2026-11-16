"""Tests for the estate diff.

The diff tests build facts as literals, so a failure points at the subject/shape rules
rather than at a repo that moved. The index and snapshot tests run against the real
graph, because "this does not fall over on 5,199 subjects" is a claim worth holding.

The tests marked WAS WRONG ONCE encode mistakes this tool made on its own first run.
Two of them produced the same symptom - every subject in the estate reported as
CHANGED - which is the failure mode that makes a diff tool worthless rather than wrong:
nobody reads a worklist with 5,199 entries, so a flood hides the three real findings
exactly as well as a crash would.
"""
import gzip
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import estate as X                    # noqa: E402

KB = HERE.parent


def fact(**kw):
    """A source-layer fact with the fields every extractor emits."""
    f = {"kind": "GrpcRpc", "component": "akst", "repo": "platform-core",
         "contract_key": "GrpcRpc:TaskService.getTask", "name": "TaskService.getTask",
         "path": "src/main/proto/task.proto", "line": 75, "commit": "aaaaaaa",
         "branch": "release/v1.0.0", "confidence": "EXTRACTED", "provenance": "code",
         "props": {"request": "TaskAvailableRequest", "response": "Task"}}
    f.update(kw)
    return f


def changes(then: list, now: list) -> dict:
    """verdict -> [subject], for a before/after pair of fact lists."""
    out = {}
    for c in X.diff(X.index(now), X.snapshot(X.index(then))):
        out.setdefault(c["verdict"], []).append(c)
    return out


# --------------------------------------------------------------- the subject rules
def test_a_fact_that_only_moved_is_not_a_change():
    """The whole reason the unit is a subject and not a fact.

    `cite drift` already answers "this line moved". If this tool answered it too, one
    edit above a method would appear as a GONE, a NEW and a drift entry across two
    tools, and the reader would have to work out they were the same event.
    """
    assert changes([fact(line=75)], [fact(line=412, path="src/main/proto/task.proto")]) == {}


def test_a_commit_bump_alone_changes_nothing():
    """Every fact carries the commit it came from, and every sync changes it."""
    assert changes([fact()], [fact(commit="bbbbbbb", branch="release/v1.1.0")]) == {}


def test_bookkeeping_fields_are_not_part_of_a_subjects_shape():
    """WAS WRONG ONCE.

    `load_facts` tags each fact with `_src`, the file it was read from. That tag was not
    in VOLATILE, so facts read from disk and facts re-extracted in memory had different
    shapes and the first rehearsal reported all 267 of interface's ConfigKeys as CHANGED.
    """
    assert changes([fact(_src="facts.jsonl")], [fact()]) == {}


def test_a_changed_prop_is_reported_as_changed_and_names_the_field():
    got = changes([fact()], [fact(props={"request": "TaskAvailableRequest",
                                         "response": "TaskV2"})])
    assert list(got) == ["CHANGED"]
    assert got["CHANGED"][0]["detail"] == ["props.response: Task -> TaskV2"]


def test_a_version_bump_is_one_changed_subject_not_a_gone_and_a_new():
    """`version` is deliberately absent from IDENT for DependsOnArtifact.

    Put it in the identity and a library bump reads as one artifact disappearing and a
    different one appearing, which is both twice the noise and the wrong shape of news:
    the question a reader has is "what changed", not "what are these two artifacts".
    """
    dep = dict(kind="DependsOnArtifact", repo="akst-service", group="e5.state",
               artifact="StateService", path="build.gradle", provenance="build-file")
    dep.pop("props", None)
    got = changes([{**dep, "version": "1.3.4"}], [{**dep, "version": "1.3.5"}])
    assert list(got) == ["CHANGED"], got
    assert got["CHANGED"][0]["detail"] == ["version: 1.3.4 -> 1.3.5"]


def test_a_removed_subject_is_gone_and_a_fresh_one_is_new():
    got = changes([fact()], [fact(contract_key="GrpcRpc:TaskService.ackTask",
                                  name="TaskService.ackTask")])
    assert sorted(got) == ["GONE", "NEW"]
    assert got["GONE"][0]["key"] == "GrpcRpc:TaskService.getTask"
    assert got["NEW"][0]["key"] == "GrpcRpc:TaskService.ackTask"


def test_a_subject_is_scoped_to_its_component():
    """Two components can hold the same contract. platform-core holds six of them."""
    got = changes([fact(component="akst")], [fact(component="akst"),
                                             fact(component="wf-engine")])
    assert list(got) == ["NEW"] and got["NEW"][0]["owner"] == "wf-engine"


def test_a_path_and_line_inside_a_key_collapses_to_the_basename():
    """RoutingRule and VirtualQueuePoll key on `<repo>:<path>:<line>` - location, as an
    identity. Left alone, every such subject would churn on any edit above it."""
    k = "RoutingRule:platform-core:src/main/java/com/e5/akst/RoutingMetrics.java:75"
    kind, owner, key = X.subject_of(fact(kind="RoutingRule", contract_key=k, name=k))
    assert key == "RoutingRule:platform-core:RoutingMetrics.java"
    moved = k.replace(":75", ":91")
    assert X.subject_of(fact(kind="RoutingRule", contract_key=moved, name=moved))[2] == key


def test_authored_facts_are_never_diffed():
    """flow-facts.yaml is written in this repository. Diffing it would report our own
    edits as platform movement, which is the opposite of the question being asked."""
    assert changes([], [fact(provenance="authored-join")]) == {}


def test_every_fact_layer_is_either_diffed_or_left_out_on_purpose():
    """A new extractor writes a new graph/*-facts.jsonl. Nothing would notice.

    Derived from the directory, not from a list, because the lesson from the slash-command
    guard is that a hardcoded inventory only ever catches the thing you already fixed.
    """
    # call-facts.jsonl: Level 2, one row per METHOD (9k+). A refactor inside a service is
    # not platform movement; the layers above already report entry points, RPCs and routes.
    known = set(X.FACT_FILES) | {"flow-facts.jsonl", "citation-facts.jsonl", "call-facts.jsonl"}
    found = {p.name for p in (KB / "graph").glob("*facts.jsonl")}
    assert not (found - known), (
        f"{sorted(found - known)} is a fact layer the estate diff ignores silently.\n"
        f"  Add it to FACT_FILES, or to this test's exclusion set with a reason.")


# ------------------------------------------------------------------- identifiers
def test_an_expression_is_not_a_searchable_identifier():
    """An unresolved queue is recorded as the expression that produces it. That is the
    right thing for the extractor to do and a nonsense thing to grep a card for."""
    assert X._searchable("TaskRoutingEndpoints.getDestinationVqName(taskTransport)") is None
    assert X._searchable("#{scalingEstimatorConsumerConfig.topic}") is None
    assert X._searchable("e5_platform_workflow_ops_vq") == "e5_platform_workflow_ops_vq"


def test_a_generic_word_is_not_a_searchable_identifier():
    """`topics` is a real extracted name and also an ordinary English word."""
    assert X._searchable("topics") is None
    assert X._searchable("op") is None
    assert X._searchable("AUDIT_RETENTION_HOT_TIER_DAYS")


def test_a_kafka_topic_ref_is_named_by_its_topic_not_by_its_config_key():
    """WAS WRONG ONCE.

    A KafkaTopicRef keys on the config key that holds the topic - `audit-service:topics`.
    Searching cards for `topics` found nothing, and the report said no card named any
    Kafka topic reference, which was a fact about the key and not about the cards.
    """
    assert X._ident("KafkaTopicRef", "KafkaTopicRef:audit-service:topics",
                    {"env_var": "KAFKA_TOPIC_AUDIT_EVENTS"}) == "KAFKA_TOPIC_AUDIT_EVENTS"


def test_a_test_only_subject_is_never_called_undocumented():
    """`any_vq` lives in TaskTransportParserTest.java. Nobody failed to document it."""
    idx = X.index([fact(kind="VirtualQueue", contract_key="VirtualQueue:any_vq",
                        name="any_vq", path="src/test/java/T.java",
                        props={"test": True})])
    assert list(idx.values())[0]["test_only"] is True
    idx = X.index([fact(kind="VirtualQueue", contract_key="VirtualQueue:real_vq",
                        name="real_vq", props={"test": False}),
                   fact(kind="VirtualQueue", contract_key="VirtualQueue:real_vq",
                        name="real_vq", path="src/test/java/T.java",
                        props={"test": True})])
    assert list(idx.values())[0]["test_only"] is False


# ------------------------------------------------------------- the real knowledge base
def test_the_committed_snapshot_is_smaller_than_the_facts_it_summarises():
    """The reason it is gzipped. Uncompressed it was 2.8 MB against a 2.6 MB
    facts.jsonl, and a summary that costs more to store than its source does not get
    committed - at which point there is nothing to diff the next sync against."""
    assert X.SNAP.is_file(), "run `python3 tools/estate.py accept`"
    facts = sum((KB / "graph" / f).stat().st_size
                for f in X.FACT_FILES if (KB / "graph" / f).is_file())
    assert X.SNAP.stat().st_size < facts // 4


def test_the_committed_snapshot_carries_no_volatile_field_in_a_shape():
    """WAS WRONG ONCE, and it is the second half of the `_src` bug.

    Fixing VOLATILE in the code left a snapshot on disk that had been written under the
    old rule, so the next run flooded again - 5,199 CHANGED from correct code and a stale
    artefact. Any VOLATILE field found inside a committed shape means the snapshot
    predates the current shape rule and must be re-taken, not debugged.
    """
    with gzip.open(X.SNAP, "rt") as fh:
        snap = json.load(fh)
    bad = {k for e in snap["subjects"].values() for sh in e.get("shapes", []) for k in sh
           if k in X.VOLATILE}
    assert not bad, (f"the committed snapshot carries {sorted(bad)} in its shapes - it "
                     f"was written under an older VOLATILE. Re-run `estate accept`.")


def test_the_snapshot_round_trips_against_the_estate_it_was_taken_from():
    with gzip.open(X.SNAP, "rt") as fh:
        snap = json.load(fh)
    assert snap["subjects"], "snapshot has no subjects"
    # re-snapshotting the same index must be a no-op, or `accept` would never settle
    idx = X.index()
    assert not X.diff(idx, X.snapshot(idx))


def test_the_real_estate_indexes_and_every_subject_has_an_owner():
    idx = X.index()
    assert len(idx) > 3000, f"only {len(idx)} subjects - a fact layer went missing"
    assert not [s for s, e in idx.items() if e["owner"] in ("", "?")]


def test_the_strongest_route_wins_per_card():
    """A card that both names the identifier and cites the file is reported once, as
    `names`. Printing it twice would make one card look like two findings."""
    links = X.Links.__new__(X.Links)
    links.tok = {"e5_qa_audit_events": {"knowledge/a.md"}}
    links.cite = {("audit-service", "src/K.java"): {"knowledge/a.md", "knowledge/b.md"}}
    links.repo = {"audit-service": {"knowledge/a.md", "knowledge/c.md"}}
    links._blobs, links._sub = {}, {}
    got = links.of({"ident": "e5_qa_audit_events", "repo": "audit-service",
                    "paths": ["src/K.java"]})
    assert got == [("names", "knowledge/a.md"), ("cites", "knowledge/b.md"),
                   ("describes", "knowledge/c.md")]


def test_estate_is_advisory_in_e5_check():
    """An estate change is not a defect in this repository. A gate that fails the build
    because the platform shipped a release is a gate someone deletes."""
    s = (KB / "e5").read_text()
    assert '("estate-drift", "estate.py")' in s, "estate is not wired into `e5 check`"
    m = re.search(r'if r and name not in \(([^)]*)\)', s, re.S)
    assert m and "estate-drift" in m.group(1), (
        "estate is BLOCKING in `e5 check` - it must be in the advisory list")


def test_diff_runs_on_the_real_knowledge_base():
    assert X.cmd_diff(5) == 0


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn()
            print("PASS ", fn.__name__)
        except Exception:
            bad += 1
            print("FAIL ", fn.__name__)
            traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)

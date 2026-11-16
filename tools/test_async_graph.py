"""Tests for the asynchronous communication graph.

Parser tests use literal fragments so a failure means the parser broke, not that a repo
moved. Estate tests use the real facts, because the tool's whole claim is about the real
estate. Several of these exist because the bug they describe actually happened while the
extractor was being written and produced a confident wrong answer.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "release"))
import async_graph as A      # noqa: E402
import catalog as C          # noqa: E402

KB = HERE.parent
FACTS = KB / "graph" / "async-facts.jsonl"


def _facts():
    return [json.loads(l) for l in FACTS.open()]


# ELEVEN TESTS WERE DELETED FROM THIS FILE, not fixed. They pinned the five-rung topic
# resolution ladder - code literal, @Value default, bound getter, application.yml, then
# the OBSERVED QA CLUSTER - and the `virtual_queue` that `TaskRouting` resolved the same
# way. Every one of them asserted that a deploy-time VALUE had been correctly chased
# down, which is a thing this estate cannot support: the devops pipeline injects the
# name and each repo runs with whatever it is handed. `workflow-manager`'s committed
# qa-config literally says `"taskVirtualQueueName": "value"`.
#
# A test that pins wrong behaviour is worse than no test, because it makes the behaviour
# look deliberate. What replaced them is below: the task name is the contract, and it is
# compared in code by two repositories independently.

# --------------------------------------------------------------------- parsing
def test_yaml_flatten_handles_parent_keys():
    """`topic:` with no value is a PARENT. Requiring a value meant parents never pushed
    onto the stack and `kafka.topic.producer.task-manager` flattened to nothing - the
    whole task plane resolved to zero."""
    y = ("kafka:\n  topic:\n    producer:\n      task-manager: ${TASK_MANAGER_INBOUND_TOPIC}\n"
         "    consumer:\n      task-manager: ${TASK_MANAGER_OUTBOUND_TOPIC}\n")
    f = A._flatten_yaml(y)
    assert f["kafka.topic.producer.task-manager"] == "${TASK_MANAGER_INBOUND_TOPIC}"
    assert f["kafka.topic.consumer.task-manager"] == "${TASK_MANAGER_OUTBOUND_TOPIC}"




def test_every_async_edge_carries_its_evidence_and_how_it_was_resolved():
    for f in _facts():
        if f["kind"] not in ("TopicProduce", "TopicConsume"):
            continue
        assert f.get("resolution"), f"{f} has no resolution"
        assert f.get("provenance") in ("code", "authored-join"), f
        if f.get("provenance") == "code":
            assert f.get("path"), f"{f} has no file"


def test_every_component_named_in_an_async_fact_exists_in_the_catalog():
    cat = C.load()
    for f in _facts():
        for key in ("component", "handled_by"):
            n = f.get(key)
            if n:
                assert n in cat["Component"], f"async fact names unknown component {n!r}"


def test_extraction_is_deterministic():
    a, b = A.extract(), A.extract()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_no_secret_from_the_cluster_snapshot_reaches_the_facts():
    """The resolver reads pod env, which is where credentials live."""
    body = FACTS.read_text().lower()
    for bad in ("password", "secret", "token", "ghp_", "aws_access", "bootstrapserver"):
        assert bad not in body, f"{bad!r} leaked out of the cluster env into async facts"


# --------------------------------------------------------------------- subsystem routing




def test_the_task_name_is_the_contract_and_two_repos_compare_it():
    """What replaced the queue resolution. `interface` routes PAUSE_OR_RESUME_APP at
    TaskManagerProtocolBuilder:84; `workflow-manager` dispatches PAUSE_RESUME_APP at
    WorkflowManagerTaskHandler:90. Two repositories, one string, no queue name."""
    routes = [f for f in _facts() if f["kind"] == "TaskRoute"]
    assert routes, "no TaskRoute facts"
    r = next(f for f in routes if f["task_name"] == "PAUSE_OR_RESUME_APP")
    assert r["component"] == "interface" and r["handled_by"] == "workflow-manager"
    assert r["fb_name"] == "PAUSE_RESUME_APP"
    for f in routes:
        assert f.get("handled_by"), f
        # producer may be unknown only when task-names.yaml declares it null
        assert f.get("component") or f.get("task_name") == "SCALE_UP_WORKER", f


def test_no_async_fact_carries_a_queue_or_topic_value():
    """The regression guard for the whole redesign."""
    import re as _re
    blob = FACTS.read_text()
    for pat in (r'_vq"', r'_VQ"', r'e5_qa_', r'"virtual_queue"', r'"topic"'):
        assert not _re.search(pat, blob), f"{pat} is back in async-facts.jsonl"


def test_the_routing_switch_field_is_kept_because_the_field_name_is_definite():
    """`workflowManagerVQ` is what a developer wrote in the switch. Its VALUE is not
    ours to know, and `virtual_queue`/`vq_resolution` are gone for that reason."""
    routing = [f for f in _facts() if f["kind"] == "TaskRouting"]
    assert routing, "no TaskRouting facts"
    assert any(f.get("vq_field") for f in routing)
    for f in routing:
        assert "virtual_queue" not in f and "vq_resolution" not in f, f



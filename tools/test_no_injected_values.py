"""Tests for the gate that keeps deploy-time values out of the facts.

Every test that asserts the gate FIRES splices a ghost fact into a real layer and
restores it, because a gate nobody has watched fail is a gate that does not work. The
false positives recorded here were real: the first version flagged 75 correct facts.
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_no_injected_values as G      # noqa: E402

KB = HERE.parent
LAYER = KB / "graph" / "code-facts.jsonl"


def _with_ghost(fact: dict):
    """Append a fact, run the gate, always restore."""
    orig = LAYER.read_text()
    try:
        LAYER.write_text(orig + json.dumps(fact, sort_keys=True) + "\n")
        return G.scan()
    finally:
        LAYER.write_text(orig)


def test_the_estate_is_clean_right_now():
    assert G.scan() == [], G.scan()[:5]


def test_a_field_whose_job_is_to_hold_a_channel_fires():
    """The primary rule. The defect was never one string - it was storing `which topic`
    as data at all."""
    bad = _with_ghost({"kind": "GhostFact", "component": "interface",
                       "topic": "anything_at_all"})
    assert any(b[3] == "topic" for b in bad), bad


def test_a_queue_field_fires_even_with_an_innocuous_value():
    bad = _with_ghost({"kind": "GhostFact", "component": "interface", "queue": "q1"})
    assert any(b[3] == "queue" for b in bad), bad


def test_a_vq_suffixed_value_in_a_new_field_name_fires():
    """The backstop, for a field named something nobody listed."""
    bad = _with_ghost({"kind": "GhostFact", "component": "interface",
                       "destination_thing": "some_service_vq"})
    assert any("some_service_vq" in b[4] for b in bad), bad


def test_an_environment_segment_fires():
    bad = _with_ghost({"kind": "GhostFact", "component": "interface",
                       "channel": "e5_qa_task_manager_cmdq"})
    assert any("e5_qa_task_manager_cmdq" in b[4] for b in bad), bad


def test_a_measured_fiction_fires():
    """`workflow_manager_vq` is dynamic-scaling's stale Spring default; the platform runs
    `wfmanager_service_vq`. It is in the graph's history, not its present."""
    bad = _with_ghost({"kind": "GhostFact", "component": "interface",
                       "something": "workflow_manager_vq"})
    assert bad, "a known-fictional queue name was not caught"


# ----------------------------------------------- what must NOT fire (all were FPs once)
def test_a_config_key_never_fires():
    """WAS A FALSE POSITIVE RISK. Joining on keys is the replacement strategy, so a key
    containing the word queue has to be allowed."""
    for field in ("config_key", "env_var", "declares_config_key", "vq_field"):
        bad = _with_ghost({"kind": "GhostFact", "component": "interface",
                           field: "TASK_VIRTUAL_QUEUE"})
        assert not bad, f"{field} was flagged: {bad}"


def test_a_published_artifact_coordinate_never_fires():
    """WAS WRONG ONCE. `e5_task_sdk_python` is a PyPI package name and matched an
    `^e5_<x>_<y>$` value rule, flagging 75 correct artifact facts."""
    bad = _with_ghost({"kind": "GhostFact", "component": "interface",
                       "repo": "e5_task_sdk_python", "artifact": "e5_task_sdk_python"})
    assert not bad, bad


def test_a_grpc_stub_literal_never_fires():
    """WAS WRONG ONCE. `literal` was in the forbidden-field set, which flagged 23
    `CallsComponent` facts holding gRPC enums and stub class names."""
    bad = _with_ghost({"kind": "GhostFact", "component": "interface",
                       "literal": "DeploymentManagementServiceBlockingStub"})
    assert not bad, bad


def test_a_dotted_event_type_never_fires():
    """An EventType is a payload contract written in source, not a channel."""
    bad = _with_ghost({"kind": "EventType", "component": "interface",
                       "name": "e5.platform.event.workflow.started"})
    assert not bad, bad


def test_a_protobuf_message_called_VirtualQueue_never_fires():
    """credential-monitor really declares one, at task.proto:10. The word is not the
    problem; a deployed channel name is."""
    bad = _with_ghost({"kind": "ProtoMessage", "component": "credential-monitor",
                       "name": "VirtualQueue"})
    assert not bad, bad


# --------------------------------------------------------------------- it actually runs
def test_the_gate_exits_nonzero_when_it_finds_something():
    orig = LAYER.read_text()
    try:
        LAYER.write_text(orig + json.dumps({"kind": "GhostFact", "queue": "x_vq"}) + "\n")
        r = subprocess.run([sys.executable, str(HERE / "check_no_injected_values.py")],
                           capture_output=True, text=True)
        out = (r.stdout + r.stderr).lower()
        assert r.returncode == 1, out
        assert "deploy-time channel name" in out, out
    finally:
        LAYER.write_text(orig)


def test_it_is_a_blocking_gate_in_e5_check():
    e5 = (KB / "e5").read_text()
    assert '("injected-values", "check_no_injected_values.py")' in e5
    advisory = e5[e5.index('if r and name not in ('):]
    advisory = advisory[:advisory.index(")")]
    assert "injected-values" not in advisory, "this gate must block, not warn"


def test_it_runs_in_ci():
    g = (KB / "ci" / "jenkins-kb-index.groovy").read_text()
    assert "check_no_injected_values.py" in g
    assert g.rindex("error(") > g.rindex("python3 tools/check_no_injected_values.py") - 2000

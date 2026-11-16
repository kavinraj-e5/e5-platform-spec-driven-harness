"""Tests for task flows - the ordered multi-hop chains.

A flow is AUTHORED, so the risk is not that the parser breaks but that the file drifts
away from the code and becomes a confident diagram of something that no longer happens.
Most of these tests exist to make that drift fail loudly.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "release"))
import task_flows as TF    # noqa: E402
import catalog as C        # noqa: E402

KB = HERE.parent


def test_the_authored_flows_validate():
    assert TF.check(TF.load()) == 0, "task-flows.yaml has errors - see output above"


def test_a_task_hop_that_contradicts_the_routing_table_is_an_error():
    """The load-bearing check. An authored chain is only worth having if it fails when
    it stops matching the extracted routing table - otherwise it is a diagram that ages
    silently into fiction."""
    flows = [{
        "name": "wrong", "summary": "deliberately contradicts the routing table",
        "applies_to": {"task_names": ["FB_WORKER_POD_SCALE_APP_WORKER"]},
        "steps": [{"from": "interface", "to": "workflow-ops", "via": "task",
                   "carries": "FB_WORKER_POD_SCALE_APP_WORKER",
                   "evidence": "interface src/main/java/X.java:1"}],
    }]
    assert TF.check(flows) == 1, "a contradicted task hop must be an ERROR"


def test_a_step_naming_an_unknown_component_is_an_error():
    flows = [{"name": "x", "summary": "s",
              "steps": [{"from": "interface", "to": "not-a-service", "via": "grpc",
                         "evidence": "interface src/main/java/X.java:1"}]}]
    assert TF.check(flows) == 1


def test_a_step_with_no_evidence_is_an_error():
    """A hop with no file:line is an assertion, not knowledge."""
    flows = [{"name": "x", "summary": "s",
              "steps": [{"from": "interface", "to": "workflow-ops", "via": "grpc"}]}]
    assert TF.check(flows) == 1


def test_an_unknown_mechanism_is_an_error():
    flows = [{"name": "x", "summary": "s",
              "steps": [{"from": "interface", "to": "workflow-ops", "via": "telepathy",
                         "evidence": "interface src/main/java/X.java:1"}]}]
    assert TF.check(flows) == 1


def test_a_flow_with_no_summary_is_an_error():
    """A chain with no stated purpose is a diagram. The summary is where the branches
    and the resume behaviour get written down, and those are the parts that matter."""
    flows = [{"name": "x", "summary": "  ",
              "steps": [{"from": "interface", "to": "workflow-ops", "via": "grpc",
                         "evidence": "interface src/main/java/X.java:1"}]}]
    assert TF.check(flows) == 1


# --------------------------------------------------------------------- content
def test_scale_app_worker_is_multi_hop_and_reaches_four_components():
    """The flow that motivated the model. A single producer->handler edge says
    `interface -> workflow-manager` and hides six further hops."""
    f = next(x for x in TF.load() if x["name"] == "scale-app-worker")
    steps = TF.resolve(f, "FB_WORKER_POD_SCALE_APP_WORKER")
    assert len(steps) >= 7, f"only {len(steps)} steps"
    comps = {c for s in steps for c in (s["from"], s["to"])}
    for expected in ("interface", "workflow-manager", "fb-worker", "workflow-ops"):
        assert expected in comps, f"{expected} missing from the scale flow"
    assert any(s.get("condition") for s in steps), \
        "the scale flow branches on needsScaleDown; no step records a condition"
    assert any(s["via"] == "task" for s in steps) and \
           any(s["via"] == "grpc" for s in steps), \
        "the flow crosses mechanisms; that must be visible per step"


def test_one_flow_can_serve_many_task_names():
    """'the webhook flow is the same for all webhooks' has to be expressible ONCE.
    Copying a shape per task name is how this file would rot."""
    flows = TF.load()
    shared = [f for f in flows
              if len((f.get("applies_to") or {}).get("task_names") or []) > 1]
    assert shared, "no flow serves more than one task name - the shape is being copied"
    assert any(len((f.get("applies_to") or {}).get("task_names") or []) >= 5
               for f in shared)


def test_placeholders_resolve_from_the_routing_table():
    """`standard-subsystem-task` is written once with a HANDLER placeholder and resolved
    per task name, rather than duplicated ten times."""
    f = next(x for x in TF.load() if x["name"] == "standard-subsystem-task")
    assert any(s["to"] in TF.PLACEHOLDERS or s["from"] in TF.PLACEHOLDERS
               for s in f["steps"]), "no placeholder to resolve"
    steps = TF.resolve(f, "DEPLOYMENT_HEALTH_CHECK")
    assert not any(s["to"] in TF.PLACEHOLDERS or s["from"] in TF.PLACEHOLDERS
                   for s in steps), "a placeholder survived resolution"
    assert any(s["to"] == "monitoring-service" for s in steps), \
        "DEPLOYMENT_HEALTH_CHECK should resolve its handler to monitoring-service"


def test_a_flow_not_starting_at_interface_exists():
    """interface is a convention for externally-initiated work, not a chokepoint. If
    every authored flow started there the model would be quietly teaching the wrong
    thing."""
    starts = {f["steps"][0]["from"] for f in TF.load() if f.get("steps")}
    assert starts - {"interface"}, "every flow starts at interface - verify that is true"




def test_every_flow_step_fact_names_real_components():
    cat = C.load()
    for line in (KB / "graph" / "flow-facts.jsonl").open():
        f = json.loads(line)
        for side in ("from", "to"):
            assert f[side] in cat["Component"], f"flow step names unknown {f[side]!r}"


def test_every_routed_task_name_has_a_flow():
    """Completeness, as a TEST rather than as a gate.

    `task_flows.py check` reports an unauthored flow as a WARNING, deliberately: CI
    indexing must not break because the platform added a task name. But the knowledge
    base's own suite is where its gaps belong, so this is an assertion here. If the
    platform gains a taskName, this fails until someone traces the chain - which is the
    pressure that stops the file drifting back to partial coverage.
    """
    route = TF._routing()
    covered = TF._covered(TF.load())
    # A row with no known producer cannot be traced; task-names.yaml says so with
    # `produced_by: null` (e.g. SCALE_UP_WORKER), and that is recorded, not guessed.
    unknown = {n for n, r in route.items() if not r.get("produced_by")}
    missing = sorted(set(route) - set(covered) - unknown)
    assert not missing, (
        f"{len(missing)} routed task name(s) have no authored flow: {missing}\n"
        f"  Trace the chain and add an entry to "
        f"knowledge/_reference/joins/task-flows.yaml, then run ./e5 flows check.")


def test_deployment_action_is_not_filed_as_a_standard_task():
    """It was, and that was wrong. workflow-ops routes DEPLOYMENT_ACTION to an ASYNC
    STATE MACHINE with fourteen states that pauses apps, scales down fb workers and runs
    a Jenkins job - the opposite of the standard one-hop-and-reply shape. Filing it under
    `standard-subsystem-task` made the longest chain on the platform look like the
    shortest."""
    std = next(f for f in TF.load() if f["name"] == "standard-subsystem-task")
    names = (std.get("applies_to") or {}).get("task_names") or []
    assert "DEPLOYMENT_ACTION" not in names
    da = next(f for f in TF.load() if f["name"] == "deployment-action")
    comps = {c for s in da["steps"] for c in (s["from"], s["to"])}
    assert {"workflow-ops", "workflow-manager", "jenkins"} <= comps


def test_workflow_resource_provisioning_is_handled_and_replies():
    """WAS WRONG. This flow used to say workflow-ops drops the task (UNHANDLED). At
    workflow-ops release/v1.7.4 the poller routes it to async_service.handle
    (polling_service.py:133-135, async_service.py:111), which provisions and replies
    TaskComplete (resource_provisioning.py:207). Re-verified from the code 2026-11-14."""
    f = next(x for x in TF.load() if x["name"] == "workflow-resource-provisioning")
    steps = TF.resolve(f, "WORKFLOW_RESOURCE_PROVISIONING")
    assert any(s["to"] == "interface" and s["carries"] == "TaskComplete" for s in steps)
    assert "UNHANDLED" not in " ".join(str(s.get("carries")) for s in steps)

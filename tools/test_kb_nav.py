"""The navigation tools return what a document or an extracted fact says, or say nothing.

These pin the property that replaced word search (P0, 2026-11-14): exact names, aliases
that resolve to exactly one component, and a not-found answer that lists what IS there
instead of a nearest guess.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_nav as N


def test_overview_is_the_l0_document():
    r = N.overview()
    assert r["ok"] and r["path"] == "00-overview.md"
    assert "How to traverse" in r["text"]


def test_aliases_resolve_to_exactly_one_component():
    assert N.resolve("DSS") == "dynamic-scaling"
    assert N.resolve("dms") == "deployment-mgmt"
    assert N.resolve("workflow-ops") == "workflow-ops"
    for target in N.ALIASES.values():
        assert N.resolve(target) == target, f"alias target {target} is not a component"


def test_an_unknown_name_is_not_guessed():
    r = N.component("workflow-opz")
    assert not r["ok"] and "components" in r, "a near miss must list names, not pick one"


def test_component_answer_comes_from_catalog_and_extracted_facts():
    r = N.component("cds")
    assert r["ok"] and r["catalog"]["system"] == "workflow-interactions"
    assert "subsystems/cds.md" in r["documents"]
    calls = r["extracted"]["CallsComponent"]["items"]
    assert any(i.startswith("cds->deployment-mgmt") for i in calls)


def test_no_kafka_topic_wiring_is_returned():
    for comp in ("dynamic-scaling", "cds", "workflow-api", "webhook-service"):
        r = N.component(comp)
        for item in (r["extracted"] or {}).get("ConfigKey", {}).get("items", []):
            name = item.split()[0].lower()
            assert "kafka" not in name and "topic" not in name, f"{comp}: {item}"


def test_read_by_path_and_folder():
    assert N.read("subsystems/README.md")["ok"]
    assert N.read("knowledge/subsystems/workflow-ops")["ok"]           # prefix and .md optional
    f = N.read("app-development/")
    assert f["ok"] and "app-library/" in f["contents"]


def test_read_not_found_lists_the_folder_instead_of_guessing():
    r = N.read("app-development/app-libary")
    assert not r["ok"] and "app-library/" in r["contents"]


def test_read_cannot_escape_the_knowledge_tree():
    assert not N.read("../registry.yaml")["ok"]


def test_flow_by_flow_name_task_name_and_fb_name():
    assert N.flow("deployment-action")["flow"] == "deployment-action"
    assert N.flow("DEPLOYMENT_ACTION")["flow"] == "deployment-action"
    r = N.flow("PAUSE_RESUME_APP")                                   # an fbName
    assert r["ok"] and r["task"] == "PAUSE_OR_RESUME_APP"


def test_flow_not_found_lists_what_exists():
    r = N.flow("FbWorkerPodScaleDownX")
    assert not r["ok"] and r["flows"] and r["task_names"]


def test_contract_is_exact_and_says_declared_not_served():
    r = N.contract("e5.platform.ss.ToggleTrigger.v1.ToggleTrigger")
    assert r["ok"] and {d["component"] for d in r["declared"]} == {"workflow-ops"}
    assert "several repos copy the same proto" in r["note"]
    assert {s["component"] for s in N.contract("UpdateToggleStatus")["served_by"]} == {"workflow-ops"}
    assert not N.contract("ToggleTrig")["ok"], "a prefix must not match"


def test_dependencies_come_from_build_facts():
    r = N.dependencies("platform-core")
    arts = {d["artifact"] for d in r["depends_on"]}
    assert "e5-task-sdk-java" in arts
    assert any(u["repo"] == "variant-scaffolding" for u in r["used_by"])

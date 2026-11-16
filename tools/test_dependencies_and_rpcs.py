"""Pins answers that were verified against the code on 2026-11-14 (evals/reverse-test-*).

If an extractor regresses, these fail with the verified answer in the message."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kb_nav as N
KB = HERE.parent


def _served_called(rpc):
    r = N.contract(rpc)
    return ({x["component"] for x in r.get("served_by") or []},
            {x["component"] for x in r.get("called_by") or []})


def test_rpc_servers_and_callers_match_the_code():
    assert _served_called("DisableDeployment") == ({"workflow-ops"}, {"workflow-manager"})
    s, c = _served_called("UpdateFbWorkerPodDetails")
    assert s == {"deployment-mgmt"} and {"cds", "workflow-manager"} <= c
    s, c = _served_called("CreateDssTask")
    assert s == {"deployment-mgmt"} and "dynamic-scaling" in c
    s, _ = _served_called("SendEvent")
    assert s == {"event-store"}


def test_called_but_unserved_rpcs_are_reported():
    for rpc in ("GetFailedEventsWebhooksList", "GetNotStartedWorkflows"):
        assert N.contract(rpc)["unserved"], rpc


def test_wrapper_dataflow_finds_the_akst_poll():
    """workflow-ops polls AKST via __check_for("getTask") -> client.call(method)."""
    rf = [json.loads(l) for l in (KB / "graph" / "rpc-facts.jsonl").open()]
    got = {f["rpc"] for f in rf if f["repo"] == "workflow-ops" and f["service"] == "TaskService"}
    assert {"isTaskAvailable", "getTask"} <= got


def test_library_consumers_include_customer_repos_and_behind_flags():
    r = N.dependencies("platform-core")
    outside = [u for u in r["used_by"] if u["outside_platform"]]
    assert len(outside) >= 20, "customer workflow repos are not being found"
    assert any(u["behind"] for u in outside)
    r = N.dependencies("task-sdk-java")
    assert any(u["repo"] == "credential-monitor" and u["behind"] for u in r["used_by"])
    assert {i["repo"] for i in r["inherited_by"] or []} >= {"variant-scaffolding"}


def test_no_internal_dependency_is_explicit_not_silent():
    assert "no internal E5 library dependency" in (N.dependencies("workflow-ops")["note"] or "")


def test_fb_worker_apps_are_built_from_the_base_image():
    users = N.dependencies("rf-fb-base-image")["image_used_by"] or []
    assert len(users) >= 3 and all(u["tag"] for u in users)


def test_library_source_repos_are_registered():
    import kb_core as K
    ids = {r["id"] for r in K._registry_repos()}
    assert {"task-sdk-java", "app-sdk", "state-service", "task-sdk-python",
            "non-rpa-worker-core"} <= ids


def test_each_graphql_operation_maps_to_the_task_it_sends():
    """Verified in interface release/v1.7.6 on 2026-11-14 (TaskServiceImpl / *TaskService)."""
    r = N.flow("PAUSE_OR_RESUME_APP")
    assert [g["graphql"] for g in r["triggered_by_graphql"]] == ["Mutation.pauseOrResumeApp"]
    rows = [json.loads(l) for l in (KB / "graph" / "gql-task-facts.jsonl").open()]
    by = {}
    for f in rows:
        by.setdefault(f["graphql"], set()).add(f["task_name"])
    assert by["Mutation.releaseCredential"] == {"CREDENTIAL_RELEASE"}
    assert by["Mutation.performDeployment"] == {"DEPLOYMENT_ACTION"}
    assert all(len(v) == 1 for v in by.values()), "an operation maps to several tasks again"
    assert any(t.startswith("<dynamic") for t in by["Mutation.terminateWorkflow"])

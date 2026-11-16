"""Tests that check the GENERATED facts against the code are skipped until `./e5 sync all`
has produced them (a fresh clone has none; graph/ is git-ignored). After a sync they run."""
import pathlib
import pytest

KB = pathlib.Path(__file__).resolve().parent.parent
DATA_TESTS = {"test_artifacts_codegraph", "test_async_graph", "test_build_graph",
              "test_card_provenance", "test_check_compiles", "test_check_plumbing",
              "test_citations", "test_dependencies_and_rpcs", "test_estate", "test_kb_nav",
              "test_no_injected_values", "test_plugin_surface", "test_task_flows"}


# Tests that read a file this distribution does not ship (the maintainers' Jenkins file, a
# committed estate snapshot, files only the maintainers' tooling writes): skipped when absent.
NEEDS = {
    "test_ci_stage_calls_the_gate_and_splits_the_exit_codes": "ci/jenkins-kb-index.groovy",
    "test_it_is_a_blocking_ci_stage": "ci/jenkins-kb-index.groovy",
    "test_the_gate_runs_in_e5_check_and_in_ci": "ci/jenkins-kb-index.groovy",
    "test_it_runs_in_ci": "ci/jenkins-kb-index.groovy",
    "test_the_committed_snapshot_is_smaller_than_the_facts_it_summarises": "graph/estate-snapshot.json.gz",
    "test_the_committed_snapshot_carries_no_volatile_field_in_a_shape": "graph/estate-snapshot.json.gz",
    "test_the_snapshot_round_trips_against_the_estate_it_was_taken_from": "graph/estate-snapshot.json.gz",
    "test_every_flow_step_fact_names_real_components": "graph/flow-facts.jsonl",
}


def pytest_collection_modifyitems(config, items):
    no_facts = not (KB / "graph" / "code-facts.jsonl").is_file()
    for it in items:
        if no_facts and pathlib.Path(str(it.fspath)).stem in DATA_TESTS:
            it.add_marker(pytest.mark.skip(
                reason="no generated facts yet: run ./e5 sync all, then re-run"))
        need = NEEDS.get(it.name)
        if need and not (KB / need).exists():
            it.add_marker(pytest.mark.skip(reason=f"{need} is not part of this distribution"))

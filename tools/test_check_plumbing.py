"""Tests for the dead-integration-point gate.

The gate exists because three instances of one bug shape shipped and survived:

  * `queue_served_by` read `graph/vq-bindings.json`, which no tool wrote, and a
    `ClusterQueueBinding` fact kind, which no extractor emits. It resolved 1 of 6 queues
    and reported that as a property of the platform.
  * `graph_store.py stats` counted a hand-kept list and reported 6 node labels for a
    14-label graph.
  * `build_graph.py` set `"artifact": None` as a literal, so the publisher half of every
    coordinate was absent.

In every case a consumer and a producer never met, and the consumer had no way to say so.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_plumbing as X            # noqa: E402

KB = HERE.parent


def run(extra_src: str = "", in_file: str = "kb_core.py"):
    """Run the gate, optionally with a line spliced into a real module. Restores it."""
    target = HERE / in_file
    original = target.read_text()
    if extra_src:
        target.write_text(original + "\n" + extra_src + "\n")
    try:
        r = subprocess.run([sys.executable, str(HERE / "check_plumbing.py")],
                           capture_output=True, text=True, cwd=str(KB))
        return r.returncode, r.stdout
    finally:
        target.write_text(original)


def test_the_repository_has_no_dead_integration_point():
    rc, out = run()
    assert rc == 0, out


def test_it_catches_a_graph_artifact_nothing_writes():
    """The vq-bindings.json case, which cost QUEUE_SERVED_BY five of its six queues."""
    rc, out = run('_GHOST = KB / "graph" / "nothing-writes-this.json"')
    assert rc == 1
    assert "nothing-writes-this.json" in out and "nothing in this repository writes it" in out


def test_it_catches_a_fact_kind_no_extractor_emits():
    """The ClusterQueueBinding case."""
    rc, out = run('def _ghost(f):\n    return f["kind"] == "GhostBinding"')
    assert rc == 1 and "GhostBinding" in out


def test_it_catches_the_literal_graph_path_spelling_too():
    """Three spellings are in use - `GRAPH / "x"`, `KB / "graph" / "x"` and `"graph/x"`.
    The first version matched two of them, which is the same silent miss it exists for."""
    rc, out = run('_GHOST2 = Path("graph/also-nothing.jsonl")')
    assert rc == 1 and "also-nothing.jsonl" in out


def test_a_kind_compared_on_an_attribute_is_not_a_fact_kind():
    """A bare `kind == "owns"` matches capability.py's own Claim attribute and
    console/kb.py's display categories. Six false positives, and a gate that blocks the
    build on six false positives is switched off the same day."""
    compared = X.compared_kinds(X.sources())
    for not_a_fact in ("owns", "excludes", "column", "rpc"):
        assert not_a_fact not in compared


def test_the_gate_does_not_scan_itself():
    """Its own regexes and the comment explaining them contain `GRAPH / "x"`, so the
    first run reported `graph/x` as read by nothing."""
    assert "tools/check_plumbing.py" not in X.sources()


def test_every_expected_absence_carries_a_reason():
    """An exception list with no reasons is where dead code goes to hide."""
    for artifact, why in X.EXPECTED.items():
        assert len(why) > 25, f"{artifact} has no real reason: {why!r}"


def test_the_gate_is_blocking_in_e5_check():
    """Unlike staleness, drift and ownership, this one is wrong on a clean checkout."""
    s = (KB / "e5").read_text()
    assert '("plumbing", "check_plumbing.py")' in s
    m = re.search(r'if r and name not in \(([^)]*)\)', s, re.S)
    assert m and "plumbing" not in m.group(1), (
        "plumbing is in the advisory list - a dead reference is a defect here, not drift")


def test_it_is_a_blocking_ci_stage():
    g = (KB / "ci" / "jenkins-kb-index.groovy").read_text()
    assert "check_plumbing.py" in g
    # from the INVOCATION, not from the comment banner that explains it
    seg = g[g.rindex("python3 tools/check_plumbing.py"):]
    assert "error(" in seg[:500], "the CI stage does not fail the build"
    assert "unstable(" not in seg[:500], "the CI stage only marks the build unstable"


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

#!/usr/bin/env python3
"""Tests for the contract-artifact extractor, the Tier 1 code layer and the Neo4j export.

These are offline: they assert over the committed fact files and the catalog, not over
JFrog or a running database. A test that needs a network call to pass is a test that gets
skipped in CI and then deleted.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog as C
import codegraph as CG

KB = Path(__file__).resolve().parent.parent
ARTIFACT_FACTS = KB / "graph" / "artifact-facts.jsonl"
CODE_FACTS = KB / "graph" / "code-facts.jsonl"


def _load(p):
    return [json.loads(l) for l in p.open()] if p.is_file() else []


# --------------------------------------------------------------- artifacts
def _same_commit(repo, a, b):
    """Two release branches at one commit are the same code (DMS v1.9.8 == v1.9.9)."""
    import subprocess, os
    gd = os.path.expanduser(f"~/.e5/cache/{repo}.git")
    rev = lambda br: subprocess.run(["git", "-C", gd, "rev-parse", f"refs/heads/{br}"],
                                    capture_output=True, text=True).stdout.strip()
    return bool(rev(a)) and rev(a) == rev(b)










# --------------------------------------------------------------- code graph
def test_deployment_mgmt_is_a_pure_sink():
    """Measured, and load-bearing for impact analysis: it serves ~124 RPCs, owns every
    table, and calls nobody. If an outbound edge appears, either the architecture changed
    or the edge detector is producing false positives."""
    calls = [f for f in _load(CODE_FACTS) if f["kind"] == "CallsComponent"]
    out = [f for f in calls if f["component"] == "deployment-mgmt"]
    assert not out, f"deployment-mgmt gained outbound calls: {[f['name'] for f in out]}"


def test_edges_name_only_real_components():
    """An edge to a name that is not a Component is a mapping bug in TARGET_ALIASES."""
    known = set(C.load()["Component"])
    bad = [f["name"] for f in _load(CODE_FACTS)
           if f["kind"] == "CallsComponent" and f["props"].get("target") not in known]
    assert not bad, f"edges pointing at unknown Components: {bad[:5]}"


def test_entry_points_cover_each_mechanism():
    """Four detectors exist because this estate has four genuinely different mechanisms.
    If one yields nothing, that detector has silently stopped matching."""
    eps = [f for f in _load(CODE_FACTS) if f["kind"] == "EntryPoint"]
    mechs = {f["props"].get("mechanism") for f in eps}
    for want in ("graphql", "grpc", "kafka", "task"):
        assert want in mechs, (
            f"no {want} entry points found at all - that detector has regressed")


def test_curated_joins_parse_and_resolve():
    """The three hand-curated joins are judgement, not parse output - but they must still
    reference Components that exist, or they join to nothing."""
    known = set(C.load()["Component"])
    rows = CG._load_join("task-names")
    assert rows, "task-names join is empty or failed to parse"
    bad = []
    for r in rows:
        for side in ("produced_by", "handled_by"):
            if r.get(side) and r[side] not in known:
                bad.append(f"{r.get('task_name')}.{side}={r[side]}")
    assert not bad, f"curated join references unknown Components: {bad}"


def test_task_routing_join_is_not_thinner_than_the_code():
    """interface declares ten task routes; the extractor finds nine because one `case`
    label is a constant rather than a literal. The join must carry at least the ten."""
    rows = CG._load_join("task-names")
    from_interface = [r for r in rows if r.get("produced_by") == "interface"]
    assert len(from_interface) >= 10, (
        f"only {len(from_interface)} interface-produced task names curated; the two "
        f"switches in TaskManagerHelperImpl/TaskManagerProtocolBuilder declare ten")


# --------------------------------------------------------------- neo4j projection






# --------------------------------------------------------------- tier 2 layer trace
LAYER_FACTS = KB / "graph" / "layer-facts.jsonl"


def test_layer_map_parses_in_the_minyaml_subset():
    """The layer map once used `- { name: x, paths: [...] }`, a NESTED flow collection,
    which minyaml documents as unsupported. It parsed into `{'{ name': 'entry, paths:...'}`
    and every class silently came back unlabelled. Block style only."""
    import scip_pilot as SP
    for comp in ("deployment-mgmt", "interface", "workflow-ops", "workflow-manager"):
        layers = SP.layer_map(comp)
        assert layers, f"{comp} has no layer map"
        for L in layers:
            assert L.get("name"), f"{comp}: a layer with no name - flow-map parse failure"
            assert L.get("paths"), f"{comp}/{L.get('name')}: no paths"


def test_tier2_measures_the_documented_request_path():
    """deployment-mgmt is documented as RPC -> UseCase -> RepositoryFactory -> impl.
    Bytecode must agree, or the layer map is describing something that is not there."""
    if not LAYER_FACTS.is_file():
        return                           # pilot not run in this environment
    import collections
    facts = [json.loads(l) for l in LAYER_FACTS.open()]
    lt = collections.Counter()
    for f in facts:
        # Scoped to the component the assertion is about. Tier 2 is estate-wide now, and
        # `entry -> usecase` is deployment-mgmt's documented chain, not everyone's.
        if f["kind"] != "CodeCalls" or f["component"] != "deployment-mgmt":
            continue
        p = f["props"]
        lt[(p["from_layer"], p["to_layer"])] += 1
    if not lt:
        return                       # deployment-mgmt not in this run
    for a, b in [("entry", "usecase"), ("usecase", "repo-factory")]:
        assert lt[(a, b)] > 50, (
            f"only {lt[(a, b)]} {a}->{b} edges; the documented request path is not "
            f"visible in the bytecode")


def test_a_layer_map_still_covers_what_its_confidence_claims():
    """`unlabelled` must mean the map is incomplete, not be the default outcome - but how
    much coverage a map CLAIMS is what its declared `confidence` says.

    Measured 2026-10-29, estate-wide:

        deployment-mgmt   838 classes    1% unlabelled   confidence: high
        interface         342 classes   93% unlabelled   confidence: low
        workflow-manager  605 classes   96% unlabelled   confidence: low

    That is not drift. `deployment-mgmt`'s map has `entity`, `dto`, `mapper`, `support`
    and `generated-proto` layers and genuinely classifies the repo; the other two declare
    only the request path - entry, service, client - and their own notes in
    layer-maps.yaml say to treat their labels as advisory. A single 10% threshold was
    calibrated when deployment-mgmt was the only indexed repo, and applying it estate-wide
    reports two honestly partial maps as broken ones.

    So: a `high`-confidence map must still cover its repo, and a `low`/`medium` one must
    still label SOMETHING - a map that has stopped matching entirely is drift at any
    confidence. The threshold is read from the YAML, so changing a map's claim means
    changing its declared confidence, in the file, deliberately.
    """
    if not LAYER_FACTS.is_file():
        return
    import collections
    import scip_pilot as SP
    tot, unl = collections.Counter(), collections.Counter()
    for line in LAYER_FACTS.open():
        u = json.loads(line)
        if u["kind"] != "CodeUnit" or not SP.layer_map(u["component"]):
            continue
        tot[u["component"]] += 1
        if u["props"]["layer"] == "unlabelled":
            unl[u["component"]] += 1
    if not tot:
        return
    for comp, n in tot.items():
        pct = 100 * unl[comp] / n
        conf = SP.layer_confidence(comp)
        if conf == "high":
            assert pct < 10, (
                f"{comp} declares confidence=high but {pct:.0f}% of its {n} classes are "
                f"unlabelled - the map has stopped covering its repo")
        else:
            assert unl[comp] < n, (
                f"{comp}'s layer map matches NOTHING ({n} classes, all unlabelled) - the "
                f"packages it names have moved or been renamed")


def test_tier2_never_silently_invents_a_layer():
    """A class in a component with no declared map is `undeclared`, never a guess.

    Package names were measured unreliable - `interface`'s `grpc/impl/` holds GraphQL
    implementations - so inferring is worse than admitting. The two words must also not be
    confused: `unlabelled` can only appear where a map exists, `undeclared` only where
    none does.
    """
    if not LAYER_FACTS.is_file():
        return
    import scip_pilot as SP
    units = [json.loads(l) for l in LAYER_FACTS.open() if json.loads(l)["kind"] == "CodeUnit"]
    for u in units:
        has_map = bool(SP.layer_map(u["component"]))
        layer = u["props"]["layer"]
        if has_map:
            assert layer != "undeclared", (
                f"{u['component']} declares a layer map but {u['name']} is `undeclared`")
        else:
            assert layer == "undeclared", (
                f"{u['component']} has no layer map, yet {u['name']} is labelled "
                f"{layer!r} - a layer was inferred")


def test_tier2_coverage_is_measured_against_a_reachable_denominator():
    """41 Components is the catalog; Tier 2 can only ever reach the JVM-backed ones.

    Quoting "1 of 41" counted 11 Components with no repo at all and 7 that are Python,
    C#, YAML or a Dockerfile against a tier that compiles Java. The denominator has to be
    the reachable set or the number says nothing about progress.
    """
    import scip_pilot as SP
    cv = SP.coverage()
    assert cv["jvm_eligible"] < cv["catalog"], "every Component cannot be JVM-backed"
    assert cv["no_repo"] + cv["not_jvm"] + cv["jvm_eligible"] == cv["catalog"], (
        f"the three buckets do not partition the catalog: {cv}")
    if LAYER_FACTS.is_file():
        assert cv["covered"] <= cv["jvm_eligible"], (
            "a covered Component is not backed by a JVM repo - attribution is wrong")


def test_tier2_facts_state_their_limit():
    if not LAYER_FACTS.is_file():
        return
    facts = [json.loads(l) for l in LAYER_FACTS.open()]
    assert all(f["provenance"] == "bytecode" for f in facts)
    units = [f for f in facts if f["kind"] == "CodeUnit"]
    assert units and all(f["props"].get("limits") for f in units), (
        "bytecode facts must record that they carry no source positions")




def test_one_repo_can_back_several_components():
    """platform-core is ONE repo and SEVEN Components. Attribution must split it.

    This is the thing a repo-keyed model cannot express and the reason the catalog exists.
    If every class in platform-core were attributed to the Component literally named
    `platform-core`, the six path-scoped roles would each show an empty call graph while
    the repo showed a large one.
    """
    import scip_pilot as SP
    comps = SP.components_in("platform-core")
    assert len(comps) >= 7, f"expected 7 Components in platform-core, got {len(comps)}"
    cases = {
        "src/main/java/com/e5/platform/core/wre/engine/worker/Poller.java": "wf-worker",
        "src/main/java/com/e5/platform/core/wre/engine/Engine.java": "wf-engine",
        "src/main/java/com/e5/platform/core/trigger/Cron.java": "wf-trigger",
        "src/main/java/com/e5/platform/core/akst/Client.java": "akst",
        "src/main/java/com/e5/platform/core/rpagateway/Gw.java": "rpa-gateway",
        "src/main/java/com/e5/platform/core/event/receiver/Recv.java": "wf-aggregator",
    }
    for path, want in cases.items():
        got = SP._component_of(path, comps)
        assert got == want, f"{path} attributed to {got!r}, expected {want!r}"


def test_the_deepest_glob_wins_not_the_first():
    """`wre/engine/worker/**` must beat `wre/engine/**`.

    wf-worker's package sits INSIDE wf-engine's. Dict order decides nothing here; glob
    specificity does, the same rule `_layer_of` uses. First-match-wins would hand every
    worker class to wf-engine.
    """
    import scip_pilot as SP
    comps = SP.components_in("platform-core")
    path = "src/main/java/com/e5/platform/core/wre/engine/worker/Poller.java"
    assert SP._component_of(path, comps) == "wf-worker"
    # and the reverse ordering must not change the answer
    assert SP._component_of(path, list(reversed(comps))) == "wf-worker"


def test_a_pathless_component_claims_only_the_remainder():
    """platform-core declares no `source.paths`, so it gets what the roles do not."""
    import scip_pilot as SP
    comps = SP.components_in("platform-core")
    assert SP._component_of("src/main/java/com/e5/platform/core/util/Strings.java",
                            comps) == "platform-core"


def test_class_roots_cover_every_build_layout(tmp_path):
    """Gradle, Maven and multi-module. The pilot knew one of the three.

    Knowing only `build/classes/java/main` is why the three Maven repos reported "no
    compiled classes" - a sentence about Gradle printed about a Maven build.
    """
    import scip_pilot as SP
    for layout in ("build/classes/java/main", "build/classes/main", "target/classes",
                   "mod-a/target/classes", "mod-b/build/classes/java/main"):
        tree = tmp_path / layout.replace("/", "_")
        d = tree / layout
        d.mkdir(parents=True)
        (d / "X.class").write_bytes(b"\xca\xfe\xba\xbe")
        roots = SP._class_roots(tree)
        assert roots, f"{layout} not found"
        assert SP._src_prefix(roots[0], tree).endswith("src/main/java/"), layout
    # an empty class dir is not a build
    bare = tmp_path / "empty"
    (bare / "target" / "classes").mkdir(parents=True)
    assert SP._class_roots(bare) == []


def test_maven_is_found_outside_the_path():
    """apt will not install Maven without root, so `which mvn` is not the whole story.

    This exact gap read as "three repos need JDK 21 and the host runs 17" - the JDK was
    present and detected all along; the build tool was missing. Mirrors the `~/.jdks`
    lesson already encoded in _host_jdks.
    """
    import check_compiles as CC
    assert Path.home() / ".e5" / "tools" / "maven" in CC._MVN_DIRS, (
        "the no-root install location must be searched")
    import os
    saved = os.environ.pop("E5_MVN", None)
    try:
        got = CC._host_maven()
    finally:
        if saved:
            os.environ["E5_MVN"] = saved
    if got is None:
        return                       # no Maven installed in this environment
    assert Path(got).name == "mvn" and os.access(got, os.X_OK)


def test_class_declaration_regex_covers_every_modifier_javap_prints():
    """A missed declaration does not lose a class - it MISATTRIBUTES its edges.

    `cur` keeps pointing at the previous class, so every method reference in the unmatched
    class is recorded as coming from its neighbour. Wrong edges, not absent ones. The
    original pattern hand-listed `public|final|abstract|static` and so missed `sealed`,
    `non-sealed`, `protected`, `private` and `strictfp`.
    """
    import scip_pilot as SP
    cases = {
        "public class A": "A", "public abstract sealed class B<T> extends C": "B",
        "non-sealed class D": "D", "public interface E": "E", "final enum F": "F",
        "public record G(int x)": "G", "protected static final class H": "H",
        "public strictfp class I": "I", "class J": "J",
    }
    for line, want in cases.items():
        m = SP._CLASSDECL.match(line)
        assert m and m.group(1) == want, f"{line!r} -> {m.group(1) if m else None}"
    # and the block reset exists, so an unmatched declaration cannot leak into its neighbour
    assert SP._CLASSHEAD.match('Compiled from "Foo.java"')
    assert not SP._CLASSHEAD.match("public class Foo")


def test_a_failed_build_is_never_reused_as_a_cache_hit():
    """javac writes classes right up until it stops, so class files are not proof.

    deployment-mgmt's `release/v1.8.3` tree held 683 of its 838 classes after the compile
    that fails, and the estate pass reported `ok  release/v1.8.3  (cached)` for it. The
    marker file is written only on a zero exit, so a partial tree is rebuilt rather than
    read.
    """
    import scip_pilot as SP
    assert SP._OK_MARK.startswith("."), "the marker must not look like source"
    src = Path(SP.__file__).read_text()
    i_check = src.index("if (wt / _OK_MARK).is_file()")
    i_write = src.index("(wt / _OK_MARK).write_text(r)")
    i_rc = src.index("if rc == 0 and _class_roots(wt):")
    assert i_rc < i_write, "the marker must be written only inside the rc == 0 branch"
    assert i_check < i_rc, "the cache check must precede the compile"


def test_tier2_facts_name_the_ref_they_were_built_from():
    """A trace from a branch other than the indexed one must say so on every fact.

    deployment-mgmt's indexed branch does not compile, so its facts come from v1.8.2. That
    is usable; presenting it as v1.8.3 would not be.
    """
    if not LAYER_FACTS.is_file():
        return
    import sys as _s
    _s.path.insert(0, str(KB / "tools"))
    import workspace as ws
    _, repos = ws.registry()
    facts = [json.loads(l) for l in LAYER_FACTS.open()]
    assert facts and all(f.get("branch") for f in facts), "a fact with no branch"
    off = {}
    for f in facts:
        want = repos.get(f["repo"], {}).get("branch")
        if want and f["branch"] != want and not _same_commit(f["repo"], f["branch"], want):
            off.setdefault(f["repo"], (f["branch"], want))
    for repo, (got, want) in off.items():
        card = KB / "disagreements" / "dms-latest-release-does-not-compile.md"
        assert repo == "deployment-mgmt" and card.is_file(), (
            f"{repo} tier 2 was built from {got}, the KB indexes {want}, and no "
            f"disagreement card explains why")




def test_invokes_never_crosses_a_repo_boundary():
    """Tier 2 is INTRA-repo by construction. Tier 1 owns cross-service edges.

    An INVOKES edge joining two repos would mean the bytecode reader resolved a reference
    into a dependency jar, which `javap -c` cannot do - so such an edge is a bug in
    attribution, not a discovery.
    """
    if not LAYER_FACTS.is_file():
        return
    facts = [json.loads(l) for l in LAYER_FACTS.open()]
    repo_of = {}
    for f in facts:
        if f["kind"] == "CodeUnit":
            repo_of.setdefault(f["name"], set()).add(f["repo"])
    for f in facts:
        if f["kind"] != "CodeCalls":
            continue
        a, b = f["name"].split("->")
        assert f["repo"] in repo_of.get(b, {f["repo"]}), (
            f"{f['name']} leaves {f['repo']} - tier 2 does not resolve into other repos")


# --------------------------------------------------------------- neo4j projection ordering


def test_every_invokes_endpoint_has_a_node():
    """An edge naming a class with no CodeUnit can never match."""
    import json as _j
    lf = KB / "graph" / "layer-facts.jsonl"
    if not lf.is_file():
        return
    facts = [_j.loads(l) for l in lf.open()]
    units = {f["name"] for f in facts if f["kind"] == "CodeUnit"}
    orphan = set()
    for f in facts:
        if f["kind"] != "CodeCalls":
            continue
        for end in f["name"].split("->"):
            if end not in units:
                orphan.add(end)
    assert not orphan, (
        f"{len(orphan)} edge endpoints have no CodeUnit node (javap renders interfaces "
        f"as `interface X`, not `class X`): {sorted(orphan)[:3]}")




if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn(); print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            bad += 1; print(f"FAIL  {fn.__name__}\n      {e}")
        except Exception:
            bad += 1; print(f"ERROR {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)

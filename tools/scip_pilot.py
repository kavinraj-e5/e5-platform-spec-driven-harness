#!/usr/bin/env python3
"""Tier 2: the intra-service layer trace. Estate-wide over every JVM repo.

  python3 tools/scip_pilot.py all                      every JVM repo, one pass
  python3 tools/scip_pilot.py build  --component deployment-mgmt [--ref release/v1.8.2]
  python3 tools/scip_pilot.py index  --component deployment-mgmt
  python3 tools/scip_pilot.py trace  getConfig
  python3 tools/scip_pilot.py status

IT WAS A PILOT, AND THE PILOT WAS ONE REPO
------------------------------------------
deployment-mgmt was chosen on measured grounds: no private dependencies (its
`artifactory{}` block is commented out, so nothing needs JFrog), Java 21, and the most
mechanical layering in the estate - RPC -> `<Rpc>UseCase` -> repository interface -> single
impl, with no DI framework at all. That made it the cheapest place to find out whether
bytecode carries a usable layer trace. It does.

Generalising it took no new idea and three fixes, all of them toolchain rather than
analysis:

  * `./gradlew` was hardcoded, so every Maven repo reported "no compiled classes"
    - a build-system gap read as an absent call graph;
  * `mvn` was looked up with `shutil.which` only, and apt will not install Maven without
    root, so a host that HAS one reported NO_MAVEN. Toolchain resolution now lives in
    tools/check_compiles.py and is imported, not duplicated - two resolvers that drift is
    how a repo gets reported unbuildable while it builds fine by hand;
  * one class layout was assumed. Gradle writes `build/classes/java/main`, Maven writes
    `target/classes`, and a multi-module build writes one per module.

TWO BACKENDS, AND WHY THE FALLBACK IS THE ONE THAT RUNS
-------------------------------------------------------
`scip-java` is the right tool and is preferred when present. It is not installed here and
pulling it needs coursier, so the fallback does the work: **`javap -c` over the compiled
classes**. Bytecode carries every method reference in the constant pool, which is exactly
the edge set needed for "entry point -> layer -> layer -> outbound call". It needs only a JDK,
which the build already requires.

What the fallback gives up versus SCIP: no column-accurate positions, no cross-repo symbol
identity, and no resolution into dependency jars. For an intra-service layer trace none of
those matter. For cross-repo work, Tier 1 (tools/codegraph.py) already covers it from
static literals.

LAYERS ARE DECLARED, COVERAGE IS NOT INVENTED
---------------------------------------------
Four components have a layer map in knowledge/_reference/joins/layer-maps.yaml. The rest
do not, and this tool will not guess one - package names were measured unreliable
(`interface`'s `grpc/impl/` holds GraphQL implementations). A class in a component with no
declared map is labelled **`undeclared`**, which is a different fact from `unlabelled`:
`unlabelled` means someone wrote a map and it has a hole, `undeclared` means nobody has
written one yet. The call graph is real either way; only the layer names are missing.

BOTH BACKENDS NEED A COMPILE
----------------------------
Which is where this still bites: `deployment-mgmt release/v1.8.3` - the branch the KB
indexes - **does not compile**. See disagreements/dms-latest-release-does-not-compile.md.
Candidate refs are therefore the indexed branch FIRST, then release branches newest-first,
and the ref that actually built is stamped on every fact. A trace from a branch other than
the indexed one is still useful; silently pretending it came from the indexed one is not.
"""
from __future__ import annotations
import argparse, fnmatch, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "release"))
import catalog as C
import check_compiles as CC
import mirror as M
import minyaml
import workspace as ws

KB = HERE.parent
WORK = Path.home() / ".e5" / "scip"
GRAPH = KB / "graph"
LAYER_FACTS = GRAPH / "layer-facts.jsonl"
JOINS = KB / "extract" / "config"


# --------------------------------------------------------------------------- layer map
def layer_map(component: str) -> list[dict]:
    """Declared, never inferred. Package names were measured unreliable: `interface`'s
    `grpc/impl/` package holds GraphQL implementations, so a name-based classifier puts
    eight classes in the wrong layer and calls them gRPC entry points."""
    f = JOINS / "layer-maps.yaml"
    if not f.is_file():
        return []
    for row in (minyaml.load(f.read_text()).get("rows") or []):
        if row.get("component") == component:
            return row.get("layers") or []
    return []


def layer_confidence(component: str) -> str:
    f = JOINS / "layer-maps.yaml"
    for row in (minyaml.load(f.read_text()).get("rows") or []):
        if row.get("component") == component:
            return row.get("confidence", "unknown")
    return "undeclared"


def _layer_of(src_path: str, layers: list[dict]) -> str | None:
    import fnmatch
    best, name = -1, None
    for L in layers:
        for g in L.get("paths") or []:
            pat = g.replace("/**", "/*")
            if (src_path.startswith(g.replace("/**", "/"))
                    or fnmatch.fnmatch(src_path, g) or fnmatch.fnmatch(src_path, pat)):
                d = len([s for s in g.replace("/**", "").split("/") if s and "*" not in s])
                if d > best:
                    best, name = d, L["name"]
    return name


# --------------------------------------------------------------------- estate shape
JVM_BUILDS = ("gradle", "maven")


def jvm_repos() -> dict[str, dict]:
    """Every registered repo this tier can reach, keyed on the registry's DECLARED
    language/build - the same rule tools/check_compiles.py uses for applicability. A new
    JVM repo joins the estate by being declared, not by being guessed at."""
    _, repos = ws.registry()
    return {r: m for r, m in repos.items()
            if m.get("language") == "java" and m.get("build") in JVM_BUILDS}


def _depth(glob: str) -> int:
    return len([s for s in glob.replace("/**", "").split("/") if s and "*" not in s])


def components_in(repo: str) -> list[tuple[str, list[str]]]:
    """(component, source.paths) for every catalog Component backed by `repo`.

    One repo is not one component. `platform-core` backs SEVEN - six path-scoped roles
    plus the repo-wide one - and attributing all of its classes to a single name would
    erase the distinction the catalog exists to make.
    """
    out = []
    for name, c in C.load()["Component"].items():
        src = c.get("source") or {}
        if src.get("repo") == repo:
            out.append((name, list(src.get("paths") or [])))
    return out


def _component_of(src_path: str, comps: list[tuple[str, list[str]]]) -> str | None:
    """Deepest matching glob wins; a Component with no `paths` claims the remainder.

    Same precedence rule as `_layer_of`, for the same reason: `platform-core` declares no
    paths and the six roles inside it declare deep ones, so without "most specific first"
    every class would land on the repo-wide Component.
    """
    best, name, fallback = -1, None, None
    for comp, globs in comps:
        if not globs:
            fallback = fallback or comp
            continue
        for g in globs:
            pat = g.replace("/**", "/*")
            if (src_path.startswith(g.replace("/**", "/"))
                    or fnmatch.fnmatch(src_path, g) or fnmatch.fnmatch(src_path, pat)):
                d = _depth(g)
                if d > best:
                    best, name = d, comp
    return name or fallback


# --------------------------------------------------------------------------- build
def _ref_candidates(repo: str, indexed: str = "") -> list[str]:
    """The indexed branch first, then release branches newest-first.

    The indexed branch leads deliberately: a Tier 2 trace should describe the tree the
    rest of the KB describes. Falling back is allowed - deployment-mgmt has to - but it
    is a second choice and gets recorded as one.
    """
    bare = M.CACHE / f"{repo}.git"
    r = subprocess.run(["git", "-C", str(bare), "for-each-ref",
                        "--format=%(refname:short)", "refs/heads/release/*"],
                       capture_output=True, text=True)
    rel = sorted([b for b in r.stdout.splitlines() if M.vkey(b)], key=M.vkey, reverse=True)
    head = [indexed] if indexed else []
    return head + [b for b in rel if b != indexed]


_CLASS_DIRS = ("build/classes/java/main", "build/classes/main", "target/classes")
# Written only after a zero-exit compile. Its absence means "do not trust these classes".
_OK_MARK = ".e5-build-ok"


def _class_roots(tree: Path) -> list[Path]:
    """Where the compiler put the classes. Three layouts, and multi-module builds.

    The pilot knew only `build/classes/java/main`, so the three Maven repos reported "no
    compiled classes under .../build/classes" - a sentence about Gradle, printed about a
    Maven build. The absence was in the lookup, not in the repo.
    """
    out = [tree / d for d in _CLASS_DIRS]
    out = [p for p in out if p.is_dir() and any(p.rglob("*.class"))]
    if out:
        return out
    for d in _CLASS_DIRS:                                   # one level of subprojects
        for p in sorted(tree.glob(f"*/{d}")):
            if p.is_dir() and any(p.rglob("*.class")):
                out.append(p)
    return out


def _src_prefix(root: Path, tree: Path) -> str:
    """`<module>/src/main/java/` for a class root, so the declared globs can be applied.

    Bytecode knows its package, not its path, and both the layer map and the catalog's
    `source.paths` are expressed as paths. Reconstructing the path is what lets a
    `com.e5.platform.core.wre.engine.X` class be attributed to `wf-engine`.
    """
    rel = root.relative_to(tree).as_posix()
    mod = rel.split("build/classes")[0].split("target/classes")[0]
    return f"{mod}src/main/java/"


def _compile(tree: Path, build_system: str) -> tuple[int, str, str]:
    """Compile MAIN sources only, through check_compiles.py's resolver.

    Importing that resolver rather than re-deriving one is the point: it already knows
    that Gradle resolves its own toolchain (forcing JAVA_HOME broke builds that were
    green), that `~/.jdks` holds the JDK 21s, that workflow-manager ships no
    gradle-wrapper.jar, and that audit-service needs a JFrog credential in the
    environment. None of that is worth learning twice.
    """
    m2 = Path(os.environ.get("E5_M2", Path.home() / ".e5" / "m2"))
    m2.mkdir(parents=True, exist_ok=True)
    settings = tree.parent / "settings.xml"
    settings.write_text(CC._MVN_SETTINGS)
    java = CC._required_java(tree, build_system)
    argv, extra, how = CC._cmd(tree, build_system, java, CC._host_jdks(), m2, settings,
                               goals=("compile", "compileJava"))
    if not argv:
        return 1, "", f"NO TOOLCHAIN: {how} (needs java {java or '?'})"
    if build_system == "gradle":
        # 17 sequential builds on a 15 GB host: a daemon per Gradle version survives the
        # build and the next repo then starts with no headroom. Each tree is compiled once.
        argv = argv[:1] + ["--no-daemon"] + argv[1:]
    env = {**os.environ, **extra, **CC._jfrog_env()}
    p = subprocess.run(argv, cwd=str(tree), env=env, capture_output=True, text=True,
                       timeout=int(os.environ.get("E5_COMPILE_TIMEOUT", "1800")),
                       stdin=subprocess.DEVNULL)
    return p.returncode, (p.stdout or "") + (p.stderr or ""), how


def build_repo(repo: str, meta: dict, ref: str | None = None, try_refs: int = 3,
               keep_failed: bool = False) -> dict:
    """Export from the bare mirror and compile, trying candidate refs in order."""
    bs = meta.get("build", "gradle")
    refs = [ref] if ref else _ref_candidates(repo, meta.get("branch", ""))[:try_refs]
    if not refs:
        return {"ok": False, "error": f"no candidate refs for {repo}", "attempts": []}
    attempts = []
    for r in refs:
        wt = WORK / f"{repo}-{r.replace('/', '_')}"
        # Reuse requires a RECORDED success, not merely the presence of class files.
        #
        # A failed javac leaves behind every class it managed to write before it stopped,
        # so keying the cache on `_class_roots(wt)` read a half-finished build as a
        # finished one. deployment-mgmt's `release/v1.8.3` tree - the branch that does not
        # compile, and the subject of its own disagreement card - held 683 of its 838
        # classes, and the run printed `ok  release/v1.8.3  (cached)`. A partial call graph
        # presented as a complete one is worse than none: nothing about it looks wrong.
        if (wt / _OK_MARK).is_file() and _class_roots(wt):
            attempts.append({"ref": r, "ok": True, "tree": str(wt), "how": "cached"})
            return {"ok": True, "ref": r, "tree": wt, "how": "cached",
                    "fellback": r != meta.get("branch", ""), "attempts": attempts}
        shutil.rmtree(wt, ignore_errors=True)
        wt.mkdir(parents=True, exist_ok=True)
        bare = M.CACHE / f"{repo}.git"
        pr = subprocess.run(["git", "-C", str(bare), "archive", "--format=tar", r],
                            capture_output=True)
        if pr.returncode or not pr.stdout:
            attempts.append({"ref": r, "ok": False, "error": f"git archive {r} failed"})
            continue
        subprocess.run(["tar", "-x", "-C", str(wt)], input=pr.stdout, capture_output=True)
        try:
            rc, out, how = _compile(wt, bs)
        except subprocess.TimeoutExpired:
            attempts.append({"ref": r, "ok": False, "error": "compile timed out"})
            continue
        (wt.parent / f"{wt.name}.build.log").write_text(out)
        if rc == 0 and _class_roots(wt):
            (wt / _OK_MARK).write_text(r)
            attempts.append({"ref": r, "ok": True, "tree": str(wt), "how": how})
            return {"ok": True, "ref": r, "tree": wt, "how": how,
                    "fellback": r != meta.get("branch", ""), "attempts": attempts}
        errs = [l.strip() for l in out.splitlines() if CC._ERR.search(l)]
        tail = [l for l in out.splitlines()
                if l.startswith("[ERROR]") or "error:" in l or "What went wrong" in l]
        attempts.append({"ref": r, "ok": False,
                         "error": (errs[0] if errs else
                                   (how if rc else "compiled but produced no classes")
                                   + ": " + " ".join(tail[:2])[:200]),
                         "compile_error": bool(errs),
                         "log": str(wt.parent / f"{wt.name}.build.log")})
        # The log survives, the tree does not. A failed export is ~1 GB of downloaded
        # dependencies and there is 19 GB free; keeping them fills the disk mid-run and
        # the next repo then fails for a reason that has nothing to do with the next repo.
        if not keep_failed:
            shutil.rmtree(wt, ignore_errors=True)
    return {"ok": False, "attempts": attempts,
            "error": f"no candidate ref of {repo} compiles (tried {len(refs)})"}


def build(component: str, ref: str | None = None, try_refs: int = 3) -> dict:
    """Component-addressed wrapper, kept for `build`/`index`/the existing tests."""
    c = C.load()["Component"].get(component)
    if not c:
        return {"ok": False, "error": f"no Component {component!r}", "attempts": []}
    repo = (c.get("source") or {}).get("repo")
    _, repos = ws.registry()
    return build_repo(repo, repos.get(repo, {}), ref, try_refs)


# --------------------------------------------------------------------------- index
_METHODREF = re.compile(r'//\s*(?:Method|InterfaceMethod)\s+([\w/$.]+)\.(\w+):')
# javap emits `interface X` and `enum X` too. Matching only `class` dropped all 26
# repository INTERFACES - and in this repo the documented chain runs
# usecase -> repository interface -> impl, so the middle of the chain went missing.
# Any sequence of lowercase modifiers, not a hand-listed four. `sealed`, `non-sealed`,
# `protected`, `private` and `strictfp` all appear in javap output and none were matched.
_CLASSDECL = re.compile(r'^(?:[a-z-]+\s+)*(?:class|interface|enum|record)\s+([\w.$]+)')
# javap starts each class block with this. Resetting on it is the safety net: without it a
# declaration the regex fails to match leaves `cur` pointing at the PREVIOUS class, and
# every method reference in the unmatched class is then attributed to its neighbour -
# wrong edges rather than missing ones, which is far worse.
_CLASSHEAD = re.compile(r'^Compiled from "')


def index_repo(repo: str, tree: Path) -> dict:
    """Class-to-class references from compiled bytecode, labelled by layer and attributed
    to a Component."""
    roots = _class_roots(tree)
    if not roots:
        return {"ok": False, "error":
                f"no compiled classes under {tree} (looked for {', '.join(_CLASS_DIRS)} "
                f"and one level of subprojects)"}

    comps = components_in(repo)
    src_of, comp_of = {}, {}
    for root in roots:
        pre = _src_prefix(root, tree)
        for f in sorted(root.rglob("*.class")):
            if "$" in f.name:
                continue
            rel = f.relative_to(root).with_suffix(".java")
            fqn = str(rel).replace("/", ".")[:-5]
            if fqn in src_of:
                continue                      # first module wins; duplicates are shaded copies
            src_of[fqn] = pre + str(rel)
            comp_of[fqn] = _component_of(pre + str(rel), comps)

    layers_of = {comp: layer_map(comp) for comp, _ in comps}
    nodes, edges = {}, {}
    cp = ":".join(str(r) for r in roots)
    fqns = sorted(src_of)
    BATCH = 50
    for i in range(0, len(fqns), BATCH):
        p = subprocess.run(["javap", "-p", "-c", "-classpath", cp, *fqns[i:i + BATCH]],
                           capture_output=True, text=True, timeout=900)
        cur = None
        for line in p.stdout.splitlines():
            if _CLASSHEAD.match(line):
                cur = None
                continue
            m = _CLASSDECL.match(line.strip())
            if m:
                cur = m.group(1)
                if cur in src_of:
                    L = layers_of.get(comp_of.get(cur)) or []
                    # `undeclared` and `unlabelled` are DIFFERENT findings - see the
                    # module docstring. Collapsing them would report 13 repos as having a
                    # layer map with holes in it, when they have no layer map at all.
                    nodes[cur] = (_layer_of(src_of[cur], L) or "unlabelled") if L \
                        else "undeclared"
                continue
            if cur is None or cur not in src_of:
                continue
            r = _METHODREF.search(line)
            if not r:
                continue
            tgt = r.group(1).replace("/", ".")
            if tgt == cur or tgt not in src_of:
                continue          # only edges WITHIN the repo; Tier 1 has the rest
            edges.setdefault((cur, tgt), set()).add(r.group(2))

    lt = {}
    for (a, b), meths in edges.items():
        la, lb = nodes.get(a, "undeclared"), nodes.get(b, "undeclared")
        if la != lb:
            lt[(la, lb)] = lt.get((la, lb), 0) + 1
    return {"ok": True, "repo": repo, "classes": len(src_of), "nodes": nodes,
            "edges": {f"{a}->{b}": sorted(v) for (a, b), v in edges.items()},
            "layer_transitions": {f"{a} -> {b}": n for (a, b), n in sorted(lt.items())},
            "src_of": src_of, "comp_of": comp_of,
            "components": sorted({c for c in comp_of.values() if c})}


def index(component: str, tree: Path) -> dict:
    """Component-addressed wrapper over `index_repo`, narrowed to that Component."""
    c = C.load()["Component"].get(component) or {}
    repo = (c.get("source") or {}).get("repo") or component
    idx = index_repo(repo, tree)
    if not idx["ok"]:
        return idx
    keep = {f for f, comp in idx["comp_of"].items() if comp == component}
    if keep:
        idx["nodes"] = {k: v for k, v in idx["nodes"].items() if k in keep}
        idx["edges"] = {k: v for k, v in idx["edges"].items()
                        if k.split("->")[0] in keep}
        idx["classes"] = len(keep)
    return idx


# --------------------------------------------------------------------------- emit
def facts_for(repo: str, ref: str, idx: dict) -> list[dict]:
    """Facts for one repo.

    `component` may be EMPTY, and that is a deliberate outcome rather than a hole to
    paper over. akst-service checks in generated protobuf stubs under
    `com/e5/management/proto/**` and `com/e5/platform/core/akst/service/**` - vendored
    from two other services - which sit outside `task-manager`'s declared
    `source.paths`. The first version of this function fell back to the REPO name, which
    invented a Component called `akst-service` that the catalog has never heard of and
    reported 24 of 23 Components covered. A class that matches no declared glob is
    unattributed; that is a finding about `source.paths`, not a new Component, and it is
    emitted as a CodeAttributionGap so the number is visible instead of inferred.
    """
    cat = C.load()["Component"]
    out = []
    orphans = []
    for fqn, layer in idx["nodes"].items():
        comp = idx["comp_of"].get(fqn) or ""
        if not comp:
            orphans.append(idx["src_of"].get(fqn, fqn))
        out.append({
            "id": f"CodeUnit:{repo}:{fqn}", "contract_key": f"CodeUnit:{repo}:{fqn}",
            "kind": "CodeUnit", "name": fqn, "repo": repo, "component": comp,
            "system": (cat.get(comp) or {}).get("system", ""),
            "path": idx["src_of"].get(fqn, ""), "line": 0,
            "provenance": "bytecode", "confidence": "EXTRACTED",
            "commit": ref, "branch": ref,
            "props": {"layer": layer, "layer_confidence": layer_confidence(comp),
                      "limits": "class-level references from bytecode; no positions"}})
    for key, meths in idx["edges"].items():
        a, b = key.split("->")
        ca = idx["comp_of"].get(a) or ""
        cb = idx["comp_of"].get(b) or ""
        props = {"from_layer": idx["nodes"].get(a, "undeclared"),
                 "to_layer": idx["nodes"].get(b, "undeclared"),
                 "methods": meths[:12], "method_count": len(meths)}
        if ca != cb:
            # Same repo, two Components. platform-core is one repo and six roles, so an
            # edge can cross a Component boundary without crossing a process boundary -
            # which is exactly the thing a repo-keyed model cannot say.
            props["to_component"] = cb
        out.append({
            "id": f"CodeCalls:{repo}:{key}", "contract_key": f"CodeCalls:{repo}:{key}",
            "kind": "CodeCalls", "name": key, "repo": repo, "component": ca,
            "system": (cat.get(ca) or {}).get("system", ""),
            "path": idx["src_of"].get(a, ""), "line": 0,
            "provenance": "bytecode", "confidence": "EXTRACTED",
            "commit": ref, "branch": ref, "props": props})
    if orphans:
        out.append({
            "id": f"CodeAttributionGap:{repo}",
            "contract_key": f"CodeAttributionGap:{repo}",
            "kind": "CodeAttributionGap", "name": repo, "repo": repo, "component": "",
            "system": "", "path": "", "line": 0,
            "provenance": "bytecode", "confidence": "EXTRACTED",
            "commit": ref, "branch": ref,
            "props": {"count": len(orphans), "paths": sorted(orphans)[:12],
                      "note": "compiled classes outside every Component's declared "
                              "source.paths in this repo - a catalog gap, not a Component"}})
    return out


def write_facts(facts: list[dict]) -> int:
    """One sorted write. Same commits in, byte-identical file out."""
    GRAPH.mkdir(parents=True, exist_ok=True)
    facts.sort(key=lambda f: (f["kind"], f["repo"], f["name"]))
    with LAYER_FACTS.open("w") as fh:
        for f in facts:
            fh.write(json.dumps(f, sort_keys=True) + "\n")
    return len(facts)


def emit(component: str, ref: str, idx: dict) -> int:
    """Single-component emit, preserved for the pilot path. Replaces the whole file."""
    repo = idx.get("repo") or component
    return write_facts(facts_for(repo, ref, idx))


# --------------------------------------------------------------------- estate run
def run_estate(only: list[str] | None = None, try_refs: int = 3,
               keep_trees: bool = False, on_row=None) -> dict:
    """Build and index every JVM repo, then write one facts file.

    Sequential on purpose. Four cores and 15 GB, and a Gradle build will happily take all
    of both; two in parallel turns a compile failure into a memory failure and the report
    then blames the repo.

    `on_row` is called as each repo finishes, because a full pass takes half an hour and a
    run that prints nothing until the end is indistinguishable from a hung one - the same
    reason tools/check_compiles.py streams. Collecting first and printing last was how
    this function shipped once, and it was unreadable.
    """
    repos = jvm_repos()
    if only:
        repos = {r: m for r, m in repos.items() if r in only}
    rows, facts = [], []
    for repo, meta in repos.items():
        b = build_repo(repo, meta, None, try_refs)
        if not b["ok"]:
            bad = [a for a in b["attempts"] if not a["ok"]]
            rows.append({"repo": repo, "status": "no-build", "ref": "",
                         "detail": bad[0]["error"][:150] if bad else b["error"],
                         "compile_error": any(a.get("compile_error") for a in bad),
                         "components": [c for c, _ in components_in(repo)]})
            if on_row:
                on_row(rows[-1])
            continue
        idx = index_repo(repo, b["tree"])
        if not idx["ok"]:
            rows.append({"repo": repo, "status": "no-classes", "ref": b["ref"],
                         "detail": idx["error"][:150],
                         "components": [c for c, _ in components_in(repo)]})
            if on_row:
                on_row(rows[-1])
            continue
        facts += facts_for(repo, b["ref"], idx)
        rows.append({"repo": repo, "status": "ok", "ref": b["ref"],
                     "fellback": b.get("fellback", False),
                     "detail": f"{idx['classes']} classes, {len(idx['edges'])} edges "
                               f"({b['how']})",
                     "classes": idx["classes"], "edges": len(idx["edges"]),
                     "components": idx["components"]})
        if on_row:
            on_row(rows[-1])
        if not keep_trees:
            shutil.rmtree(b["tree"], ignore_errors=True)
    n = write_facts(facts)
    return {"rows": rows, "facts": n}


def coverage() -> dict:
    """What fraction of the estate Tier 2 actually reaches, by Component.

    The denominator matters and has been quoted wrongly: 41 Components is the whole
    catalog, and 11 of those are external or have no repo at all, so Tier 2 can never
    reach them. The honest denominator is Components backed by a JVM repo.
    """
    cat = C.load()["Component"]
    eligible = set()
    for repo in jvm_repos():
        eligible |= {c for c, _ in components_in(repo)}
    have, orphan = set(), 0
    if LAYER_FACTS.is_file():
        for line in LAYER_FACTS.open():
            f = json.loads(line)
            if f["kind"] == "CodeAttributionGap":
                orphan += f["props"]["count"]
                continue
            if f["component"]:
                have.add(f["component"])
    no_repo = {c for c, v in cat.items() if not (v.get("source") or {}).get("paths")
               and (v.get("source") or {}).get("repo") in (None, "EXTERNAL", "UNKNOWN")}
    return {"catalog": len(cat), "jvm_eligible": len(eligible), "covered": len(have),
            "missing": sorted(eligible - have), "no_repo": len(no_repo),
            "unattributed_classes": orphan,
            "not_jvm": len(cat) - len(eligible) - len(no_repo)}


# --------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("build", "index"):
        p = sub.add_parser(n)
        p.add_argument("--component", default="deployment-mgmt")
        p.add_argument("--ref")
    al = sub.add_parser("all", help="every JVM repo in the registry")
    al.add_argument("--only", default="", help="comma-separated repo ids")
    al.add_argument("--refs", type=int, default=3, help="candidate refs per repo")
    al.add_argument("--keep-trees", action="store_true",
                    help="leave build trees in ~/.e5/scip (fast re-index, ~1 GB/repo)")
    sub.add_parser("coverage")
    sub.add_parser("status")
    t = sub.add_parser("trace"); t.add_argument("name")
    a = ap.parse_args()

    if a.cmd == "status":
        print("backends:")
        for tool, why in (("scip-java", "preferred: precise, cross-repo symbol identity"),
                          ("javap", "fallback: class-level refs from bytecode")):
            have = subprocess.run(["which", tool], capture_output=True).returncode == 0
            print(f"  {'ok  ' if have else 'MISS'} {tool:<11} {why}")
        print("\nlayer maps declared for:")
        for row in (minyaml.load((JOINS / 'layer-maps.yaml').read_text()).get("rows") or []):
            print(f"  {row['component']:<20} confidence={row['confidence']}")
        if LAYER_FACTS.is_file():
            n = sum(1 for _ in LAYER_FACTS.open())
            print(f"\n{n} layer facts in {LAYER_FACTS.relative_to(KB)}")
        else:
            print("\nno layer facts yet - run `build` then `index`")
        return 0

    if a.cmd == "coverage":
        cv = coverage()
        print(f"Tier 2 covers {cv['covered']} of {cv['jvm_eligible']} Components backed "
              f"by a JVM repo.")
        print(f"  catalog total        {cv['catalog']}")
        print(f"  no repo / external   {cv['no_repo']}  (unreachable by any code tier)")
        print(f"  not a JVM repo       {cv['not_jvm']}  (python, csharp, yaml, docker)")
        if cv["missing"]:
            print(f"\n  still missing ({len(cv['missing'])}): {', '.join(cv['missing'])}")
        return 0

    if a.cmd == "all":
        only = [r for r in a.only.split(",") if r]
        w = max((len(r) for r in jvm_repos()), default=20)

        def show(r):
            mark = {"ok": "ok      ", "no-build": "NO-BUILD",
                    "no-classes": "NO-CLASS"}[r["status"]]
            ref = r["ref"] + (" (fellback)" if r.get("fellback") else "")
            print(f"{r['repo'].ljust(w)}  {mark}  {ref:<34} {r['detail']}", flush=True)

        res = run_estate(only or None, a.refs, a.keep_trees, on_row=show)
        ok = [r for r in res["rows"] if r["status"] == "ok"]
        print(f"\n{len(ok)}/{len(res['rows'])} JVM repos indexed, "
              f"{sum(r['classes'] for r in ok)} classes, "
              f"{sum(r['edges'] for r in ok)} intra-repo edges, {res['facts']} facts "
              f"-> {LAYER_FACTS.relative_to(KB)}")
        fell = [r for r in ok if r.get("fellback")]
        if fell:
            print("\nNOT the indexed branch - these traces describe an older tree:")
            for r in fell:
                print(f"  {r['repo']}: built {r['ref']}")
        bad = [r for r in res["rows"] if r["status"] != "ok"]
        if bad:
            print("\nnot reached:")
            for r in bad:
                print(f"  {r['repo']}: {r['detail']}")
        cv = coverage()
        print(f"\nCOVERAGE {cv['covered']}/{cv['jvm_eligible']} Components backed by a "
              f"JVM repo ({cv['no_repo']} of the catalog's {cv['catalog']} have no repo "
              f"at all, {cv['not_jvm']} are not JVM)")
        return 0 if len(ok) == len(res["rows"]) else 1

    if a.cmd == "build":
        r = build(a.component, a.ref)
        for att in r.get("attempts", []):
            mark = "ok  " if att["ok"] else "FAIL"
            print(f"  {mark} {att['ref']:<22} {att.get('error', att.get('tree', ''))[:90]}")
        if not r["ok"]:
            print(f"\n{r['error']}")
            print("  A Tier 2 trace needs a compile. This is a finding about the branch, "
                  "not about the tool - see disagreements/.")
            return 1
        print(f"\nbuilt {a.component} at {r['ref']}")
        print(f"  next: python3 tools/scip_pilot.py index --component {a.component} "
              f"--ref {r['ref']}")
        return 0

    if a.cmd == "index":
        r = build(a.component, a.ref)
        if not r["ok"]:
            print(f"cannot index: {r['error']}")
            return 1
        idx = index(a.component, r["tree"])
        if not idx["ok"]:
            print(idx["error"]); return 1
        n = emit(a.component, r["ref"], idx)
        print(f"{a.component} @ {r['ref']} — {idx['classes']} classes, "
              f"{len(idx['edges'])} intra-service edges")
        print(f"layer confidence: {layer_confidence(a.component)}\n")
        print("LAYER TRANSITIONS (what calls what, by declared layer)")
        for k, v in idx["layer_transitions"].items():
            print(f"  {k:<42} {v}")
        print(f"\n{n} facts -> {LAYER_FACTS.relative_to(KB)}")
        return 0

    # trace
    if not LAYER_FACTS.is_file():
        print("no layer facts - run `index` first")
        return 1
    facts = [json.loads(l) for l in LAYER_FACTS.open()]
    units = {f["name"]: f for f in facts if f["kind"] == "CodeUnit"}
    calls = [f for f in facts if f["kind"] == "CodeCalls"]
    seeds = [u for u in units if a.name.lower() in u.lower()]
    if not seeds:
        hits = [c for c in calls if a.name.lower() in
                " ".join(c["props"].get("methods", [])).lower()]
        seeds = sorted({c["name"].split("->")[0] for c in hits})[:3]
    if not seeds:
        print(f"nothing matching {a.name!r}")
        return 2
    for s in seeds[:3]:
        print(f"\n{s}   [layer: {units.get(s, {}).get('props', {}).get('layer', '?')}]")
        frontier, seen, depth = [s], {s}, 0
        while frontier and depth < 4:
            nxt = []
            for node in frontier:
                for c in calls:
                    fr, to = c["name"].split("->")
                    if fr != node or to in seen:
                        continue
                    seen.add(to)
                    nxt.append(to)
                    print(f"{'  ' * (depth + 1)}-> {to.rsplit('.', 1)[-1]:<44} "
                          f"[{c['props'].get('to_layer')}] "
                          f"{','.join(c['props'].get('methods', [])[:2])}")
            frontier = nxt
            depth += 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

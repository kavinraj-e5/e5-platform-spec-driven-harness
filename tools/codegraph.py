#!/usr/bin/env python3
"""Tier 1 code layer: entry points and service-to-service edges, from static literals.

  python3 tools/codegraph.py entrypoints
  python3 tools/codegraph.py edges
  python3 tools/codegraph.py extract      # emit facts
  python3 tools/codegraph.py trace startWorkflow

WHY TIER 1 BEFORE SCIP
----------------------
Exploration measured that the cross-service graph is derivable from static literals with
near-exact accuracy and needs no semantic indexer, because each repo encodes its outbound
target unambiguously:

  workflow-manager   `ServiceType` enum literal at the call site
  interface          the injected stub TYPE
  workflow-ops       stub class literal + env-var literal
  deployment-mgmt    nothing - it is a pure sink with zero outbound calls

That answers "this API ends up calling deployment-mgmt" for the whole estate at the cost of
a regex pass. SCIP is needed only for the *intra*-service layer trace, needs protoc plus a
working build, and is piloted separately.

THE THREE HAND-CURATED JOINS
----------------------------
Three things no indexer will find, declared in `knowledge/joins/` because they are
judgement, not parse output:

  1. poller task-name constants - workflow-manager's 9 entry points are string comparisons
     against constants, and the PRODUCER of each string lives in a different repo
  2. Kafka topic config keys - topics are never literals in this estate
  3. per-repo layer maps - four repos, four conventions; `interface` alone splits across
     `grpc/impl/`, `service/impl/` and `service/task/`, and its `grpc/impl/` package holds
     GraphQL implementations, which will bait a false gRPC-entry-point classification

Layer convention is NOT inferred from package names. That was measured as unreliable.
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "release"))
import catalog as C
import mirror as M
import minyaml
import sync as S   # reuse owns(): most-specific-glob ownership

KB = HERE.parent
GRAPH = KB / "graph"
CODE_FACTS = GRAPH / "code-facts.jsonl"
JOINS = KB / "knowledge" / "flows"


# --------------------------------------------------------------------------- git access
#
# One `git show` per file meant thousands of subprocess spawns and blew a 2-minute budget.
# A repo's tree is materialised ONCE with `git archive` and then read from disk - the same
# approach sync.py uses, and for the same reason.
_TREES: dict = {}


def _materialise(repo: str, ref: str) -> Path | None:
    key = (repo, ref)
    if key in _TREES:
        return _TREES[key]
    import shutil, tempfile
    bare = M.CACHE / f"{repo}.git"
    if not bare.is_dir():
        _TREES[key] = None
        return None
    d = Path(tempfile.mkdtemp(prefix=f"e5cg-{repo}-"))
    pr = subprocess.run(["git", "-C", str(bare), "archive", "--format=tar", ref],
                        capture_output=True)
    if pr.returncode:
        shutil.rmtree(d, ignore_errors=True)
        _TREES[key] = None
        return None
    tx = subprocess.run(["tar", "-x", "-C", str(d)], input=pr.stdout, capture_output=True)
    if tx.returncode:
        shutil.rmtree(d, ignore_errors=True)
        d = None
    _TREES[key] = d
    return d


def cleanup():
    import shutil
    for d in _TREES.values():
        if d:
            shutil.rmtree(d, ignore_errors=True)
    _TREES.clear()


def _tree(repo: str, ref: str) -> list[str]:
    d = _materialise(repo, ref)
    if d is None:
        return []
    return [str(f.relative_to(d)) for f in d.rglob("*") if f.is_file()]


def _show(repo: str, ref: str, path: str) -> str:
    d = _materialise(repo, ref)
    if d is None:
        return ""
    f = d / path
    try:
        return f.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _ref_for(repo: str) -> str | None:
    _root, repos = M.registry()
    for r in repos:
        if r["id"] == repo:
            return r["branch"]
    return None


# --------------------------------------------------------------------------- entry points
# Four detectors, because this estate has four genuinely different mechanisms and no
# single pattern covers them. The poller one is the important one: those entry points
# appear in no proto and no annotation, so nothing generic would ever find them.
_SPRING_GQL = re.compile(r'@(MutationMapping|QueryMapping|SchemaMapping)\s*(?:\([^)]*\))?\s*'
                         r'(?:public\s+)?[\w.<>\[\],? ]+\s+(\w+)\s*\(')
_GRPC_IMPLBASE = re.compile(r'class\s+(\w+)\s+extends\s+(\w+)Grpc\.\2ImplBase')
_GRPC_OVERRIDE = re.compile(r'@Override\s+public\s+void\s+(\w+)\s*\(')
_PY_SERVICER = re.compile(r'class\s+(\w+)\s*\(\s*[\w.]*?(\w+)Servicer\s*\)')
_PY_RPC = re.compile(r'^\s{4}def\s+([A-Z]\w+)\s*\(\s*self\s*,\s*request', re.M)
_KAFKA_LISTENER = re.compile(r'@KafkaListener\s*\([^)]*\)\s*(?:public\s+)?\w[\w.<>\[\] ]*\s+(\w+)\s*\(')


def entrypoints(component: str, cdef: dict) -> list[dict]:
    repo = (cdef.get("source") or {}).get("repo")
    ref = _ref_for(repo)
    if not ref:
        return []
    out = []
    cat = C.load()
    files = [f for f in _tree(repo, ref) if f.endswith((".java", ".py"))]
    for f in files:
        # platform-core holds six Components; without this, its AKST protos were
        # credited as entry points to wf-engine, wf-worker, wf-aggregator and the rest.
        if not S.owns(f, component, cat, repo):
            continue
        t = _show(repo, ref, f)
        if not t:
            continue
        line = lambda i: t[:i].count("\n") + 1

        for m in _SPRING_GQL.finditer(t):
            out.append({"component": component, "kind": "graphql",
                        "name": m.group(2), "op": m.group(1).replace("Mapping", "").lower(),
                        "path": f, "line": line(m.start())})

        for m in _GRPC_IMPLBASE.finditer(t):
            svc = m.group(2)
            for om in _GRPC_OVERRIDE.finditer(t):
                out.append({"component": component, "kind": "grpc",
                            "name": f"{svc}.{om.group(1)}", "op": "rpc",
                            "path": f, "line": line(om.start())})

        for m in _PY_SERVICER.finditer(t):
            svc = m.group(2)
            for rm in _PY_RPC.finditer(t):
                out.append({"component": component, "kind": "grpc",
                            "name": f"{svc}.{rm.group(1)}", "op": "rpc",
                            "path": f, "line": line(rm.start())})

        for m in _KAFKA_LISTENER.finditer(t):
            out.append({"component": component, "kind": "kafka", "name": m.group(1),
                        "op": "consume", "path": f, "line": line(m.start())})

    # poller entry points come from the curated join - see module docstring
    for row in _load_join("task-names"):
        if row.get("handled_by") == component:
            out.append({"component": component, "kind": "task",
                        "name": row["fb_name"], "op": "poll",
                        "path": row.get("site", ""), "line": row.get("line", 0),
                        "curated": True})
    # de-dup: one Java file can declare both the ImplBase and its overrides
    seen, uniq = set(), []
    for e in out:
        k = (e["component"], e["kind"], e["name"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


# --------------------------------------------------------------------------- edges
# Each Component encodes its outbound target differently but each is unambiguous.
_SERVICETYPE_USE = re.compile(r'ServiceType\.([A-Z][A-Z0-9_]+)')
_STUB_TYPE = re.compile(r'(\w+)(?:Grpc)?\.?(\w*)BlockingStub|(\w+)Stub\b')
_PY_STUB = re.compile(r'(\w+)_pb2_grpc\.(\w+)Stub')
_ENV_TARGET = re.compile(r'getenv\(\s*["\']([A-Z][A-Z0-9_]*(?:SERVICE|MANAMGENT|MANAGEMENT)[A-Z0-9_]*)["\']')

# ServiceType / stub names -> Component. Authored, because the mapping between an enum
# constant and a deployed Component is a naming convention, not something derivable.
TARGET_ALIASES = {
    "DEPLOYMENT_MANAGEMENT": "deployment-mgmt", "DEPLOYMENTMANAGEMENT": "deployment-mgmt",
    "AKST": "task-manager", "TASK_MANAGER": "task-manager",
    "WORKFLOW_OPS": "workflow-ops", "WORKFLOWOPS": "workflow-ops",
    "EVENT_STORE": "event-store", "EVENTSTORE": "event-store",
    "WORKFLOW_MANAGER": "workflow-manager", "WORKFLOWMANAGER": "workflow-manager",
    "MONITOR": "monitoring-service", "MONITORING": "monitoring-service",
    "INTERFACE": "interface", "INTERFACE_TASK": "interface", "CONFIG": "deployment-mgmt",
    "DEPLOYMENT_MANAMGENT_SERVICE": "deployment-mgmt",   # typo is consistent in workflow-ops
    "DEPLOYMENT_MANAGEMENT_SERVICE": "deployment-mgmt",
    "WORKFLOW_MANAGER_SERVICE": "workflow-manager",
    "TASK_MANAGER_SERVICE": "task-manager",
    "MONITORING_SERVICE": "monitoring-service",
    "EVENT_STORE_SERVICE": "event-store",
    "WORKFLOW_OPS_SERVICE": "workflow-ops",
}


def _norm_target(raw: str) -> str | None:
    key = raw.upper().replace("-", "_")
    if key in TARGET_ALIASES:
        return TARGET_ALIASES[key]
    # DeploymentManagementServiceBlockingStub -> DEPLOYMENT_MANAGEMENT...
    snake = re.sub(r'(?<!^)(?=[A-Z])', '_', raw).upper()
    snake = snake.replace("_SERVICE_BLOCKING_STUB", "").replace("_BLOCKING_STUB", "")
    snake = snake.replace("_SERVICE_STUB", "").replace("_STUB", "")
    return TARGET_ALIASES.get(snake)


def edges(component: str, cdef: dict) -> list[dict]:
    repo = (cdef.get("source") or {}).get("repo")
    ref = _ref_for(repo)
    if not ref:
        return []
    found = {}
    cat = C.load()
    for f in [x for x in _tree(repo, ref) if x.endswith((".java", ".py"))]:
        if not S.owns(f, component, cat, repo):
            continue
        t = _show(repo, ref, f)
        if not t:
            continue
        line = lambda i: t[:i].count("\n") + 1
        for rx, how in ((_SERVICETYPE_USE, "servicetype-enum"),
                        (_PY_STUB, "py-stub-literal"),
                        (_ENV_TARGET, "env-literal")):
            for m in rx.finditer(t):
                raw = m.group(2) if rx is _PY_STUB else m.group(1)
                tgt = _norm_target(raw)
                if not tgt or tgt == component:
                    continue
                found.setdefault((tgt, how), {
                    "from": component, "to": tgt, "how": how,
                    "path": f, "line": line(m.start()), "literal": raw})
        for m in re.finditer(r'(\w+ServiceBlockingStub|\w+OpsBlockingStub|'
                             r'\w+ManagerServiceBlockingStub)', t):
            tgt = _norm_target(m.group(1))
            if tgt and tgt != component:
                found.setdefault((tgt, "stub-type"), {
                    "from": component, "to": tgt, "how": "stub-type",
                    "path": f, "line": line(m.start()), "literal": m.group(1)})
    # Collapse to one edge per (from, to), keeping every encoding that found it.
    # Two independent encodings agreeing is stronger evidence than either alone -
    # monitoring-service -> deployment-mgmt is confirmed by both the ServiceType enum
    # and the stub type - so the methods are kept rather than one being discarded.
    merged: dict = {}
    for e in found.values():
        k = (e["from"], e["to"])
        if k in merged:
            m = merged[k]
            m["how"] = ",".join(sorted(set(m["how"].split(",") + [e["how"]])))
            m["evidence"].append(f"{e['path'].split('/')[-1]}:{e['line']}")
        else:
            e = dict(e)
            e["evidence"] = [f"{e['path'].split('/')[-1]}:{e['line']}"]
            merged[k] = e
    for m in merged.values():
        m["confirmations"] = len(m["how"].split(","))
    return list(merged.values())


# --------------------------------------------------------------------------- curated joins
def _load_join(name: str) -> list[dict]:
    f = JOINS / f"{name}.yaml"
    if not f.is_file():
        return []
    try:
        doc = minyaml.load(f.read_text())
    except Exception as e:
        print(f"  ! {f.name}: {e}", file=sys.stderr)
        return []
    return doc.get("rows") or []


# --------------------------------------------------------------------------- facts
def _fact(kind, name, component, system, path, line, **props):
    return {"id": f"{kind}:{component}:{path}:{line}:{name}",
            "contract_key": f"{kind}:{name}", "kind": kind, "name": name,
            "repo": component, "component": component, "system": system,
            "path": path, "line": line, "provenance": "code",
            "confidence": "EXTRACTED", "props": props}


def extract() -> dict:
    cat = C.load()
    eps, eds, errs = [], [], []
    for cname, cdef in sorted(cat["Component"].items()):
        if cdef.get("unindexed") or cdef.get("system") in ("excluded", "external/actors"):
            continue
        try:
            eps.extend(entrypoints(cname, cdef))
            eds.extend(edges(cname, cdef))
        except Exception as e:
            errs.append(f"{cname}: {type(e).__name__}: {e}")
    facts = []
    for e in eps:
        sysname = cat["Component"][e["component"]]["system"]
        facts.append(_fact("EntryPoint", e["name"], e["component"], sysname,
                           e["path"], e["line"], mechanism=e["kind"], op=e["op"],
                           curated=e.get("curated", False)))
    for e in eds:
        sysname = cat["Component"][e["from"]]["system"]
        facts.append(_fact("CallsComponent", f"{e['from']}->{e['to']}", e["from"],
                           sysname, e["path"], e["line"], target=e["to"],
                           how=e["how"], literal=e["literal"]))
    if facts:
        GRAPH.mkdir(parents=True, exist_ok=True)
        facts.sort(key=lambda f: (f["component"], f["kind"], f["name"]))
        with CODE_FACTS.open("w") as fh:
            for f in facts:
                fh.write(json.dumps(f, sort_keys=True) + "\n")
    return {"entrypoints": eps, "edges": eds, "facts": facts, "errors": errs}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("entrypoints")
    sub.add_parser("edges")
    sub.add_parser("extract")
    t = sub.add_parser("trace"); t.add_argument("name")
    a = ap.parse_args()

    try:
        out = extract()
    finally:
        cleanup()
    if a.cmd == "entrypoints":
        import collections
        by = collections.Counter((e["component"], e["kind"]) for e in out["entrypoints"])
        print(f"{'component':<22} {'mechanism':<12} count")
        print("-" * 46)
        for (c, k), n in sorted(by.items()):
            print(f"{c:<22} {k:<12} {n}")
        print(f"\n{len(out['entrypoints'])} entry points across "
              f"{len({e['component'] for e in out['entrypoints']})} Components")
        return 0

    if a.cmd == "edges":
        print(f"{'from':<20} -> {'to':<19} {'#':>2} {'encodings':<34} evidence")
        print("-" * 112)
        for e in sorted(out["edges"], key=lambda x: (x["from"], x["to"])):
            print(f"{e['from']:<20} -> {e['to']:<19} {e['confirmations']:>2} "
                  f"{e['how']:<34} {','.join(e['evidence'][:2])}")
        sinks = {c for c in C.load()["Component"]} - {e["from"] for e in out["edges"]}
        print(f"\n{len(out['edges'])} edges. Components with NO outbound call: "
              f"{len(sinks)}")
        return 0

    if a.cmd == "trace":
        hits = [e for e in out["entrypoints"] if a.name.lower() in e["name"].lower()]
        if not hits:
            print(f"no entry point matching {a.name!r}")
            return 2
        for h in hits:
            print(f"\n{h['component']}  {h['kind']}/{h['op']}  {h['name']}")
            print(f"  declared at {h['path']}:{h['line']}")
            outs = [e for e in out["edges"] if e["from"] == h["component"]]
            if outs:
                print(f"  {h['component']} calls:")
                for o in sorted(outs, key=lambda x: x["to"]):
                    print(f"    -> {o['to']:<20} ({o['how']}, "
                          f"{o['path'].split('/')[-1]}:{o['line']})")
            else:
                print(f"  {h['component']} makes no outbound call - it is a sink")
        print("\nTier 1 gives entry point -> target Component. The INTERNAL layer trace "
              "needs SCIP; see tools/scip_pilot.py.")
        return 0

    import collections
    print(f"{len(out['facts'])} code facts: "
          f"{dict(collections.Counter(f['kind'] for f in out['facts']))}")
    if out["errors"]:
        print(f"{len(out['errors'])} errors:")
        for e in out["errors"]:
            print(f"  {e}")
    print(f"-> {CODE_FACTS.relative_to(KB)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Navigation over the knowledge base: documents by PATH, extracted facts by exact NAME.

There is deliberately no search here. Every earlier retrieval tool (locate, fits, impact,
search) ranked results by word overlap, and P0 measured them at 15-40% on real platform
stories (evals/p0-tool-validation-2026-11-14.md). A word match is a guess.

The model reads knowledge/00-overview.md (loaded at session start), decides which areas a
task touches, and walks down:

    00-overview.md -> <system>/README.md -> <area>/README.md -> <component>.md
                   -> flows/, contracts/, tables/        (by exact name)
                   -> component(name)                    (extracted facts)

Every function returns exactly what a document or an extracted fact says, or says plainly
that nothing is recorded. None of them ranks, scores or infers.
"""
from __future__ import annotations
import collections, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

KB = HERE.parent
KN = KB / "knowledge"
GRAPH = KB / "graph"
FACTS = GRAPH / "facts.jsonl"
CODE_FACTS = GRAPH / "code-facts.jsonl"
BUILD_FACTS = GRAPH / "build-facts.jsonl"
TASK_FACTS = GRAPH / "async-facts.jsonl"      # task routes only; Kafka topic facts are not used
CALL_FACTS = GRAPH / "call-facts.jsonl"       # Level 2: method-level calls (tools/call_graph.py)
RPC_FACTS = GRAPH / "rpc-facts.jsonl"

# Names people use for components. Source: knowledge/constitution/glossary.md and the owner.
# An alias resolves to exactly one catalog Component, or it is not an alias.
ALIASES = {
    "dss": "dynamic-scaling", "dynamic-scaling-service": "dynamic-scaling",
    "dms": "deployment-mgmt", "db-mgmt-service": "deployment-mgmt",
    "deployment-management": "deployment-mgmt",
    "cds": "cds", "credential-distribution-service": "cds",
    "ccs": "ccs",
    "akst-service": "task-manager", "e5-akst-service": "task-manager",
    "wfm": "workflow-manager", "workflow-ops-service": "workflow-ops", "wfops": "workflow-ops",
    "monitoring": "monitoring-service", "interface-service": "interface",
}

# Kafka topic wiring is deploy-time configuration. It is not knowledge and is never returned.
_TOPICISH = ("kafka", "topic")


def _jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _catalog():
    import catalog as C
    return C.load()


def _rel(p: Path) -> str:
    return p.relative_to(KN).as_posix()


# --------------------------------------------------------------------------- documents
def overview() -> dict:
    """The L0 document. Also injected at session start."""
    p = KN / "00-overview.md"
    return {"ok": True, "path": _rel(p), "text": p.read_text()}


def read(path: str) -> dict:
    """A document under knowledge/, by path. A folder returns its README and its contents.

    Not found returns what IS there, never a nearest match."""
    raw = path.strip().lstrip("/")
    if raw.startswith("knowledge/"):
        raw = raw[len("knowledge/"):]
    target = (KN / raw).resolve()
    if KN.resolve() not in target.parents and target != KN.resolve():
        return {"ok": False, "error": "path is outside knowledge/"}
    if target.is_dir():
        readme = target / "README.md"
        return {"ok": True, "path": _rel(target) + "/",
                "readme": readme.read_text() if readme.is_file() else None,
                "contents": sorted(p.name + ("/" if p.is_dir() else "")
                                   for p in target.iterdir() if not p.name.startswith("."))}
    for cand in (target, target.with_suffix(".md")):
        if cand.is_file():
            return {"ok": True, "path": _rel(cand), "text": cand.read_text()}
    parent = target.parent
    while not parent.is_dir() and KN.resolve() in parent.parents:
        parent = parent.parent
    return {"ok": False, "error": f"no document at knowledge/{raw}",
            "folder": _rel(parent) + "/" if parent != KN.resolve() else "",
            "contents": sorted(p.name + ("/" if p.is_dir() else "")
                               for p in parent.iterdir() if not p.name.startswith("."))}


# --------------------------------------------------------------------------- components
def resolve(name: str) -> str | None:
    """Catalog Component for a name or a known alias. Exact, case-insensitive. No fuzzing."""
    cat = _catalog()
    key = name.strip().lower()
    if key in cat["Component"]:
        return key
    a = ALIASES.get(key)
    return a if a in cat["Component"] else None


def _documents(comp: str) -> list[str]:
    return sorted(_rel(p) for p in KN.rglob("*.md")
                  if p.name != "README.md" and (p.stem == comp or p.stem.startswith(comp + "-")))


def component(name: str) -> dict:
    """What the catalog says, which documents exist, and what the extractors found."""
    comp = resolve(name)
    cat = _catalog()
    if not comp:
        return {"ok": False, "error": f"{name!r} is not a catalog component or known alias",
                "components": sorted(cat["Component"]), "aliases": ALIASES}
    c = cat["Component"][comp]
    repo = (c.get("source") or {}).get("repo")

    facts = [f for f in _jsonl(FACTS) + _jsonl(CODE_FACTS) if f.get("component") == comp]
    facts = [f for f in facts
             if not (f["kind"] == "ConfigKey" and any(t in f["name"].lower() for t in _TOPICISH))]
    by_kind = collections.defaultdict(list)
    for f in facts:
        by_kind[f["kind"]].append(f"{f['name']}  @ {f.get('path')}:{f.get('line')}")
    extracted = {k: {"count": len(v), "items": sorted(v)[:40]} for k, v in sorted(by_kind.items())}

    rf = [f for f in _jsonl(GRAPH / "rpc-facts.jsonl") if f.get("component") == comp]
    serves = sorted({f"{f['service']}.{f['rpc']}" for f in rf if f["kind"] == "ServesRpc"})
    calls = sorted({f"{f['service']}.{f['rpc']}" for f in rf if f["kind"] == "CallsRpc"})
    tasks = [t for t in _jsonl(TASK_FACTS) if t["kind"] in ("TaskRoute", "TaskRouting")
             and comp in (t.get("component"), t.get("handled_by"), t.get("produced_by"))]
    return {
        "ok": True, "component": comp,
        "catalog": {k: c.get(k) for k in ("system", "kind", "plane", "worker_mode",
                                          "owns_task_type", "deployed_in_qa") if c.get(k)}
                   | {"repo": repo, "role": " ".join((c.get("role") or "").split())},
        "documents": _documents(comp),
        "serves_rpcs": serves or None, "calls_rpcs": calls or None,
        "graphql_sends_task": [{"graphql": g["graphql"], "task": g["task_name"],
                                "at": f"{g['path']}:{g['line']}"}
                               for g in _jsonl(GRAPH / "gql-task-facts.jsonl")
                               if g.get("component") == comp] or None,
        "extracted": extracted or None,
        "task_routes": [{k: t.get(k) for k in ("task_name", "task_type", "fb_name",
                                               "produced_by", "handled_by", "path", "line")}
                        for t in tasks] or None,
        "dependencies": dependencies(comp)["depends_on"] if repo else None,
        "note": ("extracted = what the extractors found at the indexed release. A kind that "
                 "is absent was not extracted for this component; it does not mean absent "
                 "in the code." + (" This component has no indexed repo." if not repo
                                   or repo in ("UNKNOWN", "EXTERNAL") else "")),
    }


def dependencies(name: str) -> dict:
    """Build-file dependencies, both directions, with versions, and what is behind.

    Sources: graph/build-facts.jsonl (build files and Dockerfiles at each repo's latest
    release), graph/library-versions.json (newest release per library on JFrog),
    graph/consumer-build-facts.jsonl (build files of repos OUTSIDE the platform that
    consume its libraries, e.g. customer workflows and apps)."""
    comp = resolve(name) or name
    cat = _catalog()
    repo = ((cat["Component"].get(comp) or {}).get("source") or {}).get("repo") or comp
    facts = _jsonl(BUILD_FACTS)
    outside = _jsonl(GRAPH / "consumer-build-facts.jsonl")
    vers = json.loads((GRAPH / "library-versions.json").read_text()) \
        if (GRAPH / "library-versions.json").is_file() else {}
    import lib_versions as LV

    def _latest(art, pin):
        v = vers.get(art) or vers.get((art or "").lower().replace("-", "_")) or {}
        return (v.get("latest_by_major") or {}).get(pin.split(".")[0]) if pin else None

    pub = {f.get("artifact") for f in facts if f["kind"] == "Publishes" and f["repo"] == repo} - {None}
    art = (cat["Component"].get(comp) or {}).get("artifact")      # artifact-only libraries
    if art:
        pub.add(art.split(":")[-1])
    norm = lambda a: (a or "").lower().replace("-", "_")
    pubn = {norm(a) for a in pub}
    pub = sorted(pub)
    out = []
    for f in facts:
        if f["kind"] == "DependsOnArtifact" and f["repo"] == repo:
            lt = _latest(f.get("artifact"), f.get("version"))
            out.append({"artifact": f.get("artifact"), "version": f.get("version"),
                        "latest_in_line": lt,
                        "behind": bool(lt and LV.vkey(f.get("version") or "") < LV.vkey(lt)),
                        "re_exported": bool(f.get("transitive"))})
    used = []
    for f in facts + outside:
        if f["kind"] == "DependsOnArtifact" and norm(f.get("artifact")) in pubn:
            lt = _latest(f["artifact"], f.get("version"))
            used.append({"repo": f["repo"], "artifact": f["artifact"], "version": f.get("version"),
                         "latest_in_line": lt,
                         "behind": bool(lt and LV.vkey(f.get("version") or "") < LV.vkey(lt)),
                         "outside_platform": f in outside})
    # inherited: consumers of a library that RE-EXPORTS (gradle `api`) another one get it too
    inherited = []
    reexp = {(f["repo"], f.get("artifact")) for f in facts if f["kind"] == "DependsOnArtifact" and f.get("transitive")}
    for lib_repo, art_ in reexp:
        if norm(art_) not in pubn:
            continue
        lib_pub = {g.get("artifact") for g in facts if g["kind"] == "Publishes" and g["repo"] == lib_repo}
        for g in facts + outside:
            if g["kind"] == "DependsOnArtifact" and g.get("artifact") in lib_pub:
                inherited.append({"repo": g["repo"], "via": f"{lib_repo} (re-exports {art_})"})
    scanned = next((f for f in facts if f["kind"] == "BuildFileScanned" and f["repo"] == repo), None)
    images = [{"image": f["image"], "tag": f["tag"], "at": f"{f['path']}:{f['line']}",
               "unresolved": f["unresolved"]}
              for f in facts if f["kind"] == "BuildsFromImage" and f["repo"] == repo]
    image_users = [{"repo": f["repo"], "tag": f["tag"], "at": f"{f['path']}:{f['line']}"}
                   for f in facts + outside
                   if f["kind"] == "BuildsFromImage" and comp in ("rf-fb-base-image",)
                   and f.get("image") == "e5-rf-fb-base"]
    note = None
    if scanned and scanned["parsed"] and not out:
        note = f"build files read ({', '.join(scanned['files'][:4])}): no internal E5 library dependency"
    elif not scanned:
        note = "no build facts for this repo: its build files were not read"
    return {"ok": True, "repo": repo, "publishes": pub, "depends_on": out,
            "used_by": sorted(used, key=lambda u: (u["outside_platform"], u["repo"])),
            "inherited_by": sorted(inherited, key=lambda x: x["repo"]) or None,
            "builds_from_images": images or None, "image_used_by": image_users or None,
            "note": note,
            "caveat": "latest_in_line = newest release on JFrog in the same major line as the pin. "
                      "Releases outside every consumer's line are listed in "
                      "graph/library-versions.json for the owner to confirm."}


# --------------------------------------------------------------------------- flows, contracts
def flow(name: str | None = None) -> dict:
    """A task path across subsystems, by flow name, task name or fbName. Exact names only."""
    import task_flows as TF
    flows = TF.load()
    rows = _task_name_rows()
    if not name:
        return {"ok": True, "flows": [{"flow": f["name"],
                                       "task_names": (f.get("applies_to") or {}).get("task_names")}
                                      for f in flows],
                "task_names": sorted(rows)}
    key = name.strip()
    # an fbName is translated to its task name through the authored routing join
    by_fb = {r.get("fb_name"): t for t, r in rows.items() if r.get("fb_name")}
    task = key if key in rows else by_fb.get(key)
    for f in flows:
        names = (f.get("applies_to") or {}).get("task_names") or []
        if f["name"] == key or (task and task in names) or key in names:
            t = task or (key if key in names else (names[0] if names else None))
            steps = TF.resolve(f, t)
            gql = [{"graphql": g["graphql"], "at": f"{g['path']}:{g['line']}"}
                   for g in _jsonl(GRAPH / "gql-task-facts.jsonl") if g["task_name"] == t]
            return {"ok": True, "flow": f["name"], "task": t,
                    "triggered_by_graphql": gql or None,
                    "routing": rows.get(t),
                    "summary": " ".join((f.get("summary") or "").split()),
                    "steps": [{k: s.get(k) for k in ("from", "to", "via", "carries",
                                                     "condition", "evidence")} for s in steps],
                    "source": "knowledge/flows/task-flows.yaml (authored; evidence lines are "
                              "re-verified by sync)"}
    if task:
        return {"ok": True, "flow": None, "task": task, "routing": rows.get(task),
                "note": "this task has a routing row but no authored multi-hop flow"}
    return {"ok": False, "error": f"no flow, task name or fbName {key!r}",
            "flows": [f["name"] for f in flows], "task_names": sorted(rows),
            "fb_names": sorted(by_fb)}


def _task_name_rows() -> dict:
    sys.path.insert(0, str(HERE / "release"))
    import minyaml                       # stdlib-only: the retrieval path takes no dependency
    p = KN / "flows" / "task-names.yaml"
    if not p.is_file():
        return {}
    return {r["task_name"]: r for r in (minyaml.load(p.read_text()) or {}).get("rows", [])}


def contract(identifier: str) -> dict:
    """Every extracted fact whose name IS this identifier (or ends with `.identifier`).

    Exact, case-sensitive. Reports where each is DECLARED; a repo that vendors a copy of a
    proto declares it too, so `declared in` is not `served by`."""
    ident = identifier.strip()
    hits = []
    for f in _jsonl(FACTS):
        n = f["name"]
        if n == ident or n.endswith("." + ident) or f.get("contract_key", "").endswith(":" + ident):
            if f["kind"] == "ConfigKey" and any(t in n.lower() for t in _TOPICISH):
                continue
            hits.append({"component": f.get("component"), "kind": f["kind"], "name": n,
                         "at": f"{f.get('path')}:{f.get('line')}", "branch": f.get("branch"),
                         "commit": f.get("commit")})
    docs = sorted(_rel(p) for d in ("contracts", "tables") for p in (KN / d).glob("*.md")
                  if p.stem == ident.lower() or p.stem == ident)
    # who actually SERVES and CALLS it, from the code (tools/rpc_graph.py)
    rpc = ident.split(".")[-1].lower()
    rf = _jsonl(GRAPH / "rpc-facts.jsonl")
    pick = lambda kind: sorted({(f["component"], f"{f['path']}:{f['line']}") for f in rf
                                if f["kind"] == kind and (f["rpc"].lower() == rpc
                                                          or f["service"].lower() == rpc)})[:40]
    served, called = pick("ServesRpc"), pick("CallsRpc")
    if not hits and not docs and not served and not called:
        return {"ok": False, "error": f"no extracted fact or document named {ident!r}",
                "note": "exact names only, e.g. UpdateFbWorkerPodDetails, "
                        "e5.platform.ss.ToggleTrigger.v1.ToggleTrigger, deployment_version"}
    return {"ok": True, "identifier": ident,
            "served_by": [{"component": c, "at": a} for c, a in served] or None,
            "called_by": [{"component": c, "at": a} for c, a in called] or None,
            "unserved": bool(called and not served) or None,
            "declared": hits[:60], "count": len(hits), "documents": docs,
            "note": "served_by / called_by come from the code (tools/rpc_graph.py). declared = "
                    "the file defines it; several repos copy the same proto. `unserved` means "
                    "callers exist and no indexed repo serves it at its release."}


def freshness() -> dict:
    import kb_core as core
    return core.freshness()


# --------------------------------------------------------------------------- Level 2
_CALLS: dict = {"mtime": None, "rows": {}, "callers": {}}


def _call_index():
    m = CALL_FACTS.stat().st_mtime if CALL_FACTS.is_file() else None
    if _CALLS["mtime"] != m:
        rows = {r["id"]: r for r in _jsonl(CALL_FACTS)}
        callers: dict = {}
        for r in rows.values():
            for to, line in r["calls"]:
                callers.setdefault(to, []).append((r["id"], line))
        _CALLS.update(mtime=m, rows=rows, callers=callers)
    return _CALLS["rows"], _CALLS["callers"]


def _simple(owner: str) -> str:
    return owner.split(":")[-1].split(".")[-1].split("$")[-1]


def _match(rows: dict, name: str, repo: str | None) -> list[str]:
    """Exact forms only: a full id, `Class#method`, `Class.method`, `module:Class.method`,
    or a bare method name. Never a substring."""
    name = name.strip()
    if name in rows:
        return [name]
    out = []
    for i, r in rows.items():
        if repo and r["repo"] != repo:
            continue
        own, n = r["owner"], r["name"]
        forms = {n, f"{_simple(own)}#{n}", f"{_simple(own)}.{n}", f"{own}#{n}", f"{own}.{n}",
                 i.split("@")[0]}
        if r["lang"] == "python":
            q = i.split(":", 1)[-1]
            forms |= {q, i}
        if name in forms:
            out.append(i)
    return out


_NOISE = re.compile(r"^(builtins|typing|logging|Map|HashMap|List|ArrayList|Set|HashSet|String|"
                   r"StringBuilder|Optional|Objects|Collectors|Stream|Integer|Long|Boolean|"
                   r"Double|Math|Arrays|Collections|LocalDateTime|Instant|UUID|Logger|log|"
                   r"LOGGER|logger|System|Thread|Exception|RuntimeException)\.")


# Calls that leave the service by a transport other than gRPC. A declared list, matched on
# the receiver TYPE the resolver recorded, never on a method name alone.
_TRANSPORT = re.compile(r"^(\w*Kafka\w*|\w*Producer|\w*OutboundEndpoint|requests|httpx|aiohttp|"
                        r"urllib\.request|HttpClient|RestTemplate|WebClient|OkHttpClient|"
                        r"\w*Jenkins\w*|boto3|\w*S3Client|\w*SqsClient)\.")


def _sites(r: dict, rpc_by_file: dict) -> dict:
    """What a method does at its own lines that leaves the service."""
    here = [f for f in rpc_by_file.get((r["repo"], r["path"]), ())
            if r["line"] <= f["line"] <= r["end"]]
    calls = {f["line"]: f"{f['service']}.{f['rpc']} (line {f['line']})"
             for f in here if f["kind"] == "CallsRpc"}
    for t, l in r["ext"]:      # a stub call the RPC extractor did not record at that line
        if "Grpc." in t and "Stub." in t and l not in calls:
            calls[l] = f"{t.split('Grpc.')[0]}.{t.rsplit('.', 1)[-1]} (line {l}, stub call)"
    serves = sorted({f"{f['service']}.{f['rpc']}" for f in here if f["kind"] == "ServesRpc"})
    out = {}
    if serves:
        out["serves_rpc"] = serves
    if calls:
        out["calls_rpc"] = [calls[l] for l in sorted(calls)]
    sends = [f"{t} (line {l})" for t, l in r["ext"] if _TRANSPORT.match(t)]
    if sends:
        out["sends"] = sends
    ext = [f"{t} (line {l})" for t, l in r["ext"]
           if "Grpc." not in t and not _NOISE.match(t) and not _TRANSPORT.match(t)]
    if ext:
        out["calls_outside_repo"] = ext[:12] + ([f"... {len(ext) - 12} more"] if len(ext) > 12 else [])
    return out


def trace(name: str, repo: str | None = None, depth: int = 3, direction: str = "out") -> dict:
    """Level 2: what one handler does, method by method, from the extracted call graph.

    `direction="out"` walks the calls it makes; `"in"` walks who calls it. Each step carries
    repo@branch:path:line and the RPCs it calls or serves at its own lines. A name matching
    more than one method in different classes returns the candidates instead of choosing."""
    rows, callers = _call_index()
    if not rows:
        return {"ok": False, "error": "no call graph yet - run `./e5 sync all` (or "
                "`python3 tools/call_graph.py extract`)"}
    if repo:
        comp = resolve(repo)
        if comp:
            repo = (_catalog()["Component"][comp].get("source") or {}).get("repo") or repo
    roots = _match(rows, name, repo)
    if not roots:
        return {"ok": False, "error": f"no method named {name!r}"
                + (f" in {repo}" if repo else "")
                + ". Use an exact name: `Class#method`, `Class.method`, `module:func`, or "
                  "the full id from a previous trace."}
    owners = {(rows[i]["repo"], rows[i]["owner"]) for i in roots}
    if len(owners) > 1:
        return {"ok": False, "ambiguous": True,
                "error": f"{name!r} names methods in {len(owners)} classes; pass the class "
                         "(`Class#method`) or repo=",
                "candidates": sorted(f"{rows[i]['repo']}: {i}" for i in roots)[:40]}
    rpc_by_file: dict = {}
    for f in _jsonl(RPC_FACTS):
        rpc_by_file.setdefault((f["repo"], f["path"]), []).append(f)

    steps, seen, cap = [], set(), 120
    memo: dict = {}

    def reaches_out(i, left, stack=()):
        """Does this method, or anything it calls within `left` levels, leave the service?"""
        key = (i, left)
        if key in memo:
            return memo[key]
        r = rows[i]
        st = _sites(r, rpc_by_file)
        hit = bool(st.get("calls_rpc") or st.get("serves_rpc") or st.get("sends"))
        if not hit and left > 0 and i not in stack:
            hit = any(reaches_out(t, left - 1, stack + (i,)) for t, _ in r["calls"] if t in rows)
        memo[key] = hit
        return hit

    def at(r):
        return f"{r['repo']}@{r['branch']}:{r['path']}:{r['line']}"

    def walk(i, d, via_line):
        if len(steps) >= cap:
            return
        r = rows[i]
        step = {"depth": d, "method": i, "at": at(r)}
        if via_line:
            step["from_line" if direction == "out" else "calls_at_line"] = via_line
        if i in seen:
            step["seen_above"] = True
            steps.append(step); return
        seen.add(i)
        step.update(_sites(r, rpc_by_file))
        if direction == "out" and r.get("unresolved"):
            step["unresolved_calls"] = len(r["unresolved"])
        steps.append(step)
        if d >= depth:
            nxt = r["calls"] if direction == "out" else callers.get(i, [])
            if nxt:
                step["more"] = len(nxt)
            return
        nxt = r["calls"] if direction == "out" else callers.get(i, [])
        done, leaves = set(), []
        for to, line in nxt:
            if to in rows and to not in done:
                done.add(to)
                c = rows[to]
                # a callee whose whole subtree never leaves the service (a getter, a logger
                # helper) is listed on its caller, not given a step of its own
                if direction == "out" and not reaches_out(to, depth - d):
                    leaves.append(f"{to.split('@')[0]} (line {line})")
                    continue
                walk(to, d + 1, line)
        if leaves:
            step["also_calls"] = leaves[:15] + ([f"... {len(leaves) - 15} more"]
                                                if len(leaves) > 15 else [])

    for i in roots:
        walk(i, 0, None)
    r0 = rows[roots[0]]
    return {"ok": True, "direction": direction, "depth": depth, "steps": steps,
            "truncated": len(steps) >= cap, "resolution": r0["provenance"],
            "note": ("Calls are resolved from declared types (Java) or by pyright (Python). "
                     "`unresolved_calls` counts calls whose receiver could not be typed; "
                     "they are not followed, so a path can continue past what is shown. "
                     "Read the code at `at` before relying on a step.")}


TOOLS = {"overview": overview, "read": read, "component": component,
         "dependencies": dependencies, "flow": flow, "contract": contract,
         "freshness": freshness, "trace": trace}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("tool", choices=sorted(TOOLS))
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args()
    fn = TOOLS[a.tool]
    print(json.dumps(fn(a.arg) if a.arg is not None else fn(), indent=1, default=str)[:20000])

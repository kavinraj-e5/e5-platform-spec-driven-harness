#!/usr/bin/env python3
"""Who SERVES and who CALLS each gRPC method - not just who declares it.

    python3 tools/rpc_graph.py extract    -> graph/rpc-facts.jsonl
    python3 tools/rpc_graph.py check      RPCs that are called but served by no indexed repo

Several repos vendor the same .proto, so "declared in" (kb_contract) cannot say which repo
runs an RPC. Measured 2026-11-14: DisableDeployment declared in 2 repos, UpdateFbWorkerPodDetails
in 3, sendEvent in 6. This reads the code instead, with tree-sitter, at each repo's indexed
release, from the bare mirrors:

  SERVES  Java: a class extending `XxxGrpc.XxxImplBase`; each method it declares is an RPC
          Python: a class subclassing `xxx_pb2_grpc.XxxServicer`; each method it defines
  CALLS   Java: a call on a variable, field or parameter whose declared type is
          `XxxGrpc.Xxx[Blocking|Future]Stub`, also through `.withDeadlineAfter(...)`-style
          chains, or on `getXxxStub()`-style getters whose return type is a stub
          Python: `client.call("Rpc", ...)` in a function that names `xxx_pb2_grpc.XxxStub`,
          or `v.Rpc(...)` where `v = xxx_pb2_grpc.XxxStub(...)`

Java stub methods are lowerCamel (disableDeployment); RPCs are compared case-insensitively on
the first letter. A call whose service cannot be determined is not recorded - never guessed.
"""
from __future__ import annotations
import argparse, collections, json, os, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kb_core as K
import catalog as C

KB = HERE.parent
OUT = KB / "graph" / "rpc-facts.jsonl"
STUB_CHAIN = {"withDeadlineAfter", "withDeadline", "withWaitForReady", "withInterceptors",
              "withCallCredentials", "withCompression", "withExecutor", "withMaxInboundMessageSize",
              "withMaxOutboundMessageSize", "withOption"}
_IMPLBASE = re.compile(r"(\w+)Grpc\.(\w+)ImplBase\b")
_STUBTYPE = re.compile(r"(\w+)Grpc\.(\w+?)(?:Blocking|Future)?Stub\b")
_PYSTUB = re.compile(r"_pb2_grpc\.(\w+?)Stub\b")
_PYCALL = re.compile(r"\.call\(\s*[\"'](\w+)[\"']")
_PYSERVICER = re.compile(r"class\s+(\w+)\s*\(\s*[\w.]*?(\w+)Servicer\s*\)")


def _git(gd, *a):
    return subprocess.run(["git", "-C", gd, *a], capture_output=True, text=True).stdout


def _norm(m: str) -> str:
    return m[:1].upper() + m[1:] if m else m


def _component_for(repo: str, path: str, cat) -> str:
    import fnmatch
    hits = []
    for n, c in cat["Component"].items():
        src = c.get("source") or {}
        if src.get("repo") != repo:
            continue
        pats = src.get("paths") or []
        if not pats or any(fnmatch.fnmatch(path, p) for p in pats):
            hits.append(n)
    return hits[0] if len(hits) == 1 else (repo if not hits else sorted(hits)[0])


# ------------------------------------------------------------------------------ java
def _java(repo, path, src: bytes, out):
    import tree_sitter_java as tsj
    from tree_sitter import Language, Parser
    P = Parser(Language(tsj.language()))
    tree = P.parse(src)
    txt = lambda n: src[n.start_byte:n.end_byte].decode(errors="replace")

    def walk_classes(node):
        for ch in node.children:
            if ch.type in ("class_declaration",):
                sc = ch.child_by_field_name("superclass")
                m = _IMPLBASE.search(txt(sc)) if sc is not None else None
                body = ch.child_by_field_name("body")
                if m and body is not None:
                    svc = m.group(2)
                    for mm in body.children:
                        if mm.type == "method_declaration":
                            nm = txt(mm.child_by_field_name("name"))
                            out.append(("ServesRpc", svc, _norm(nm), path, mm.start_point[0] + 1))
                if body is not None:
                    walk_classes(body)
            elif ch.children:
                walk_classes(ch)
    walk_classes(tree.root_node)

    # declared stub types: fields, locals, params; and methods that RETURN a stub
    stub_vars, stub_getters = {}, {}
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in ("field_declaration", "local_variable_declaration"):
            t = n.child_by_field_name("type")
            m = _STUBTYPE.search(txt(t)) if t is not None else None
            if m:
                for d in n.children:
                    if d.type == "variable_declarator":
                        stub_vars[txt(d.child_by_field_name("name"))] = m.group(2)
        elif n.type in ("formal_parameter",):
            t = n.child_by_field_name("type"); nm = n.child_by_field_name("name")
            m = _STUBTYPE.search(txt(t)) if t is not None else None
            if m and nm is not None:
                stub_vars[txt(nm)] = m.group(2)
        elif n.type == "method_declaration":
            t = n.child_by_field_name("type")
            m = _STUBTYPE.search(txt(t)) if t is not None else None
            if m:
                stub_getters[txt(n.child_by_field_name("name"))] = m.group(2)
        stack.extend(n.children)

    def root_service(obj):
        """Follow `a.withDeadlineAfter(..).b` back to a stub variable or getter."""
        while obj is not None:
            if obj.type == "identifier":
                return stub_vars.get(txt(obj))
            if obj.type == "field_access":
                return stub_vars.get(txt(obj.child_by_field_name("field")))
            if obj.type == "method_invocation":
                nm = txt(obj.child_by_field_name("name"))
                if nm in STUB_CHAIN:
                    obj = obj.child_by_field_name("object"); continue
                if obj.child_by_field_name("object") is None or \
                        txt(obj.child_by_field_name("object")) == "this":
                    return stub_getters.get(nm)
                return None
            return None
        return None

    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "method_invocation":
            nm = txt(n.child_by_field_name("name"))
            obj = n.child_by_field_name("object")
            if nm not in STUB_CHAIN and obj is not None:
                svc = root_service(obj)
                if svc:
                    out.append(("CallsRpc", svc, _norm(nm), path, n.start_point[0] + 1))
        stack.extend(n.children)


# ------------------------------------------------------------------------------ python
def _python(repo, path, src: str, out):
    lines = src.splitlines()
    for m in _PYSERVICER.finditer(src):
        svc = m.group(2)
        start = src[:m.start()].count("\n")
        indent = None
        for i in range(start + 1, len(lines)):
            l = lines[i]
            if l.strip() and not l.startswith((" ", "\t")):
                break
            dm = re.match(r"(\s+)(?:async\s+)?def\s+(\w+)\s*\(\s*self", l)
            if dm and (indent is None or len(dm.group(1)) == indent):
                indent = len(dm.group(1))
                if not dm.group(2).startswith("_"):
                    out.append(("ServesRpc", svc, dm.group(2), path, i + 1))
    # per top-level/def block: stubs named in it, and the calls made in it
    blocks, cur, start = [], [], 0
    for i, l in enumerate(lines):
        if re.match(r"\s*(async\s+)?def\s+\w+", l) and cur:
            blocks.append((start, cur)); cur, start = [], i
        cur.append(l)
    blocks.append((start, cur))
    # one-hop data flow: a function that forwards ONE OF ITS PARAMETERS to `.call(...)` under
    # a stub is an RPC wrapper; each literal its callers pass is an RPC call. workflow-ops
    # polls AKST this way (`__check_for("getTask")` -> `client.call(method, ...)`), which a
    # literal-only rule missed (recall check 2026-11-14: 25/26 call edges confirmed before).
    wrappers = {}
    for start, block in blocks:
        head = re.match(r"\s*(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)", block[0]) if block else None
        if not head:
            continue
        params = [x.split(":")[0].split("=")[0].strip() for x in head.group(2).split(",")]
        text = "\n".join(block)
        stubs = set(_PYSTUB.findall(text))
        m = re.search(r"\.call\(\s*(\w+)\s*[,)]", text)
        if m and m.group(1) in params and len(stubs) == 1:
            wrappers[head.group(1)] = next(iter(stubs))
    for fname, svc in wrappers.items():
        for i, l in enumerate(lines):
            for rpc in re.findall(rf"\b{re.escape(fname)}\(\s*[\"'](\w+)[\"']", l):
                out.append(("CallsRpc", svc, rpc, path, i + 1))
    for start, block in blocks:
        text = "\n".join(block)
        stubs = set(_PYSTUB.findall(text))
        var_stub = {v: s for v, s in re.findall(r"(\w+)\s*=\s*[\w.]*_pb2_grpc\.(\w+?)Stub\s*\(", text)}
        for j, l in enumerate(block):
            for rpc in _PYCALL.findall(l):
                if len(stubs) == 1:
                    out.append(("CallsRpc", next(iter(stubs)), rpc, path, start + j + 1))
            for v, s in var_stub.items():
                for rpc in re.findall(rf"\b{re.escape(v)}\.([A-Z]\w+)\s*\(", l):
                    out.append(("CallsRpc", s, rpc, path, start + j + 1))


def extract() -> list[dict]:
    cat = C.load()
    facts = []
    for r in K._registry_repos():
        gd = os.path.expanduser(r.get("mirror", ""))
        br = r.get("branch")
        lang = r.get("language")
        if not br or not os.path.isdir(gd) or lang not in ("java", "python"):
            continue
        ref = f"refs/heads/{br}"
        pat = ("ImplBase\\|Stub\\b\\|Grpc\\." if lang == "java" else "_pb2_grpc")
        files = [l.split(":", 1)[1] for l in _git(gd, "grep", "-l", pat, ref, "--",
                 "*.java" if lang == "java" else "*.py").splitlines() if ":" in l]
        files = [f for f in files if "/test" not in f and not f.startswith("test")
                 and "generated" not in f and "_pb2" not in os.path.basename(f)]
        out = []
        for f in files:
            body = _git(gd, "show", f"{ref}:{f}")
            try:
                (_java(r["id"], f, body.encode(), out) if lang == "java" else _python(r["id"], f, body, out))
            except Exception as e:
                print(f"  {r['id']} {f}: {type(e).__name__}", file=sys.stderr)
        seen = set()
        for kind, svc, rpc, path, line in out:
            key = (kind, svc, rpc, path, line)
            if key in seen:
                continue
            seen.add(key)
            facts.append({"kind": kind, "service": svc, "rpc": rpc, "repo": r["id"],
                          "component": _component_for(r["id"], path, cat), "path": path,
                          "line": line, "branch": br, "commit": r.get("commit"),
                          "provenance": "code", "confidence": "EXTRACTED"})
    return facts


def unserved(facts: list[dict]) -> list[tuple]:
    served = {(f["service"], f["rpc"].lower()) for f in facts if f["kind"] == "ServesRpc"}
    services_served = {f["service"] for f in facts if f["kind"] == "ServesRpc"}
    out = collections.defaultdict(list)
    for f in facts:
        if f["kind"] == "CallsRpc" and f["service"] in services_served \
                and (f["service"], f["rpc"].lower()) not in served:
            out[(f["service"], f["rpc"])].append(f"{f['repo']} {f['path']}:{f['line']}")
    return sorted(out.items())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["extract", "check"])
    a = ap.parse_args()
    if a.cmd == "extract":
        facts = extract()
        OUT.write_text("".join(json.dumps(f, sort_keys=True) + "\n" for f in facts))
        c = collections.Counter(f["kind"] for f in facts)
        print(f"{c['ServesRpc']} served, {c['CallsRpc']} call sites -> {OUT.relative_to(KB)}")
        return 0
    facts = [json.loads(l) for l in OUT.read_text().splitlines()] if OUT.is_file() else []
    gaps = unserved(facts)
    for (svc, rpc), sites in gaps:
        print(f"  WARN  {svc}.{rpc} is called but no indexed repo serves it: {', '.join(sites[:3])}")
    print(f"{len(gaps)} called RPC(s) with no server in the indexed release of the service's repo")
    return 0         # advisory: a server may be outside the index


if __name__ == "__main__":
    sys.exit(main())

"""The distribution model (2026-11-15): a teammate clones the harness, runs setup, fills in
credentials, runs init (checks only), then sync, which clones every platform repo into
platform/<system>/<repo>, compiles, runs the extractors and pyright, and rebuilds the
knowledge base. These tests pin that shape and the pieces that implement it."""
import json, shutil, subprocess, sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
KB = HERE.parent
sys.path.insert(0, str(HERE))


# ----------------------------------------------------------------------------- shape
def test_init_clones_nothing():
    src = (HERE / "init.py").read_text()
    assert '"mirror.py"' not in src and "MIRRORING" not in src, \
        "init must only check; cloning belongs to sync"


def test_sync_all_clones_checks_out_compiles_and_builds_the_call_graph_in_order():
    e5 = (KB / "e5").read_text()
    block = e5[e5.index('if cmd == "sync" and rest[:1] == ["all"]'):]
    order = [block.index(f'"{t}"') for t in
             ("mirror.py", "checkouts.py", "build_step.py", "sync.py", "rpc_graph.py",
              "call_graph.py", "gen_system_readmes.py")]
    assert order == sorted(order)
    assert 'main(["check"])' in block, "the gates run at the end of sync all"


def test_level2_steps_are_advisory_and_extractors_are_not():
    e5 = (KB / "e5").read_text()
    assert 'advisory = {"checkouts.py", "build_step.py", "call_graph.py"}' in e5


def test_platform_checkouts_are_never_committed():
    lines = (KB / ".gitignore").read_text().splitlines()
    assert "/platform/" in lines


def test_every_repo_has_a_checkout_folder_named_like_knowledge():
    import checkouts, mirror as M
    folders = checkouts._folders()
    for r in M.registry()[1]:
        assert r["id"] in folders, f"{r['id']} would land in platform/other/"
        top = folders[r["id"]].split("/")[0]
        assert (KB / "knowledge" / top).is_dir(), f"{top} is not a knowledge/ folder"
    path = {r["id"]: r["path"].relative_to(checkouts.ROOT).as_posix() for r in checkouts.plan()}
    assert path["workflow-ops"] == "subsystems/workflow-ops"
    assert path["platform-core"] == "workflow-development/workflow-library/platform-core"
    assert path["rf-fb-worker-core"] == "app-development/app-library/rf-fb-worker-core"
    assert path["ui-synthesizer"] == "app-development/ui-synthesizer"   # no doubled level
    assert path["akst-service"] == "communication/akst-service"


# ----------------------------------------------------------------------------- checkouts
def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _bare_with_release(tmp: Path) -> tuple[Path, Path]:
    work = tmp / "src"
    work.mkdir()
    _git("init", "-q", "-b", "release/v1.0.0", cwd=work)
    (work / "a.txt").write_text("one\n")
    _git("add", ".", cwd=work)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "1", cwd=work)
    bare = tmp / "cache" / "demo.git"
    _git("clone", "-q", "--mirror", str(work), str(bare))
    return work, bare


def test_checkout_is_created_left_alone_when_edited_and_moved_when_the_release_moves(tmp_path,
                                                                                    monkeypatch):
    import checkouts as K
    work, bare = _bare_with_release(tmp_path)
    row = {"id": "demo", "branch": "release/v1.0.0", "mirror": bare,
           "path": tmp_path / "platform" / "subsystems" / "demo"}
    monkeypatch.setattr(K, "ROOT", tmp_path / "platform")
    monkeypatch.setattr(K, "KB", tmp_path)
    assert K.state(row) == "missing"
    assert K.sync([row]) == 0 and K.state(row) == "ok"
    assert (row["path"] / "a.txt").read_text() == "one\n"
    # no config of its own: it shares the mirror's, whose origin carries no credential
    assert _git("-C", str(row["path"]), "config", "remote.origin.url") == \
        _git("-C", str(bare), "config", "remote.origin.url")

    (row["path"] / "a.txt").write_text("local edit\n")
    assert K.state(row) == "dirty"
    K.sync([row])
    assert (row["path"] / "a.txt").read_text() == "local edit\n", "sync must not touch edits"
    _git("checkout", "--", "a.txt", cwd=row["path"])

    (work / "a.txt").write_text("two\n")
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "2", cwd=work)
    _git("-C", str(bare), "fetch", "-q", str(work), "+refs/heads/*:refs/heads/*")
    assert K.state(row) == "behind"
    K.sync([row])
    assert K.state(row) == "ok" and (row["path"] / "a.txt").read_text() == "two\n"


def test_crlf_only_differences_are_not_local_edits(tmp_path, monkeypatch):
    import checkouts as K
    work = tmp_path / "src"
    work.mkdir()
    _git("init", "-q", "-b", "release/v1.0.0", cwd=work)
    (work / ".gitattributes").write_text("*.py text eol=lf\n")
    (work / "x.py").write_bytes(b"a = 1\r\nb = 2\r\n")      # committed CRLF under eol=lf
    _git("-c", "core.autocrlf=false", "add", ".", cwd=work)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "1", cwd=work)
    bare = tmp_path / "cache" / "crlf.git"
    _git("clone", "-q", "--mirror", str(work), str(bare))
    row = {"id": "crlf", "branch": "release/v1.0.0", "mirror": bare,
           "path": tmp_path / "platform" / "crlf"}
    monkeypatch.setattr(K, "ROOT", tmp_path / "platform")
    monkeypatch.setattr(K, "KB", tmp_path)
    K.sync([row])
    assert K.state(row) == "ok"


# ----------------------------------------------------------------------------- call graph
JAVA = {
 "src/main/java/p/Store.java": "package p;\npublic interface Store { int get(String k); }\n",
 "src/main/java/p/DbStore.java":
   "package p;\npublic class DbStore implements Store {\n"
   "  public int get(String k) { return k.length(); }\n}\n",
 "src/main/java/p/Handler.java":
   "package p;\nimport java.util.Map;\npublic class Handler {\n"
   "  private Store store;\n  private Map<String,String> m;\n"
   "  public int handle(String k) {\n"
   "    m.put(k, k);\n"                  # external, declared type
   "    foo().bar();\n"                  # a chain: unresolved, never guessed
   "    return store.get(k);\n"          # interface -> every implementation
   "  }\n  Handler foo() { return this; }\n  void bar() {}\n}\n",
 "src/test/java/p/HandlerTest.java": "package p;\nclass HandlerTest { void t() { new Handler(); } }\n",
 "src/main/java/p/Gen.java": "// Generated by the protocol buffer compiler.  DO NOT EDIT!\n"
                             "package p;\nclass Gen { void x() {} }\n",
}


def _write(root: Path, files: dict):
    for rel, body in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)


def test_java_call_graph_resolves_declared_types_and_never_guesses(tmp_path):
    pytest.importorskip("tree_sitter_java")
    import call_graph as G
    _write(tmp_path, JAVA)
    rows = {r["id"].split("@")[0]: r for r in G.java_rows("demo", tmp_path)}
    h = rows["p.Handler#handle"]
    targets = {t.split("@")[0] for t, _ in h["calls"]}
    assert "p.Store#get" in targets and "p.DbStore#get" in targets
    assert ["Map.put", 7] in h["ext"]
    assert [n for n, _ in h["unresolved"]] == ["bar"], "a chain is recorded, not linked"
    assert not any("HandlerTest" in i or "Gen#" in i for i in rows), \
        "tests and generated code are skipped"


def test_python_call_graph_uses_pyright_inference(tmp_path):
    import lsp_client as L, call_graph as G
    cmd = L.find_pyright()
    if not cmd:
        pytest.skip("pyright-langserver not installed")
    _write(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/svc.py": "class Service:\n    def run(self):\n        return 1\n\n"
                      "def make():\n    return Service()\n",
        "pkg/main.py": "from pkg.svc import make\n\n"
                       "def entry():\n    s = make()\n    return s.run()\n",
        "tests/test_x.py": "def test_x():\n    pass\n"})
    rows = {r["id"]: r for r in G.python_rows("demo", tmp_path, cmd)}
    called = {t for t, _ in rows["pkg.main:entry"]["calls"]}
    # `s` has no written type; only inference knows it is a Service
    assert {"pkg.svc:make", "pkg.svc:Service.run"} <= called
    assert not any(i.startswith("tests.") for i in rows)


# ----------------------------------------------------------------------------- kb_trace
def _facts(tmp_path, monkeypatch):
    import kb_nav as N
    rows = [
        {"kind": "Method", "lang": "java", "repo": "svc", "branch": "release/v1", "commit": "c",
         "id": "a.H#handle@10", "name": "handle", "owner": "a.H", "path": "H.java",
         "line": 10, "end": 20, "calls": [["a.U#util@30", 12], ["a.G#get@40", 13]],
         "ext": [["KafkaOutboundEndpoint.send", 15]], "unresolved": [],
         "provenance": "tree-sitter declared-type"},
        {"kind": "Method", "lang": "java", "repo": "svc", "branch": "release/v1", "commit": "c",
         "id": "a.U#util@30", "name": "util", "owner": "a.U", "path": "U.java",
         "line": 30, "end": 35, "calls": [], "ext": [], "unresolved": [],
         "provenance": "tree-sitter declared-type"},
        {"kind": "Method", "lang": "java", "repo": "svc", "branch": "release/v1", "commit": "c",
         "id": "a.G#get@40", "name": "get", "owner": "a.G", "path": "G.java",
         "line": 40, "end": 45, "calls": [], "ext": [], "unresolved": [],
         "provenance": "tree-sitter declared-type"},
        {"kind": "Method", "lang": "java", "repo": "svc", "branch": "release/v1", "commit": "c",
         "id": "a.X#get@5", "name": "get", "owner": "a.X", "path": "X.java",
         "line": 5, "end": 6, "calls": [], "ext": [], "unresolved": [],
         "provenance": "tree-sitter declared-type"}]
    cf = tmp_path / "call-facts.jsonl"
    cf.write_text("".join(json.dumps(r) + "\n" for r in rows))
    rf = tmp_path / "rpc-facts.jsonl"
    rf.write_text(json.dumps({"kind": "CallsRpc", "repo": "svc", "path": "G.java", "line": 42,
                              "service": "DMS", "rpc": "GetThing"}) + "\n")
    monkeypatch.setattr(N, "CALL_FACTS", cf)
    monkeypatch.setattr(N, "RPC_FACTS", rf)
    N._CALLS["mtime"] = None
    return N


def test_trace_walks_to_rpc_and_send_sites_and_folds_dead_ends(tmp_path, monkeypatch):
    N = _facts(tmp_path, monkeypatch)
    t = N.trace("H#handle")
    assert t["ok"]
    root, *rest = t["steps"]
    assert root["at"] == "svc@release/v1:H.java:10"
    assert root["sends"] == ["KafkaOutboundEndpoint.send (line 15)"]
    assert any(s["method"] == "a.G#get@40" and s["calls_rpc"] == ["DMS.GetThing (line 42)"]
               for s in rest)
    assert root["also_calls"] == ["a.U#util (line 12)"], "a dead end is listed, not walked"


def test_trace_matches_exact_names_only_and_never_picks_between_classes(tmp_path, monkeypatch):
    N = _facts(tmp_path, monkeypatch)
    amb = N.trace("get")
    assert not amb["ok"] and amb["ambiguous"] and len(amb["candidates"]) == 2
    assert N.trace("G#get")["ok"]
    assert not N.trace("hand")["ok"], "no substring matching"


def test_trace_callers(tmp_path, monkeypatch):
    N = _facts(tmp_path, monkeypatch)
    t = N.trace("U#util", direction="in")
    assert [s["method"] for s in t["steps"]] == ["a.U#util@30", "a.H#handle@10"]
    assert t["steps"][1]["calls_at_line"] == 12


# ----------------------------------------------------------------------------- build step
def test_build_step_rebuilds_only_what_moved_and_keeps_the_rest(tmp_path, monkeypatch):
    import build_step as B
    S = B.S
    lf = tmp_path / "layer-facts.jsonl"
    lf.write_text(json.dumps({"kind": "CodeUnit", "repo": "same", "name": "x"}) + "\n"
                  + json.dumps({"kind": "CodeUnit", "repo": "moved", "name": "old"}) + "\n")
    monkeypatch.setattr(S, "LAYER_FACTS", lf)
    monkeypatch.setattr(S, "GRAPH", tmp_path)
    monkeypatch.setattr(S, "WORK", tmp_path / "work")
    monkeypatch.setattr(B, "STATE", tmp_path / "build-state.json")
    monkeypatch.setattr(B, "KB", tmp_path)
    (tmp_path / "build-state.json").write_text(json.dumps(
        {"same": {"commit": "c1", "status": "ok"}, "moved": {"commit": "c1", "status": "ok"}}))
    monkeypatch.setattr(S, "jvm_repos", lambda: {"same": {"branch": "b"}, "moved": {"branch": "b"}})
    monkeypatch.setattr(B, "_commit", lambda repo, br: "c1" if repo == "same" else "c2")
    built = []
    monkeypatch.setattr(S, "build_repo", lambda repo, *a: built.append(repo) or
                        {"ok": True, "tree": tmp_path, "ref": "b", "how": "built"})
    monkeypatch.setattr(S, "index_repo", lambda repo, tree: {"ok": True, "classes": 1, "edges": []})
    monkeypatch.setattr(S, "facts_for", lambda repo, ref, idx:
                        [{"kind": "CodeUnit", "repo": repo, "name": "new"}])
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: None)
    assert B.run(force=False) == 0
    assert built == ["moved"]
    names = {(f["repo"], f["name"]) for f in map(json.loads, lf.read_text().splitlines())}
    assert names == {("same", "x"), ("moved", "new")}
    assert json.loads((tmp_path / "build-state.json").read_text())["moved"]["commit"] == "c2"

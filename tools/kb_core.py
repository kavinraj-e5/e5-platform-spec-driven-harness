"""Shared state for the knowledge base: the facts cache, freshness, the registry.

Retrieval is NOT here any more. The word-overlap tools that lived in this file (locate,
fits, impact, search, ...) were measured at 15-40% on real platform stories and removed
on 2026-11-14 (evals/p0-tool-validation-2026-11-14.md; the old file is in
e5-kb-archive/2026-11-14/tools/kb_core.py). Navigation by path and exact name is in
tools/kb_nav.py.
"""
from __future__ import annotations
import json, re, functools
from pathlib import Path
from typing import Any

KB = Path(__file__).resolve().parent.parent
GRAPH = KB / "graph"
FACTS = GRAPH / "facts.jsonl"
CODE_FACTS = GRAPH / "code-facts.jsonl"
BUILD_FACTS = GRAPH / "build-facts.jsonl"
TASK_FACTS = GRAPH / "async-facts.jsonl"
DEPLOYED = GRAPH / "deployed.json"
SYNC_STATE = GRAPH / "sync-state.json"
KNOWLEDGE = KB / "knowledge"


def est_tokens(obj: Any) -> int:
    return max(1, len(json.dumps(obj, default=str)) // 4)


_cache: dict[str, Any] = {}

# ---- staleness of a long-lived process --------------------------------------------
# The MCP server lives across syncs. Every request re-checks (mtime, size) of its inputs
# and drops the caches if anything moved, so it never answers from pre-sync facts.
_FINGERPRINT: tuple | None = None


def _inputs() -> list[Path]:
    out = [FACTS, CODE_FACTS, BUILD_FACTS, TASK_FACTS, DEPLOYED, SYNC_STATE,
           KB / "registry.yaml"]
    for d in (KB / "catalog", KNOWLEDGE / "flows"):
        if d.is_dir():
            out += sorted(d.glob("*.yaml"))
    return out


def _fingerprint() -> tuple:
    sig = []
    for f in _inputs():
        try:
            st = f.stat()
            sig.append((str(f), st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((str(f), 0, -1))
    return tuple(sig)


def refresh_if_stale() -> bool:
    global _FINGERPRINT
    now = _fingerprint()
    if now == _FINGERPRINT:
        return False
    first, _FINGERPRINT = _FINGERPRINT is None, now
    _cache.clear()
    _known_subjects.cache_clear()
    try:
        import catalog as _C
        _C.load(refresh=True)
    except Exception:
        pass
    return not first


@functools.lru_cache(maxsize=1)
def _known_subjects() -> frozenset:
    names = set()
    try:
        import catalog as _C
        names |= set(_C.load()["Component"])
    except Exception:
        pass
    names |= {r["id"] for r in _registry_repos()}
    return frozenset(names)


def subject(f: dict) -> str:
    """What a fact is about: its Component, else its repo."""
    return f.get("component") or f["repo"]


def facts() -> list[dict]:
    if "facts" not in _cache:
        _cache["facts"] = [json.loads(l) for l in FACTS.open()] if FACTS.exists() else []
    return _cache["facts"]


def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


# ---- freshness ----------------------------------------------------------------------
def _stale_repos() -> list[dict]:
    return [{"repo": r["id"], "branch": r.get("branch", ""), "why": "not on a release branch"}
            for r in _registry_repos()
            if not re.search(r'release/v\d+\.\d+\.\d+', r.get("branch", ""))]


def _moved_since_reconciled() -> list:
    snap = GRAPH / "estate-snapshot.json.gz"
    if not snap.is_file():
        return []
    try:
        import gzip
        with gzip.open(snap, "rt") as fh:
            refs = (json.load(fh).get("refs") or {})
        import mirror as _m
        _root, repos = _m.registry()
        return sorted(r["id"] for r in repos
                      if refs.get(r["id"]) and r.get("commit") and refs[r["id"]] != r["commit"])
    except Exception:
        return []


def freshness(brief: bool = False) -> dict:
    v = _load_json(SYNC_STATE, {})
    repos = components = None
    try:
        import catalog as _cat
        c = _cat.load()["Component"]
        components = len(c)
        repos = len({(x.get("source") or {}).get("repo") for x in c.values()
                     if (x.get("source") or {}).get("repo") not in _cat.NOT_A_REPO
                     and not x.get("unindexed")})
    except Exception:
        pass
    stale = _stale_repos()
    out = {"indexed_repos": repos if repos is not None else len(v),
           "indexed_components": components, "stale": stale, "stale_count": len(stale)}
    if brief:
        return {k: out[k] for k in ("indexed_repos", "indexed_components", "stale_count")}
    out["moved_since_reconciled"] = _moved_since_reconciled()
    return out


# ---- registry -----------------------------------------------------------------------
def _registry_repos() -> list[dict]:
    p = KB / "registry.yaml"
    txt = p.read_text() if p.exists() else ""
    out, cur, inr = [], None, False
    for line in txt.splitlines():
        if line.startswith("repos:"):
            inr = True
            continue
        if inr and re.match(r"^\w", line):
            inr = False
        if not inr:
            continue
        m = re.match(r"\s+-\s+id:\s*(\S+)", line)
        if m:
            cur = {"id": m.group(1)}
            out.append(cur)
            continue
        m = re.match(r"\s+(\w+):\s*(.*)$", line)
        if m and cur is not None:
            cur[m.group(1)] = m.group(2).strip()
    return out


# Contract owner first, consumers after: deployment-mgmt owns the schema, so its migration
# and proto land before anything that regenerates stubs against it.
_OWNER_RANK = {"deployment-mgmt": 0, "platform-core": 1, "task-manager": 1,
               "event-store": 2, "workflow-manager": 3, "workflow-ops": 3,
               "monitoring-service": 3, "event-processor": 4, "webhook-service": 4,
               "audit-service": 4, "interface": 5, "workflow-api": 5}


def merge_order(repos: list[str]) -> list[str]:
    return sorted(repos, key=lambda r: (_OWNER_RANK.get(r, 9), r))

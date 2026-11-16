#!/usr/bin/env python3
"""Content check for authored evidence: does the cited line, AT THE RELEASE THE KB INDEXES
NOW, actually show the thing it is cited for?

Format checks were not enough. On 2026-11-14 the trial found 11 of 16 task-flow citations
pointing at lines that no longer held the hop, while `task_flows.py check` passed, because
it only validated `<repo> <path>:<line>` and that the repo existed.

A token is "shown" at a line when the line (or the two either side, for multi-line calls)
contains the token itself or the name of a constant whose value is the token. Java
dispatches on `WorkflowManagerConstants.SCALE_DOWN`, not on "FbWorkerPodScaleDown".

    python3 tools/evidence.py task-names     verify every routing row
    python3 tools/evidence.py flows          verify every flow step's evidence
"""
from __future__ import annotations
import os, re, subprocess, sys
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "release"))
import kb_core as K
import catalog as C
import minyaml

KB = HERE.parent
WINDOW = 2


@lru_cache(maxsize=1)
def _repos() -> dict:
    return {r["id"]: r for r in K._registry_repos()}


@lru_cache(maxsize=1)
def _component_repo() -> dict:
    return {n: (c.get("source") or {}).get("repo") for n, c in C.load()["Component"].items()}


def repo_of(name: str) -> str | None:
    """A registry repo id, from a repo id or a component name."""
    if name in _repos():
        return name
    r = _component_repo().get(name)
    return r if r in _repos() else None


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    gd = os.path.expanduser(_repos()[repo]["mirror"])
    return subprocess.run(["git", "-C", gd, *args], capture_output=True, text=True)


def _ref(repo: str) -> str:
    return "refs/heads/" + _repos()[repo]["branch"]


@lru_cache(maxsize=512)
def lines(repo: str, path: str) -> tuple | None:
    p = _git(repo, "show", f"{_ref(repo)}:{path}")
    return tuple(p.stdout.splitlines()) if p.returncode == 0 else None


@lru_cache(maxsize=512)
def aliases(repo: str, token: str) -> frozenset:
    """Names of constants in `repo` whose value is exactly `token`."""
    p = _git(repo, "grep", "-h", "-E", f'"{re.escape(token)}"', _ref(repo), "--",
             "*.java", "*.py", "*.kt")
    out = set()
    for l in p.stdout.splitlines():
        m = re.search(r'(\w+)\s*(?::\s*\w+\s*)?=\s*"' + re.escape(token) + '"', l)
        if m:
            out.add(m.group(1))
    return frozenset(out)


def shows(repo: str, path: str, line: int, token: str) -> tuple[bool, str]:
    """(ok, reason)."""
    if repo not in _repos():
        return False, f"unknown repo {repo!r}"
    body = lines(repo, path)
    if body is None:
        return False, f"{path} does not exist at {_repos()[repo]['branch']}"
    if not 1 <= line <= len(body):
        return False, f"{path}:{line} is past the end of the file ({len(body)} lines)"
    win = "\n".join(body[max(0, line - 1 - WINDOW): line + WINDOW])
    names = {token} | set(aliases(repo, token))
    if any(re.search(rf'\b{re.escape(n)}\b', win) for n in names):
        return True, "ok"
    found = [i + 1 for i, l in enumerate(body) if any(n in l for n in names)][:4]
    return False, (f"{path}:{line} does not show {token!r}"
                   + (f" (it appears at line(s) {found})" if found else ""))


_AT = re.compile(r'^(\S+?):(\d+)')


def verify_task_names() -> list[str]:
    rows = minyaml.load((KB / "knowledge" / "flows" / "task-names.yaml").read_text())["rows"]
    errs = []
    for r in rows:
        tok = r.get("fb_name")
        h = repo_of(r.get("handled_by") or "")
        if h and r.get("site"):
            ok, why = shows(h, r["site"], int(r.get("line") or 0), tok)
            if not ok and r.get("task_type"):
                # a leaf handler (fb-worker) dispatches on the task type, not the fbName
                ok2, _ = shows(h, r["site"], int(r.get("line") or 0), r["task_type"])
                ok = ok or ok2
            if not ok:
                errs.append(f"{r['task_name']}: handler {r['handled_by']} {why}")
        p = repo_of(r.get("produced_by") or "") if r.get("produced_by") not in (None, "null") else None
        at = _AT.match(str(r.get("produced_at") or ""))
        if p and at:
            ok, why = shows(p, at.group(1), int(at.group(2)), tok)
            if not ok:
                errs.append(f"{r['task_name']}: producer {r['produced_by']} {why}")
    return errs


_EVID = re.compile(r'^(\S+)\s+(\S+):(\d+)$')


def verify_flows() -> list[str]:
    flows = minyaml.load((KB / "knowledge" / "flows" / "task-flows.yaml").read_text()).get("flows") or []
    errs = []
    for f in flows:
        for i, s in enumerate(f.get("steps") or [], 1):
            m = _EVID.match(s.get("evidence") or "")
            tok = s.get("shows")
            if not m:
                continue
            if not tok:
                errs.append(f"flow {f['name']} step {i}: no `shows:` token, so its evidence "
                            f"cannot be checked")
                continue
            ok, why = shows(m.group(1), m.group(2), int(m.group(3)), str(tok))
            if not ok:
                errs.append(f"flow {f['name']} step {i} ({s.get('from')} -> {s.get('to')}, "
                            f"{tok}): {why}")
    return errs


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else "task-names"
    errs = verify_task_names() if what == "task-names" else verify_flows()
    for e in errs:
        print("ERROR ", e)
    print(f"{what}: {len(errs)} evidence line(s) do not show what they are cited for")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())

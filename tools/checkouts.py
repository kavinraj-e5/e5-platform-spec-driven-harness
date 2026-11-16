#!/usr/bin/env python3
"""Lay the platform repos out inside the harness, by system and area.

    python3 tools/checkouts.py sync      create or move every checkout to the indexed release
    python3 tools/checkouts.py status    where each repo is, and whether it is clean
    python3 tools/checkouts.py path <repo>

Layout (git-ignored; nothing under platform/ is ever committed to the harness):

    platform/
      workflow-development/workflow-library/platform-core
      app-development/app-library/rf-fb-worker-core
      subsystems/workflow-ops
      communication/akst-service
      reporting/event-store
      platform-delivery/devops-pipeline

The folder names are the same as the ones in knowledge/, so the document for a component and its
code sit at the same relative place.

Each checkout is a git WORKTREE of the bare mirror in ~/.e5/cache, detached at the exact
release branch registry.yaml indexes. That has three consequences:
  * no second download: the objects are the mirror's
  * no credential on disk: a worktree shares its mirror's git config, and a mirror's origin
    is the plain URL (authentication is attached per fetch; `e5 init` audits every origin)
  * what you browse, what pyright and gradle read, and what the knowledge base was
    extracted from are the same commit

A checkout with local edits is never moved or overwritten. It is reported as DIRTY, and the
build and LSP steps skip it, because facts taken from edited code would not describe the
release. Change sets get their own worktrees (`e5 workspace`), not these.
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mirror as M

KB = HERE.parent
ROOT = KB / "platform"


def _folders() -> dict:
    """repo id -> folder under platform/, from the catalog (system) and the area lists."""
    import catalog as C
    import gen_system_readmes as G
    comps = C.load()["Component"]
    area_of = {m: area for sysn, areas in G.AREAS.items()
               for area, (_t, members, _i) in areas.items() for m in members}
    out = {}
    for repo, names in C.components_by_repo().items():
        if not repo:
            continue
        # One repo can yield several components (platform-core yields every workflow role).
        # The component named after the repo decides; otherwise the first in catalog order.
        name = repo if repo in names else names[0]
        sysn = comps[name].get("system", "")
        top = G.DIRS.get(sysn)
        if not top:
            continue
        area = area_of.get(name)
        # an area holding just this repo under the same name (ui-synthesizer) adds no level
        out[repo] = f"{top}/{area}" if area and sysn in G.AREAS and area != repo else top
    return out


def _git(*a, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


def plan() -> list[dict]:
    folders = _folders()
    _, repos = M.registry()
    rows = []
    for r in repos:
        rid = r["id"]
        rows.append({"id": rid, "branch": r.get("branch", ""),
                     "mirror": M.CACHE / f"{rid}.git",
                     "path": ROOT / folders.get(rid, "other") / rid})
    return rows


def state(row: dict) -> str:
    p, m = row["path"], row["mirror"]
    if not m.is_dir():
        return "no-mirror"
    if not (p / ".git").exists():
        return "missing"
    head = _git("rev-parse", "HEAD", cwd=p).stdout.strip()
    want = _git("-C", str(m), "rev-parse", f"refs/heads/{row['branch']}").stdout.strip()
    # --ignore-cr-at-eol: rf-fb-worker-core commits CRLF files under an `eol=lf` rule, so a
    # fresh checkout already "differs" in line endings only. That is not a local edit.
    if _git("diff", "--ignore-cr-at-eol", "--quiet", "HEAD", cwd=p).returncode:
        return "dirty"
    return "ok" if head and head == want else "behind"


def sync(rows: list[dict]) -> int:
    ROOT.mkdir(exist_ok=True)
    fails = 0
    for row in rows:
        rid, p, m, br = row["id"], row["path"], row["mirror"], row["branch"]
        st = state(row)
        rel = p.relative_to(KB)
        if st == "no-mirror":
            print(f"  {rid:32} SKIP  not mirrored (run the mirror step first)"); fails += 1; continue
        ref = f"refs/heads/{br}"
        if _git("-C", str(m), "rev-parse", "--verify", "--quiet", ref).returncode:
            print(f"  {rid:32} FAIL  {br} is not in the mirror"); fails += 1; continue
        if st == "dirty":
            print(f"  {rid:32} DIRTY {rel} has local edits - left as it is"); continue
        if st == "ok":
            print(f"  {rid:32} ok    {rel} @ {br}"); continue
        if st == "missing":
            p.parent.mkdir(parents=True, exist_ok=True)
            _git("-C", str(m), "worktree", "prune")
            res = _git("-C", str(m), "worktree", "add", "--detach", str(p), ref)
        else:  # behind: the release moved, or the registry was re-pointed
            res = _git("checkout", "--detach", ref, cwd=p)
        if res.returncode:
            print(f"  {rid:32} FAIL  {res.stderr.strip()[:120]}"); fails += 1
        else:
            print(f"  {rid:32} {'new ' if st == 'missing' else 'moved'} {rel} @ {br}")
    print(f"\n{len(rows) - fails}/{len(rows)} repos checked out under {ROOT.relative_to(KB)}/")
    return 1 if fails else 0


def clean_paths() -> dict:
    """repo id -> checkout path, only for checkouts that are clean and at the indexed release.
    The build and LSP steps read only these."""
    return {r["id"]: r["path"] for r in plan() if state(r) == "ok"}


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync"); st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    pp = sub.add_parser("path"); pp.add_argument("repo")
    a = ap.parse_args()
    rows = plan()
    if a.cmd == "sync":
        return sync(rows)
    if a.cmd == "path":
        row = next((r for r in rows if r["id"] == a.repo), None)
        if not row:
            print(f"unknown repo {a.repo}"); return 1
        print(row["path"]); return 0
    out = [{"id": r["id"], "path": str(r["path"].relative_to(KB)), "branch": r["branch"],
            "state": state(r)} for r in rows]
    if a.json:
        print(json.dumps(out, indent=1)); return 0
    for o in out:
        print(f"  {o['id']:32} {o['state']:9} {o['path']} @ {o['branch']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

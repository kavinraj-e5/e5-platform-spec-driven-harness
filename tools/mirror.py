#!/usr/bin/env python3
"""Bare-mirror sync and release-branch resolution.

The knowledge base must never be built from whatever branch a working copy happens
to be sitting on. That is exactly what produced the workflow-manager defect: the
registry was seeded from `main` @ 8b6c121 (Jul 2025) and a card was written asserting
the service had no gRPC server, which stopped being true long before.

So: fetch a bare mirror from the CANONICAL element5inc remote, pick the latest
release/vX.Y.Z by numeric version sort, and index that. Working copies are never read.

  python3 tools/mirror.py status          what each repo would index, and the deployment gap
  python3 tools/mirror.py sync [--repo X] fetch mirrors
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import credentials as C
import kbpaths

KB = Path(__file__).resolve().parent.parent
CACHE = Path(__import__("os").environ.get("E5_CACHE", Path.home() / ".e5" / "cache"))
DEPLOYED = KB / "graph" / "deployed.json"
CANON_ORG = "element5inc"

# release/v1.3.10 must sort ABOVE release/v1.3.6, and this estate has four-part
# versions (v1.1.93.1.30) and patch branches (release/v4.0.7.1). Lexical sort is wrong.
_VER = re.compile(r'^release/v(\d+(?:\.\d+)*)$')

def vkey(branch: str):
    m = _VER.match(branch)
    if not m: return None
    return tuple(int(x) for x in m.group(1).split("."))

def registry():
    txt = (KB / "registry.yaml").read_text()
    root = kbpaths.workspace_root(txt)
    rows, cur, inr = [], None, False
    for line in txt.splitlines():
        if line.startswith("repos:"): inr = True; continue
        if inr and re.match(r'^\w', line): inr = False
        if not inr: continue
        m = re.match(r'\s+-\s+id:\s*(\S+)', line)
        if m: cur = {"id": m.group(1)}; rows.append(cur); continue
        m = re.match(r'\s+(\w+):\s*(.*)$', line)
        if m and cur is not None:
            val = m.group(2)
            # strip a trailing ` # comment` - the regex captures to end of line, so a
            # commented value line would otherwise fold the comment into the value.
            if "#" in val and not val.lstrip().startswith("'") and '"' not in val:
                val = val.split("#", 1)[0]
            cur[m.group(1)] = val.strip()
    return root, rows

def canonical(repo: dict) -> str | None:
    """element5inc/<name>, regardless of what the local checkout points at.
    workflow-manager's only local remote is a personal fork; the KB must not index
    a fork as though it were the platform."""
    rem = repo.get("remote", "")
    m = re.search(r'[:/]([^/]+)/([^/]+?)(?:\.git)?$', rem)
    if not m: return None
    return f"{CANON_ORG}/{m.group(2)}"

def ls_release_branches(repo_full: str) -> list[str]:
    url = C.authed_url(repo_full)
    r = subprocess.run(["git", "ls-remote", "--heads", url, "refs/heads/release/*"],
                       capture_output=True, text=True, timeout=90)
    if r.returncode:
        raise RuntimeError(C.mask(r.stderr.strip()[:200]))
    return [l.split("refs/heads/")[-1] for l in r.stdout.splitlines() if "refs/heads/" in l]

def scrub_origin(mirror_dir, repo_full: str) -> None:
    """Force a mirror's `origin` to the plain, unauthenticated URL.

    Credentials are attached at fetch time by passing the URL as an argument, never by
    storing it. Anything that stores one - `git clone <authed>`, a hand-edited config -
    leaves a token sitting on disk, and a mirror cache is not a place anyone thinks to
    look for secrets.
    """
    subprocess.run(["git", "-C", str(mirror_dir), "remote", "set-url", "origin",
                    f"https://github.com/{repo_full}.git"], capture_output=True)


def audit_origins() -> list:
    """Return mirrors whose origin still embeds credentials. Safe to print: it returns
    repo names, never the URL."""
    bad = []
    for d in sorted(CACHE.glob("*.git")):
        r = subprocess.run(["git", "-C", str(d), "remote", "get-url", "origin"],
                           capture_output=True, text=True)
        if re.search(r'://[^@/\s]+@', r.stdout):
            bad.append(d.stem)
    return bad


def deployed_map() -> dict:
    """service -> deployed version, parsed from the cluster image tags."""
    try: dep = json.loads(DEPLOYED.read_text())
    except Exception: return {}
    out = {}
    for svc, img in dep.items():
        m = re.search(r'v(\d+(?:\.\d+)*)', img.split(":")[-1])
        if m: out[svc] = "v" + m.group(1)
    return out

# A deployable repo whose deployment is absent from the qa cluster snapshot. Recorded
# rather than omitted, so the registry says "looked, not there" instead of nothing - CDS
# was onboarded after the 2026-10-02 snapshot and simply had no `deployed_in_qa`, which a
# card could not inherit. It is a SENTINEL, not a version: anything that treats a truthy
# `deployed_in_qa` as evidence of running must exclude it (lifecycle.propose did not, and
# would have proposed CDS as `production` on the strength of the word "not-observed").
NOT_OBSERVED = "not-observed"


def resolve(repo: dict, deployed: dict) -> dict:
    rid = repo["id"]
    full = canonical(repo)
    row = {"id": rid, "canonical": full, "registry_branch": repo.get("branch", "")}
    if not full:
        row["status"] = "no-remote"; return row
    try:
        branches = ls_release_branches(full)
    except Exception as e:
        row["status"] = "unresolvable"; row["error"] = str(e); return row
    rels = sorted([b for b in branches if vkey(b)], key=vkey)
    if not rels:
        row["status"] = "no-release-branch"; row["release_count"] = 0; return row
    latest = rels[-1]
    row.update(status="ok", latest_release=latest, release_count=len(rels))
    depname = repo.get("deployment") or ""
    dv = next((v for k, v in deployed.items() if k and (k in depname or depname.startswith(k))), None)
    if dv:
        row["deployed_in_qa"] = dv
        want = f"release/{dv}"
        row["deployed_branch_exists"] = want in branches
        lk, dk = vkey(latest), vkey(want)
        if dk and lk and lk > dk:
            row["deployment_lag"] = f"{latest} available, {dv} running"
    elif depname:
        row["deployed_in_qa"] = NOT_OBSERVED
    return row

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status"); p.add_argument("--repo", action="append")
    p = sub.add_parser("sync");   p.add_argument("--repo", action="append")
    a = ap.parse_args()

    _, repos = registry()
    if a.repo: repos = [r for r in repos if r["id"] in a.repo]
    dep = deployed_map()

    if a.cmd == "status":
        print(f"{'repo':22} {'registry branch':28} {'would index':20} {'deployed':10} status")
        print("-" * 104)
        bad = 0
        for r in repos:
            row = resolve(r, dep)
            st = row["status"]
            if st != "ok": bad += 1
            mark = "" if st == "ok" else f"  <-- {st}"
            print(f"{row['id']:22} {row['registry_branch'][:28]:28} "
                  f"{row.get('latest_release','-')[:20]:20} "
                  f"{row.get('deployed_in_qa','-'):10} {st}{mark}")
            if row.get("deployment_lag"):
                print(f"{'':22} gap: {row['deployment_lag']}")
        print(f"\n{len(repos)-bad}/{len(repos)} resolve to a release branch")
        return 0

    CACHE.mkdir(parents=True, exist_ok=True)
    for r in repos:
        full = canonical(r)
        if not full: print(f"  {r['id']:22} SKIP (no remote)"); continue
        m = CACHE / f"{r['id']}.git"
        url = C.authed_url(full)
        try:
            if m.exists():
                res = subprocess.run(["git", "-C", str(m), "fetch", "--prune", url,
                                      "+refs/heads/*:refs/heads/*"],
                                     capture_output=True, text=True, timeout=300)
            else:
                res = subprocess.run(["git", "clone", "--mirror", url, str(m)],
                                     capture_output=True, text=True, timeout=600)
                # `clone` PERSISTS the URL it was given into .git/config. Given an
                # authenticated URL that writes the PAT to disk in 22 places, which
                # breaks the rule that credentials are never written anywhere. Reset
                # origin to the plain URL immediately; auth is re-attached per fetch.
                if not res.returncode:
                    scrub_origin(m, full)
            if res.returncode:
                print(f"  {r['id']:22} FAIL {C.mask(res.stderr.strip()[:120])}")
            else:
                print(f"  {r['id']:22} ok   {full}")
        except subprocess.TimeoutExpired:
            print(f"  {r['id']:22} TIMEOUT")
    return 0

if __name__ == "__main__":
    sys.exit(main())

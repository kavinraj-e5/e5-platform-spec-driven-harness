#!/usr/bin/env python3
"""Repos OUTSIDE the platform that consume its libraries: customer workflows, customer apps.

    python3 tools/consumers.py extract    -> graph/consumer-build-facts.jsonl
    python3 tools/consumers.py report     who consumes which platform library, at which pin

A platform-library change is finished only when its consumers have bumped their pin, and
most consumers are not platform repos: every customer workflow depends on platform-core and
AppSDK, and every customer app is built FROM the fb-worker base image and depends on
rf-fb-worker-core.

HOW. Only build files are read, through the GitHub API, read-only, with the credentials
`e5 init` already uses (the md file). Nothing is cloned and no code is indexed.
  * branch: the latest `release/vX.Y.Z` by numeric sort; a repo with none is read at its
    default branch and every fact says `branch_kind: default` (not current behaviour)
  * files: build.gradle, pom.xml, pyproject.toml and Dockerfile at the root (and one level
    down), parsed by the same functions as tools/build_graph.py
  * kept only if it depends on an internal E5 artifact or builds FROM an E5 image
"""
from __future__ import annotations
import argparse, base64, json, re, sys, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_graph as B
import credentials as CRED
import kb_core as K

KB = HERE.parent
OUT = KB / "graph" / "consumer-build-facts.jsonl"
ORG = "element5inc"
API = "https://api.github.com"
_REL = re.compile(r"^release/v(\d+(?:\.\d+)*)$")
FILES = ("build.gradle", "build.gradle.kts", "pom.xml", "pyproject.toml")


def _get(url: str):
    c = CRED._creds()
    if not c:
        raise RuntimeError("no GitHub credential in the credentials file")
    req = urllib.request.Request(url, headers={"Authorization": f"token {c[1]}",
                                               "Accept": "application/vnd.github+json",
                                               "User-Agent": "e5-harness"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _pages(url: str) -> list:
    out, page = [], 1
    while page < 50:
        d = _get(f"{url}{'&' if '?' in url else '?'}per_page=100&page={page}")
        if not d:
            break
        out += d
        page += 1
    return out


def _platform_remotes() -> set:
    names = set()
    for r in K._registry_repos():
        rem = (r.get("remote") or "").rstrip("/").removesuffix(".git")
        names.add(rem.split("/")[-1])
    return names


def _pick_branch(repo: dict) -> tuple[str, str]:
    br = [b["name"] for b in _pages(f"{API}/repos/{ORG}/{repo['name']}/branches")]
    rel = sorted((b for b in br if _REL.match(b)),
                 key=lambda b: tuple(int(x) for x in _REL.match(b).group(1).split(".")))
    return (rel[-1], "release") if rel else (repo.get("default_branch") or "main", "default")


def _file(repo: str, path: str, ref: str) -> str | None:
    try:
        d = _get(f"{API}/repos/{ORG}/{repo}/contents/{path}?ref={ref}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    if isinstance(d, dict) and d.get("encoding") == "base64":
        return base64.b64decode(d["content"]).decode(errors="replace")
    return None


def scan_repo(repo: dict) -> list[dict]:
    name = repo["name"]
    branch, kind = _pick_branch(repo)
    try:
        tree = _get(f"{API}/repos/{ORG}/{name}/git/trees/{branch}?recursive=1").get("tree", [])
    except urllib.error.HTTPError:
        return []
    paths = [t["path"] for t in tree if t.get("type") == "blob" and t["path"].count("/") <= 1]
    want = [p for p in paths if p.split("/")[-1] in FILES or p.split("/")[-1].startswith("Dockerfile")]
    facts = []
    for p in want:
        body = _file(name, p, branch)
        if not body:
            continue
        base = {"repo": name, "branch": branch, "branch_kind": kind, "path": p,
                "provenance": "build-file", "confidence": "EXTRACTED", "outside_platform": True}
        if p.split("/")[-1].startswith("Dockerfile"):
            for f in B.image_bases_from_text(name, p, body, branch, None):
                if f["image"].startswith("e5-") or "$" in f["image"]:
                    facts.append({**f, **base, "provenance": "dockerfile"})
            continue
        for grp, art, ver, cfg in B._deps(p, body):
            facts.append({**base, "kind": "DependsOnArtifact", "group": grp, "artifact": art,
                          "version": ver, "configuration": cfg, "transitive": cfg == "api"})
    return facts


def extract() -> list[dict]:
    skip = _platform_remotes()
    repos = [r for r in _pages(f"{API}/orgs/{ORG}/repos?type=all")
             if not r.get("archived") and r["name"] not in skip]
    out = []
    for r in sorted(repos, key=lambda r: r["name"]):
        try:
            facts = scan_repo(r)
        except Exception as e:
            print(f"  {r['name']}: {type(e).__name__}", file=sys.stderr)
            continue
        if facts:
            out += facts
            print(f"  {r['name']:42} {facts[0]['branch']:28} {len(facts)} fact(s)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["extract", "report"])
    a = ap.parse_args()
    if a.cmd == "extract":
        facts = extract()
        OUT.write_text("".join(json.dumps(f, sort_keys=True) + "\n" for f in facts))
        print(f"{len(facts)} facts from {len({f['repo'] for f in facts})} consumer repos -> "
              f"{OUT.relative_to(KB)}")
        return 0
    facts = [json.loads(l) for l in OUT.read_text().splitlines()] if OUT.is_file() else []
    for f in sorted(facts, key=lambda f: (f.get("artifact") or f.get("image") or "", f["repo"])):
        what = f.get("artifact") or f"image {f.get('image')}"
        pin = f.get("version") or f.get("tag")
        print(f"  {what:32} {f['repo']:40} {pin or '-':12} ({f['branch_kind']} {f['branch']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

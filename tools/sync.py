#!/usr/bin/env python3
"""The update loop. Per-Component, profile-scoped, incremental, self-verifying.

  python3 tools/sync.py status                 what would change, and why
  python3 tools/sync.py run                    extract everything that moved
  python3 tools/sync.py run --component akst   one Component
  python3 tools/sync.py run --force            ignore the commit cache

WHY THIS IS THE PRODUCT
-----------------------
The fact count is a fixture. What matters is that adding or changing a service flows through
without anyone editing an extractor. So this loop is built around three properties that the
previous all-repos-all-extractors indexer could not offer:

1. SCOPED.  A Component declares its profile; only that profile's extractors run, and only
   over that Component's path globs. `platform-core` yields six Components with six different
   path sets from one tree - impossible when the unit of work was a repository.

2. ASSERTIVE.  The profile also declares which extractors must find NOTHING. Those run too,
   and a hit is an error. This is the fix for the defect that motivated the rewrite: four
   workflow-plane repos produced 12 facts out of 3,852 because extractors ran, found nothing,
   and said nothing. Silence and absence are now distinguishable.

3. IDEMPOTENT.  Work is keyed on (component, commit). An unchanged commit does no work and
   produces no diff, which is what makes it safe to run on every push.
"""
from __future__ import annotations
import argparse, fnmatch, json, shutil, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mirror as M
import catalog as C
import profiles as P
import extractors as X

KB = HERE.parent
GRAPH = KB / "graph"
FACTS = GRAPH / "facts.jsonl"
STATE = GRAPH / "sync-state.json"


# --------------------------------------------------------------------------- state
def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    GRAPH.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- git
def _branch_for(repo: str) -> str | None:
    _root, repos = M.registry()
    for r in repos:
        if r["id"] == repo:
            return r["branch"]
    bare = M.CACHE / f"{repo}.git"
    if not bare.is_dir():
        return None
    r = subprocess.run(["git", "-C", str(bare), "for-each-ref",
                        "--format=%(refname:short)", "refs/heads/release/*"],
                       capture_output=True, text=True)
    rels = sorted([b for b in r.stdout.splitlines() if M.vkey(b)], key=M.vkey)
    return rels[-1] if rels else None


def _commit(repo: str, ref: str) -> str | None:
    bare = M.CACHE / f"{repo}.git"
    r = subprocess.run(["git", "-C", str(bare), "rev-parse", "--short", ref],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


class _tree:
    """Materialise a repo at a commit. `git archive`, never a worktree: a worktree writes
    metadata back into the mirror, and the point of a mirror is that nothing has a checkout
    anyone can leave in the wrong state."""

    def __init__(self, repo: str, ref: str):
        self.repo, self.ref, self.dir = repo, ref, None

    def __enter__(self):
        bare = M.CACHE / f"{self.repo}.git"
        self.dir = Path(tempfile.mkdtemp(prefix=f"e5sync-{self.repo}-"))
        pr = subprocess.run(["git", "-C", str(bare), "archive", "--format=tar", self.ref],
                            capture_output=True)
        if pr.returncode:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None
            return None
        tx = subprocess.run(["tar", "-x", "-C", str(self.dir)], input=pr.stdout,
                            capture_output=True)
        if tx.returncode:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None
        return self.dir

    def __exit__(self, *a):
        if self.dir:
            shutil.rmtree(self.dir, ignore_errors=True)


# --------------------------------------------------------------------------- scoping
def scope_globs(cname: str, cdef: dict, cat: dict) -> list[str]:
    """What slice of the repo this Component owns, for FACT scoping.

    When a repo holds exactly one indexed Component, that Component owns the whole tree -
    declaring globs there would only create a way to be wrong. `platform-core` is the sole
    multi-Component repo, and it is the only place a glob list is load-bearing.

    This distinction matters because a Component's facts are not confined to its own source
    packages: protos live in `src/main/proto/`, config keys in `deployment-configs/`. Scoping
    naively to the owned packages dropped 3,260 of 3,852 facts on the first run - including
    448 from the schema owner - which looked like a clean result and was a bug.

    `source.paths` is still declared for every Component, because it is what
    `catalog verify` uses to detect ROLE DRIFT. The two jobs are different.
    """
    repo = (cdef.get("source") or {}).get("repo")
    siblings = [n for n, c in cat["Component"].items()
                if not c.get("unindexed")
                and (c.get("source") or {}).get("repo") == repo]
    if len(siblings) <= 1:
        return ["**"]
    src = cdef.get("source") or {}
    return list(src.get("paths") or []) + list(src.get("contract_paths") or [])


def _glob_depth(g: str) -> int:
    """Specificity of a glob, as path segments before any wildcard."""
    return len([seg for seg in g.replace("/**", "").split("/") if seg and "*" not in seg])


def owns(path: str, cname: str, cat: dict, repo: str) -> bool:
    """Does THIS Component own this file, given its siblings in the same repo?

    Most-specific glob wins. Component globs nest in this estate - wf-worker lives at
    `wre/engine/worker/**`, inside wf-engine's `wre/engine/**` - so a plain "matches my
    glob" test attributed every shared file to BOTH roles and duplicated 12 facts. Rather
    than forcing awkward exclusion syntax into the catalog, the deepest matching glob
    takes the file, which is what a reader would assume anyway.
    """
    best, owner = -1, None
    for n, c in cat["Component"].items():
        if c.get("unindexed") or (c.get("source") or {}).get("repo") != repo:
            continue
        for g in scope_globs(n, c, cat):
            if _in_scope(path, [g]):
                d = 999 if g == "**" else _glob_depth(g)
                # "**" is a whole-repo fallback; any explicit glob beats it
                d = -1 if g == "**" else d
                if d > best:
                    best, owner = d, n
    return owner == cname or owner is None


def _in_scope(path: str, globs: list[str]) -> bool:
    """A fact belongs to a Component if its path falls under one of the declared globs.

    Scoping by path rather than by passing a filter into each extractor keeps all eight
    extractors untouched - they stay simple tree walkers and the Component model stays
    entirely in this file.
    """
    for g in globs:
        if g == "**":
            return True
        if g.endswith("/**"):
            if path.startswith(g[:-3].rstrip("/") + "/"):
                return True
        if fnmatch.fnmatch(path, g):
            return True
        if fnmatch.fnmatch(path, g.rstrip("/") + "/*"):
            return True
    return False


# --------------------------------------------------------------------------- one Component
def extract_component(cname: str, cdef: dict, tree: Path, commit: str,
                      ref: str, cat: dict | None = None) -> tuple[list[dict], list[str], dict]:
    """Returns (facts, errors, per-extractor counts)."""
    prof = C.resolve_profile(cname)
    cat = cat or C.load()
    globs = scope_globs(cname, cdef, cat)
    facts, errs, counts = [], [], {}

    # the extractors this profile claims
    for name in sorted(prof.runs):
        fn = X.EXTRACTORS.get(name)
        if not fn:
            errs.append(f"{cname}: profile names unknown extractor {name!r}")
            continue
        try:
            repo = cdef["source"]["repo"]
            got = [f for f in fn(repo, tree)
                   if _in_scope(f["path"], globs)
                   and owns(f["path"], cname, cat or C.load(), repo)]
        except Exception as e:
            errs.append(f"{cname}/{name}: {type(e).__name__}: {e}")
            continue
        for f in got:
            f["component"] = cname
            f["system"] = cdef["system"]
            f["commit"] = commit
            f["branch"] = ref
        facts.extend(got)
        counts[name] = len(got)

    # the assertions. These are the half that makes a shape change visible.
    for name in sorted(prof.na):
        fn = X.EXTRACTORS.get(name)
        if not fn:
            continue
        try:
            hits = [f for f in fn(cdef["source"]["repo"], tree)
                    if _in_scope(f["path"], globs)
                    and owns(f["path"], cname, cat or C.load(), cdef["source"]["repo"])]
        except Exception:
            continue                      # an N/A extractor crashing proves nothing
        if hits:
            where = ", ".join(sorted({h["path"] for h in hits})[:3])
            errs.append(
                f"{cname}: profile {prof.name!r} asserts NO {name} surface, but found "
                f"{len(hits)} ({where}). Either this Component changed integration style - "
                f"in which case move it to a different system - or the profile is wrong. "
                f"Do NOT widen the profile to make this pass.")
    return facts, errs, counts


# --------------------------------------------------------------------------- the loop
def run(only: str | None = None, force: bool = False, dry: bool = False) -> dict:
    cat = C.load()
    st = _state()
    comps = cat["Component"]
    t0 = time.time()

    planned, skipped = [], []
    for cname, cdef in sorted(comps.items()):
        if only and cname != only:
            continue
        if cdef.get("unindexed"):
            skipped.append((cname, "unindexed by declaration"))
            continue
        prof = C.resolve_profile(cname)
        if not prof.runs:
            skipped.append((cname, f"profile {prof.name} runs no extractors"))
            continue
        repo = (cdef.get("source") or {}).get("repo")
        ref = _branch_for(repo)
        if not ref:
            skipped.append((cname, f"no release branch for {repo}"))
            continue
        commit = _commit(repo, ref)
        if not commit:
            skipped.append((cname, f"cannot resolve {ref} in mirror"))
            continue
        prev = (st.get(cname) or {}).get("commit")
        if prev == commit and not force:
            skipped.append((cname, f"unchanged at {commit}"))
            continue
        planned.append((cname, cdef, repo, ref, commit, prev))

    if dry:
        return {"planned": planned, "skipped": skipped}

    # group by (repo, ref) so one tree is materialised once for all its Components
    bytree: dict = {}
    for item in planned:
        bytree.setdefault((item[2], item[3]), []).append(item)

    # Carry forward facts for Components we are not re-extracting, so an incremental run
    # produces a complete graph rather than only the delta.
    kept = []
    if FACTS.exists():
        touched = {p[0] for p in planned}
        for line in FACTS.open():
            f = json.loads(line)
            if f.get("component") and f["component"] not in touched:
                kept.append(f)

    newfacts, errors, report = [], [], {}
    for (repo, ref), items in sorted(bytree.items()):
        with _tree(repo, ref) as tdir:
            if tdir is None:
                errors.append(f"{repo}@{ref}: could not materialise tree")
                continue
            for cname, cdef, _r, _ref, commit, prev in items:
                fs, errs, counts = extract_component(cname, cdef, tdir, commit, ref, cat)
                newfacts.extend(fs)
                errors.extend(errs)
                report[cname] = {"commit": commit, "was": prev, "facts": len(fs),
                                 "by_extractor": counts,
                                 "profile": C.resolve_profile(cname).name}
                st[cname] = {"commit": commit, "ref": ref,
                             "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "facts": len(fs)}

    # ---- re-attribute WorkerRole facts to the Component that IS that role.
    # The WORKER_MODE dispatch is a single repo-level switch, so whichever Component's
    # glob happens to contain E5WorkflowEngine.java extracts all six modes. Mechanically
    # correct, semantically wrong: the WF_WORKER fact belongs to wf-worker.
    by_mode = {c.get("worker_mode"): n for n, c in cat["Component"].items()
               if c.get("worker_mode")}
    unclaimed = set()
    for f in newfacts:
        if f["kind"] != "WorkerRole":
            continue
        owner = by_mode.get(f["name"])
        if owner:
            f["component"] = owner
            f["system"] = cat["Component"][owner]["system"]
        else:
            unclaimed.add(f["name"])
    if unclaimed:
        # A mode the image accepts that no Component claims is ROLE DRIFT - a new role
        # shipped and the catalog has not caught up. It changes nothing about any contract,
        # so no contract diff would ever surface it. This is the only check that does.
        errors.append(
            f"WORKER_MODE value(s) {sorted(unclaimed)} are dispatched by the image but no "
            f"catalog Component declares them. Add a Component with `worker_mode:` set, or "
            f"the role is invisible to every query.")

    allfacts = kept + newfacts
    # Stable order so that an unchanged estate yields a byte-identical file. Idempotence is
    # not cosmetic here: it is what lets this run on every push without generating noise.
    allfacts.sort(key=lambda f: (f.get("component") or "", f["kind"], f["id"]))
    if not errors:
        GRAPH.mkdir(parents=True, exist_ok=True)
        with FACTS.open("w") as fh:
            for f in allfacts:
                fh.write(json.dumps(f, sort_keys=True) + "\n")
        _save_state(st)
    return {"planned": planned, "skipped": skipped, "errors": errors,
            "report": report, "facts": len(allfacts), "new": len(newfacts),
            "kept": len(kept), "secs": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    r = sub.add_parser("run")
    for p in (s, r):
        p.add_argument("--component")
        p.add_argument("--force", action="store_true")
    a = ap.parse_args()

    errs = C.check()
    if errs:
        print("catalog is invalid; refusing to sync:")
        for e in errs:
            print(f"  {e}")
        return 1

    if a.cmd == "status":
        out = run(a.component, a.force, dry=True)
        print(f"{len(out['planned'])} Components would be extracted:")
        for cname, _c, repo, ref, commit, prev in out["planned"]:
            why = "new" if not prev else f"{prev} -> {commit}"
            print(f"  {cname:<24} {repo:<20} {ref:<18} {why}")
        print(f"\n{len(out['skipped'])} skipped:")
        for cname, why in out["skipped"]:
            print(f"  {cname:<24} {why}")
        return 0

    out = run(a.component, a.force)
    print(f"\n{'component':<24} {'profile':<24} {'commit':<10} facts")
    print("-" * 74)
    for cname, info in sorted(out["report"].items()):
        print(f"{cname:<24} {info['profile']:<24} {info['commit']:<10} {info['facts']}")
    print(f"\n{out['new']} new facts + {out['kept']} carried = {out['facts']} total "
          f"in {out['secs']}s")
    if out["skipped"]:
        print(f"{len(out['skipped'])} skipped (status for detail)")
    if out["errors"]:
        print(f"\n{len(out['errors'])} ERRORS - nothing was written:")
        for e in out["errors"]:
            print(f"  {e}")
        return 1
    print(f"-> {FACTS.relative_to(KB)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

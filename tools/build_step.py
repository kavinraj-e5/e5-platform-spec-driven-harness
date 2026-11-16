#!/usr/bin/env python3
"""Compile every JVM repo at the indexed release, and read its bytecode. One `e5 sync` step.

    python3 tools/build_step.py            build only the repos whose release commit moved
    python3 tools/build_step.py --force    build every JVM repo again (about 30 minutes)
    python3 tools/build_step.py status     what was built, at which commit, and how it went

The build and the bytecode reading are tools/scip_pilot.py's (gradle or maven, JFrog
credentials from the credentials file for private dependencies). This step adds the two things
a teammate's sync needs:

  * **No rebuild when nothing moved.** A full pass takes about half an hour, so each repo's
    result is recorded against the COMMIT it was built from (graph/build-state.json), and
    only repos whose release branch now points somewhere else are built again.
  * **A merge, not a rewrite.** graph/layer-facts.jsonl keeps the facts of every repo that
    was not rebuilt, so building one repo does not erase the other twenty-one.

ADVISORY. A repo that does not compile is reported and the sync carries on: the
source-level extractors do not need a build, and a release that fails to compile is itself a
finding, not a reason to stop. The exit code is 0 unless the step itself broke.
"""
from __future__ import annotations
import argparse, json, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scip_pilot as S
import mirror as M

KB = HERE.parent
STATE = KB / "graph" / "build-state.json"


def _commit(repo: str, branch: str) -> str:
    r = subprocess.run(["git", "-C", str(M.CACHE / f"{repo}.git"), "rev-parse",
                        f"refs/heads/{branch}"], capture_output=True, text=True)
    return r.stdout.strip()


def _load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.is_file() else {}


def _existing_facts() -> list[dict]:
    if not S.LAYER_FACTS.is_file():
        return []
    return [json.loads(l) for l in S.LAYER_FACTS.open() if l.strip()]


def run(force: bool) -> int:
    state = _load_state()
    repos = S.jvm_repos()
    todo = {}
    for repo, meta in repos.items():
        c = _commit(repo, meta.get("branch", ""))
        if not c:
            print(f"  {repo:32} SKIP  not mirrored at {meta.get('branch')}")
            continue
        prev = state.get(repo, {})
        if not force and prev.get("commit") == c:
            print(f"  {repo:32} same  {prev.get('status')} @ {c[:8]} (built {prev.get('at', '?')})")
            continue
        todo[repo] = (meta, c)
    if not todo:
        print("\nnothing moved - no build needed")
        return 0
    print(f"\nbuilding {len(todo)} repo(s), one at a time\n")
    facts = [f for f in _existing_facts() if f.get("repo") not in todo]
    for repo, (meta, commit) in todo.items():
        # scip_pilot caches a build tree by BRANCH NAME. A branch that moved must not reuse
        # the tree of its previous commit.
        shutil.rmtree(S.WORK / f"{repo}-{meta.get('branch', '').replace('/', '_')}",
                      ignore_errors=True)
        t0 = time.time()
        b = S.build_repo(repo, meta, meta.get("branch"), 1)
        rec = {"commit": commit, "branch": meta.get("branch"),
               "at": time.strftime("%Y-%m-%d %H:%M")}
        if not b["ok"]:
            bad = [a for a in b.get("attempts", []) if not a["ok"]]
            err = (bad[0]["error"] if bad else b.get("error", ""))[:150]
            rec.update(status="no-build", detail=err,
                       compile_error=any(a.get("compile_error") for a in bad))
            print(f"  {repo:32} NO-BUILD {err}")
        else:
            idx = S.index_repo(repo, b["tree"])
            if not idx["ok"]:
                rec.update(status="no-classes", detail=idx["error"][:150])
                print(f"  {repo:32} NO-CLASS {idx['error'][:100]}")
            else:
                facts += S.facts_for(repo, b["ref"], idx)
                rec.update(status="ok", detail=f"{idx['classes']} classes, "
                                               f"{len(idx['edges'])} edges ({b['how']})")
                print(f"  {repo:32} ok    {rec['detail']}  [{time.time() - t0:.0f}s]")
            shutil.rmtree(b["tree"], ignore_errors=True)
        state[repo] = rec
        STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")  # per repo: a
        # killed run keeps what it finished
    n = S.write_facts(facts)
    ok = sum(1 for r in state.values() if r.get("status") == "ok")
    print(f"\n{ok}/{len(repos)} JVM repos compile at the indexed release; "
          f"{n} bytecode facts in {S.LAYER_FACTS.relative_to(KB)}")
    for repo, r in sorted(state.items()):
        if r.get("status") != "ok":
            print(f"  does not build: {repo} @ {r.get('branch')} - {r.get('detail', '')[:110]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "status"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.cmd == "status":
        for repo, r in sorted(_load_state().items()):
            print(f"  {repo:32} {r.get('status', '?'):10} {r.get('branch', '')} "
                  f"@ {r.get('commit', '')[:8]}  {r.get('at', '')}  {r.get('detail', '')[:60]}")
        return 0
    return run(a.force)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""One entry point for structured extraction.

    python3 -m extract.cli diff [name...]     ported extractor vs the regex original
    python3 -m extract.cli run  [name...]     -> stdout, jsonl
    python3 -m extract.cli parse <repo> <path>  what the front end sees

WHY `diff` IS THE POINT
-----------------------
The v3 plan's safety net for factoring out the graph model was "assert the Cypher is
byte-identical". A parser rewrite cannot be byte-identical - that is the whole reason to
do it - so the net here is a DIFFERENCE REPORT that a human signs off per extractor:

    ONLY_OLD   the regex found something the parser does not. Each one is either a
               regex false positive (good - say why) or a parser gap (a bug).
    ONLY_NEW   the parser found something the regex missed. Usually a blind spot being
               closed; still has to be explained, not assumed.
    MOVED      same fact, different line. A line number is evidence a reader checks,
               so a silent shift is not acceptable either.
    PROPS      same fact, same line, different PROPS. `routing` needed this: its facts
               are keyed on `repo:path:line`, so every label, destination and risk flag
               lives in props and a location-only diff reported "identical" while the
               payload changed completely.

Nothing is switched over until its differences are accounted for in
`extract/PORTING.md`.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

KB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KB))
sys.path.insert(0, str(KB / "tools"))

from extract import core                       # noqa: E402
from extract.declared import PORTED            # noqa: E402

import extractors as OLD                       # noqa: E402

# name -> the regex extractor it replaces
OLD_OF = {n: OLD.EXTRACTORS[n] for n in PORTED if n in OLD.EXTRACTORS}


# `repo` is part of the key. Without it, three repositories that each ship a
# `src/main/proto/task.proto` collided in the comparison dict and two of the three were
# silently dropped - which hid that task-reader's `isTaskAvailable` takes a different
# request message from platform-core's.
def _key(f):
    return (f["kind"], f["name"], f["repo"], f["path"], f["line"])


def _loose(f):
    return (f["kind"], f["name"], f["repo"], f["path"])


def diff_one(name: str, repos) -> dict:
    if getattr(OLD_OF.get(name), "ported", False):
        return {"name": name, "new": 0, "old": 0, "only_new": [], "only_old": [],
                "moved": [], "props": []}
    new_all, old_all = [], []
    for r in repos:
        new_all += PORTED[name](r)
        if name in OLD_OF:
            with core.as_dir(r) as root:
                old_all += OLD_OF[name](r.id, root)
    nk = {_key(f): f for f in new_all}
    ok = {_key(f): f for f in old_all}
    only_new = [nk[k] for k in nk.keys() - ok.keys()]
    only_old = [ok[k] for k in ok.keys() - nk.keys()]
    # Separate a genuine disappearance from a line shift.
    nl = collections.defaultdict(list)
    for f in only_new:
        nl[_loose(f)].append(f)
    moved = []
    for f in list(only_old):
        same = nl.get(_loose(f))
        if same:
            moved.append((f, same[0]))
            only_old.remove(f)
            only_new.remove(same[0])
            same.pop(0)
    props = [(ok[k], nk[k]) for k in sorted(nk.keys() & ok.keys())
             if ok[k]["props"] != nk[k]["props"]]
    return {"name": name, "new": len(new_all), "old": len(old_all),
            "only_new": only_new, "only_old": only_old, "moved": moved,
            "props": props}


def _report(d) -> int:
    n, o = d["new"], d["old"]
    print(f"\n=== {d['name']}  new={n}  old={o}")
    if getattr(OLD_OF.get(d["name"]), "ported", False):
        print("    already switched over - tools/extractors.py delegates here, so this "
              "compares\n    the parser with itself. The signed-off differences are in "
              "extract/PORTING.md.")
        return 0
    if not (d["only_new"] or d["only_old"] or d["moved"] or d["props"]):
        print("    identical")
        return 0
    for label, rows in (("ONLY_OLD (regex found, parser did not)", d["only_old"]),
                        ("ONLY_NEW (parser found, regex did not)", d["only_new"])):
        if not rows:
            continue
        print(f"    {label}: {len(rows)}")
        by = collections.Counter(f"{f['repo']}" for f in rows)
        for repo, c in by.most_common(6):
            ex = [f for f in rows if f["repo"] == repo][:3]
            print(f"      {repo} ({c}): " +
                  ", ".join(f"{f['name']}@{f['path'].rsplit('/', 1)[-1]}:{f['line']}"
                            for f in ex))
    if d["moved"]:
        print(f"    MOVED (line changed): {len(d['moved'])}")
        for old, new in d["moved"][:6]:
            print(f"      {old['repo']} {old['name']} "
                  f"{old['path'].rsplit('/', 1)[-1]}: {old['line']} -> {new['line']}")
    if d["props"]:
        print(f"    PROPS (same line, different payload): {len(d['props'])}")
        for o, n in d["props"][:8]:
            keys = sorted(set(o["props"]) | set(n["props"]))
            for k in keys:
                a, b = o["props"].get(k, "-"), n["props"].get(k, "-")
                if a != b:
                    print(f"      {o['path'].rsplit('/', 1)[-1]}:{o['line']} {k}:")
                    print(f"          old {str(a)[:150]}")
                    print(f"          new {str(b)[:150]}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["diff", "run", "parse"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--repo", action="append")
    a = ap.parse_args()

    if a.cmd == "parse":
        repo_id, path = a.rest[0], a.rest[1]
        r = next(iter(core.from_registry([repo_id])))
        src = r.read(path)
        if path.endswith(".py"):
            from extract.parse.pyfile import PyFile
            f = PyFile(src)
            print(f"ok={f.ok}")
            for fn in f.functions():
                print(f"  {'def ' if fn.top_level else '  nested '}{fn.name} @{fn.line}"
                      f"{' owner=' + fn.owner if fn.owner else ''}")
            for b in f.env_bindings():
                print(f"  env {b[0]} = getenv({b[1]!r}) @{b[2]}")
            return 0
        from extract.parse.java import JavaFile
        f = JavaFile(src)
        for t in f.types():
            print(f"  {t.kind} {t.name} @{t.line}"
                  f"{' extends ' + t.extends if t.extends else ''}")
        for sw in f.switches():
            print(f"  switch on {sw.selector!r} @{sw.line} in {sw.owner}()")
            for arm in sw.arms:
                kinds = ",".join("lit" if l else "const" for _, l in arm.labels)
                print(f"      @{arm.line} [{kinds}] {[t for t, _ in arm.labels]}")
        for s in f.strings():
            if s.role != "OTHER":
                print(f"  str @{s.line:<5} {s.role:14} {s.context:22} {s.value[:40]!r}")
        return 0

    names = [n for n in (a.rest or sorted(PORTED)) if n in PORTED]
    repos = core.from_registry(a.repo)

    if a.cmd == "run":
        for n in names:
            for r in repos:
                for f in r.stamp(PORTED[n](r)):
                    print(json.dumps(f, sort_keys=True))
        return 0

    rc = 0
    for n in names:
        rc |= _report(diff_one(n, repos))
    print()
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

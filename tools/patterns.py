#!/usr/bin/env python3
"""Re-check what the disagreement cards assert, with semgrep.

    ./e5 patterns              every rule against every mirror
    ./e5 patterns --rule ID    one of them
    ./e5 patterns list         the rules and the cards they back

WHY THIS EXISTS
---------------
A disagreement card says "the code does X", verified by a human on one date. 69 cards.
Nothing re-checks them. Two things then happen quietly:

  * someone fixes X, and the card becomes a false accusation that a reader will act on;
  * someone copies X into a second repo, and the card becomes incomplete in a way that
    looks complete.

`./e5 cite drift` does not catch either. It asks whether a cited LINE's content changed,
which is a different question: a fix three files away leaves every citation resolving.

A rule in `rules/` encodes one card's claim as a SHAPE. `e5-at` in its metadata says
where the claim is supposed to hold. This compares the two.

WHY SEMGREP RATHER THAN THE extract/ FRONT ENDS
-----------------------------------------------
`extract/` answers "what does this repo declare" and emits facts. These rules answer
"does this shape still occur" and emit a verdict on a card. The practical difference is
who can write one: a rule here is declarative YAML, so it can be reviewed and argued
with by someone who would not open `extract/parse/java.py`.

semgrep is invoked as a BINARY and never imported, so nothing in this repository depends
on its Python environment - which matters, because `pip install --user semgrep` upgrades
starlette past the pinned fastapi and breaks the console. Use pipx. See rules/README.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KB = HERE.parent
RULES = KB / "rules"
sys.path.insert(0, str(KB))
sys.path.insert(0, str(HERE))

# How far a found line may sit from the line the rule pins before it is called drift.
# Not zero: semgrep anchors a multi-line match on its first matching token, and a card
# cites the statement a human would read.
_DRIFT_TOLERANCE = 3

_INSTALL = """semgrep is not installed.

    pipx install semgrep

NOT `pip install --user semgrep`: measured 2026-10-17, it upgrades starlette past what
the pinned fastapi supports and console/test_app.py then fails at collection. semgrep is
a CLI; it has no business sharing a site-packages. See rules/README.md."""


def semgrep_bin() -> str | None:
    for c in (shutil.which("semgrep"),
              str(Path.home() / ".local/bin/semgrep"),
              "/usr/local/bin/semgrep"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


# ------------------------------------------------------------------ the rule contract
def rules() -> list[dict]:
    """Every rule, with its metadata. Read with the minimal YAML reader so this file
    takes no dependency either - the rules are flat enough for it."""
    sys.path.insert(0, str(HERE / "release"))
    import minyaml
    out = []
    for f in sorted(RULES.glob("*.yaml")):
        doc = minyaml.load(f.read_text()) or {}
        for r in doc.get("rules") or []:
            md = r.get("metadata") or {}
            out.append({
                "id": r.get("id"), "file": f.name,
                "card": md.get("e5-card"), "expect": md.get("e5-expect", "present"),
                "at": [_split_at(a) for a in (md.get("e5-at") or [])],
                "message": " ".join(str(r.get("message", "")).split())[:160],
            })
    return out


def card_spans(card: str | None, basename: str) -> list[tuple[int, int]]:
    """Every line SPAN the card cites for this file.

    The rule pins where the shape is today; the card says where a human saw it. Those
    drifting apart is the decay this module exists to catch, and nothing else notices
    it: `./e5 cite check` only asks whether the line EXISTS, and `cite drift` only
    fires when the registry moves past the commit the card declares.

    A range is read as a RANGE. Reading `:43-69` as the two points 43 and 69 called two
    correct cards stale - `PlatformEventStoreImpl.java:47` and
    `TaskServiceGrpcImpl.java:219` are both inside the span their card gives. A gate
    that makes false accusations is a gate someone switches off.
    """
    if not card:
        return []
    f = KB / card
    if not f.is_file():
        return []
    out = set()
    for m in re.finditer(re.escape(basename) + r":(\d+)(?:\s*-\s*(\d+))?",
                         f.read_text()):
        a = int(m.group(1))
        out.add((a, int(m.group(2)) if m.group(2) else a))
    return sorted(out)


def _outside(line: int, spans: list[tuple[int, int]]) -> bool:
    return all(not (a - _DRIFT_TOLERANCE <= line <= b + _DRIFT_TOLERANCE)
               for a, b in spans)


def _split_at(spec: str) -> dict:
    repo, _, rest = str(spec).partition(" ")
    path, _, line = rest.strip().partition(":")
    return {"repo": repo, "path": path, "line": int(line) if line.isdigit() else None}


# ---------------------------------------------------------------------------- running
def scan(only: list[str] | None = None, rule: str | None = None) -> dict:
    """semgrep over every mirror, once.

    Every repo is materialised into ONE temp root and semgrep runs a single time over
    it: 23 separate invocations spent more time starting semgrep than matching. The
    first path segment is the repo id.
    """
    from extract import core
    sg = semgrep_bin()
    if sg is None:
        return {"ok": False, "reason": "semgrep not installed", "hits": []}
    repos = core.from_registry(only)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for r in repos:
            dest = root / r.id
            dest.mkdir(parents=True, exist_ok=True)
            pr = subprocess.run(["git", "--git-dir", r.gitdir, "archive",
                                 "--format=tar", r.ref], capture_output=True)
            subprocess.run(["tar", "-x", "-C", str(dest)], input=pr.stdout,
                           capture_output=True)
        cmd = [sg, "--config", str(RULES), "--json", "--quiet", "--metrics=off",
               "--no-git-ignore", "--timeout", "120", str(root)]
        if rule:
            cmd[2:2] = []           # --config stays; filtering happens below
        p = subprocess.run(cmd, capture_output=True, text=True)
        try:
            doc = json.loads(p.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "reason": f"semgrep failed: {p.stderr[-400:]}",
                    "hits": []}
        hits = []
        for res in doc.get("results", []):
            rel = Path(res["path"]).relative_to(root)
            rid = res["check_id"].split(".")[-1]
            if rule and rid != rule:
                continue
            hits.append({"rule": rid, "repo": rel.parts[0],
                         "path": str(Path(*rel.parts[1:])),
                         "line": res["start"]["line"]})
        errs = [str(e.get("message", ""))[:200] for e in doc.get("errors", [])]
    return {"ok": True, "hits": hits, "errors": errs, "repos": [r.id for r in repos]}


def verdicts(rs: list[dict], hits: list[dict]) -> list[dict]:
    out = []
    for r in rs:
        mine = [h for h in hits if h["rule"] == r["id"]]
        expected = {(a["repo"], a["path"]): a["line"] for a in r["at"]}
        found = {(h["repo"], h["path"]): h["line"] for h in mine}
        rows, claimed = [], set()
        for key, want_line in sorted(expected.items()):
            if key in found:
                got = found[key]
                drift = (want_line is not None
                         and abs(got - want_line) > _DRIFT_TOLERANCE)
                spans = card_spans(r["card"], key[1].rsplit("/", 1)[-1])
                stale = bool(spans) and _outside(got, spans)
                rows.append({"verdict": "CONFIRMED", "repo": key[0], "path": key[1],
                             "line": got, "pinned": want_line, "drift": drift,
                             "card_cites": spans if stale else []})
            else:
                same_repo = [h for h in mine if h["repo"] == key[0]]
                if same_repo:
                    rows.append({"verdict": "MOVED", "repo": key[0], "path": key[1],
                                 "now": [f"{h['path']}:{h['line']}" for h in same_repo]})
                    # MOVED already names where it went. Letting those hits fall
                    # through to SPREAD reports one refactor twice, once as a loss and
                    # once as a new defect.
                    claimed.update((h["repo"], h["path"]) for h in same_repo)
                else:
                    rows.append({"verdict": "FIXED", "repo": key[0], "path": key[1],
                                 "pinned": want_line})
        for key in sorted(found):
            if key not in expected and key not in claimed:
                rows.append({"verdict": "SPREAD", "repo": key[0], "path": key[1],
                             "line": found[key]})
        if not expected and mine:
            rows = [{"verdict": "SPREAD", "repo": h["repo"], "path": h["path"],
                     "line": h["line"]} for h in mine]
        out.append({**r, "rows": rows})
    return out


# ----------------------------------------------------------------------------- output
def _print(v: list[dict], errors: list[str], repos: list[str]) -> int:
    worst = 0
    counts = {"CONFIRMED": 0, "CARD_STALE": 0, "DRIFT": 0, "MOVED": 0, "FIXED": 0,
              "SPREAD": 0}
    for r in v:
        print(f"\n=== {r['id']}   ({r['file']})")
        print(f"    card: {r['card'] or '(standing rule, no card)'}")
        if not r["rows"]:
            print("    no matches, and the rule pins no location - nothing to say")
            continue
        for row in r["rows"]:
            tag = row["verdict"]
            if tag == "CONFIRMED" and row.get("drift"):
                tag, counts["DRIFT"] = "DRIFT", counts["DRIFT"] + 1
            else:
                counts[tag] = counts.get(tag, 0) + 1
            where = f"{row['repo']} {row['path']}"
            if tag == "CONFIRMED":
                print(f"    CONFIRMED  {where}:{row['line']}")
                if row.get("card_cites"):
                    counts["CARD_STALE"] = counts.get("CARD_STALE", 0) + 1
                    print(f"               ...but the card cites "
                          f"{row['path'].rsplit('/', 1)[-1]}:"
                          f"{','.join(str(a) if a == b else f'{a}-{b}' for a, b in row['card_cites'])} - the code "
                          f"moved and the card did not")
                    worst = max(worst, 1)
            elif tag == "DRIFT":
                print(f"    DRIFT      {where}:{row['line']}  "
                      f"(the rule pins :{row['pinned']} - re-anchor the card)")
                worst = max(worst, 1)
            elif tag == "MOVED":
                print(f"    MOVED      {where} no longer matches; same repo now has "
                      f"{', '.join(row['now'])}")
                worst = max(worst, 1)
            elif tag == "FIXED":
                print(f"    FIXED      {where} no longer matches anywhere. "
                      f"RE-READ AND RETIRE THE CARD.")
                worst = max(worst, 1)
            else:
                print(f"    SPREAD     {where}:{row['line']}  "
                      f"not named by the card")
                worst = max(worst, 1)
    print("\n" + "-" * 70)
    print(f"{len(repos)} mirrors scanned.  " +
          "  ".join(f"{k} {n}" for k, n in counts.items() if n))
    if errors:
        print(f"semgrep reported {len(errors)} parse error(s): {errors[0]}")
    print("FIXED is the one that matters: the card is now wrong and a reader will act "
          "on it.\nAdvisory - this reports on the ESTATE, not on the change in front "
          "of you.")
    return worst


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", nargs="?", default="check", choices=["check", "list"])
    ap.add_argument("--rule")
    ap.add_argument("--repo", action="append")
    a = ap.parse_args(argv)

    rs = [r for r in rules() if not a.rule or r["id"] == a.rule]
    if not rs:
        print(f"no rule {a.rule!r} in {RULES}")
        return 2
    if a.cmd == "list":
        for r in rs:
            print(f"{r['id']:42} {r['file']:18} {r['card'] or '-'}")
            print(f"{'':42} {r['message'][:90]}")
        return 0

    if semgrep_bin() is None:
        print(_INSTALL)
        print(f"\n{len(rs)} rule(s) NOT CHECKED. This is a skip, not a pass.")
        return 0
    res = scan(a.repo, a.rule)
    if not res["ok"]:
        print(res["reason"])
        return 2
    return _print(verdicts(rs, res["hits"]), res.get("errors") or [], res["repos"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

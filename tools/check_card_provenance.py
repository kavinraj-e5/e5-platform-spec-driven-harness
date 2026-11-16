#!/usr/bin/env python3
"""Gate: a card may not cite a commit the registry does not index.

This is the check whose absence caused the original defect. `check_staleness.py`
compares graph/versions.json against graph/deployed.json and contains zero
references to cards -- so card `branch:`/`commit:` frontmatter was read by no line
of Python anywhere. Fact-level provenance was enforced; card-level provenance was
decorative. A card went on asserting "this service exposes no gRPC API at all",
pinned to a year-old `main`, long after the registry had been re-pointed.

Rules, in severity order:
  E1  card cites a commit/branch the registry does not index
  E2  card sits on a non-release branch (main, feat/*) without an explicit waiver
  E3  repo is unresolvable but the card asserts behaviour anyway
  E4  card carries branch: but no deployed_in_qa:, so the reader cannot see the gap
  W1  card's verified: date is older than the registry commit it cites

Exit 1 on any E. Wired into `e5 check` as blocking.
"""
import re, sys, subprocess
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mirror as M

KB = Path(__file__).resolve().parent.parent
CARDS = KB / "knowledge"
# a card may sit off a release branch only if it says why, here
WAIVERS = {"eventstore-client-sdk": "no release branch exists on the remote",
           "ui-synthesizer": "branch convention here is `release-X.Y.Z` (hyphen), "
                             "which mirror.vkey() cannot parse",
           "rf-fb-base-image": "branches here are named `fedora-vN`; there is no "
                               "semver release branch on the remote"}





def frontmatter(p: Path) -> dict:
    txt = p.read_text(errors="replace")
    m = re.match(r'^---\n(.*?)\n---', txt, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        k = re.match(r'^([a-z_]+):\s*(.*)$', line)
        if k:
            fm[k.group(1)] = k.group(2).strip()
    fm["_body"] = txt[m.end():]
    return fm


def index_registry():
    """Key the registry by every name a card might use: id, remote repo name, path."""
    _root, repos = M.registry()
    by = {}
    for r in repos:
        by[r["id"]] = r
        rem = r.get("remote", "")
        n = re.search(r'/([^/]+?)(?:\.git)?$', rem)
        if n:
            by[n.group(1)] = r
        if r.get("path"):
            by[r["path"]] = r
    return by, repos


_REL = re.compile(r'^release/v\d')


def check():
    by, repos = index_registry()
    errs, warns, checked, skipped = [], [], 0, 0

    for p in sorted(CARDS.rglob("*.md")):
        if p.name.startswith("_TEMPLATE"):
            skipped += 1
            continue
        fm = frontmatter(p)
        if not fm.get("branch") and not fm.get("commit"):
            skipped += 1
            continue
        rel = p.relative_to(KB)
        key = fm.get("repo") or fm.get("subsystem") or fm.get("repo_path") or ""
        reg = (by.get(key) or by.get(fm.get("subsystem", ""))
               or by.get(key.split("/")[-1]))
        if reg is None:
            errs.append((rel, "E1", f"repo {key!r} is not in the registry"))
            continue
        checked += 1
        rid = reg["id"]

        # E1 - the core check
        if fm.get("branch") != reg["branch"] or fm.get("commit") != reg["commit"]:
            errs.append((rel, "E1",
                f"cites {fm.get('branch')}@{fm.get('commit')}, "
                f"registry indexes {reg['branch']}@{reg['commit']}"))

        # E2 - non-release branch
        if not _REL.match(fm.get("branch", "")) and rid not in WAIVERS:
            errs.append((rel, "E2", f"branch {fm.get('branch')!r} is not a release branch"))

        # E3 - unresolvable repo must not assert behaviour
        if reg.get("status") in ("unresolvable", "no-remote"):
            if fm.get("status") != "unresolvable":
                errs.append((rel, "E3",
                    f"repo is {reg['status']} but the card asserts behaviour without "
                    f"status: unresolvable"))

        # E4 - the gap must be visible to a reader
        if reg.get("deployed_in_qa") and not fm.get("deployed_in_qa"):
            errs.append((rel, "E4",
                f"no deployed_in_qa: - reader cannot tell this describes "
                f"{reg['branch']} while QA runs {reg['deployed_in_qa']}"))

        # W1 - staleness of the human review
        v = fm.get("verified", "")
        if re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            age = (date.today() - date.fromisoformat(v)).days
            if age > 30:
                warns.append((rel, "W1", f"verified {v} ({age}d old)"))

        # W2 - the body was last read against a different tree than the facts cite.
        # sync_card_provenance.py writes this marker when it re-stamps a moved card, and
        # until 2026-11-13 nothing read it - so re-stamping cleared E1 and left no trace
        # that the prose was never re-read. A warning, not an error: re-reading is human
        # work and must not block a sync, but it must not be invisible either.
        prc = fm.get("prose_reviewed_at_commit", "")
        if prc and fm.get("commit") and prc != fm.get("commit"):
            warns.append((rel, "W2",
                f"prose last reviewed at {prc}, facts cite {fm.get('commit')} - "
                f"re-read the body, then set prose_reviewed_at_commit: {fm.get('commit')}"))

    return errs, warns, checked, skipped


def main():
    errs, warns, checked, skipped = check()
    for rel, code, msg in errs:
        print(f"ERROR  {code}  {rel}\n         {msg}")
    for rel, code, msg in warns:
        print(f"warn   {code}  {rel}: {msg}")
    print(f"\n{checked} cards carry provenance and were checked; {skipped} carry none.")
    print(f"{len(errs)} errors, {len(warns)} warnings.")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())

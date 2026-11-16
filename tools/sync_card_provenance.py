#!/usr/bin/env python3
"""Rewrite card frontmatter to match the registry. Metadata only - never prose.

Deliberately does NOT touch the body. A card whose commit moved may also be factually
wrong, and silently bumping its commit would make a wrong card *look* verified - which
is a worse version of the original defect. This tool makes the gate's E1/E4 errors go
away and leaves E-class content errors for a human to write.

So: run this, then re-read every card it touched whose commit moved by more than a
patch, and correct the prose by hand with a dated note.
"""
import re, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import mirror as M
from check_card_provenance import frontmatter, index_registry

KB = Path(__file__).resolve().parent.parent
TODAY = date.today().isoformat()


def set_key(fm_text: str, key: str, val: str) -> str:
    if re.search(rf'^{key}:', fm_text, re.M):
        return re.sub(rf'^{key}:.*$', f'{key}: {val}', fm_text, count=1, flags=re.M)
    return fm_text.rstrip("\n") + f"\n{key}: {val}"


def main():
    apply = "--apply" in sys.argv
    by, _ = index_registry()
    moved = []
    for p in sorted((KB / "knowledge").rglob("*.md")):
        txt = p.read_text()
        m = re.match(r'^---\n(.*?)\n---', txt, re.S)
        if not m:
            continue
        fm = frontmatter(p)
        if not fm.get("branch"):
            continue
        if p.name.startswith("_TEMPLATE"):
            continue
        key = fm.get("repo") or fm.get("subsystem") or fm.get("repo_path") or ""
        reg = (by.get(key) or by.get(fm.get("subsystem", ""))
               or by.get(key.split("/")[-1]))
        if not reg:
            continue
        head = m.group(1)
        old_b, old_c = fm.get("branch"), fm.get("commit")
        head = set_key(head, "branch", reg["branch"])
        head = set_key(head, "commit", reg["commit"])
        if reg.get("deployed_in_qa"):
            head = set_key(head, "deployed_in_qa", reg["deployed_in_qa"])
        if reg.get("deployment_lag"):
            head = set_key(head, "deployment_lag", reg["deployment_lag"])
        if reg.get("status") and reg["status"] != "ok":
            head = set_key(head, "status", reg["status"])
        changed = (old_b, old_c) != (reg["branch"], reg["commit"])
        if changed:
            # `verified` is NOT touched. It is the date of the last HUMAN review, and W1
            # warns on its age. Bumping it here made every re-stamped card look freshly
            # verified while its body was unread - the gate went green on 17 cards at once.
            # Mark the prose as not yet re-read against the new tree; W2 reports it. Keep
            # an existing marker: a second move must not erase the first unread commit.
            if not re.search(r'^prose_reviewed_at_commit:', head, re.M):
                head = set_key(head, "prose_reviewed_at_commit", old_c or "unknown")
            moved.append((p.relative_to(KB), f"{old_b}@{old_c}", f"{reg['branch']}@{reg['commit']}"))
        new = "---\n" + head + "\n---" + txt[m.end():]
        if apply and new != txt:
            p.write_text(new)
    for rel, a, b in moved:
        print(f"  {rel}\n      {a}  ->  {b}")
    print(f"\n{len(moved)} cards moved commit and now carry "
          f"prose_reviewed_at_commit - their BODIES still need a human read.")
    print("wrote" if apply else "dry-run")


if __name__ == "__main__":
    main()

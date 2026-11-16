#!/usr/bin/env python3
"""Relocate card citations whose code MOVED but did not change; list the rest for a human.

    python3 tools/cite_relocate.py            dry run: what would move, what needs reading
    python3 tools/cite_relocate.py --apply    rewrite the moved citations and re-anchor them

A drifted citation is one whose cited line changed since the card was verified
(`citations.py drift`). Only ONE case is safe to fix mechanically: the anchored line's text
still exists, exactly once, in the same file at the indexed release. The claim still holds;
only the number is stale. That citation is rewritten in the card (the same token on the same
card line, ranges shifted by the same delta) and re-anchored at the new release.

Everything else - the text changed, it appears several times, the file is gone - is printed
as a worklist. Those are claims a person has to re-read; re-anchoring them blindly is how
the drift worklist was once zeroed by accident (`citations.py bless`, 2026-11-14).
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import citations as C

KB = HERE.parent


def plan():
    anchors = C.load_anchors()
    trees = C.Trees()
    moves, review = [], []
    for m in C.drift(anchors):
        old = trees.lines(m["repo"], m["ref"], m["path"])
        new = trees.lines(m["repo"], m["now"], m["path"])
        if old is None or new is None or m["line"] > len(old):
            review.append((m, "file gone or unreadable"))
            continue
        text = old[m["line"] - 1].strip()
        hits = [i + 1 for i, l in enumerate(new) if l.strip() == text] if len(text) >= 4 else []
        if len(hits) != 1:
            review.append((m, "text changed" if not hits else f"text now appears {len(hits)} times"
                           if hits else "trivial line"))
            continue
        moves.append((m, hits[0]))
    return anchors, moves, review


def apply(anchors, moves) -> int:
    n = 0
    for m, new_line in moves:
        key = m["key"]
        head, _, cl = key.rpartition("@")
        raw = head.partition("#")[2]
        cl = int(cl)
        mm = re.match(r"^(.*?):(\d+)(?:-(\d+))?$", raw)
        if not mm or int(mm.group(2)) != m["line"]:
            continue
        delta = new_line - m["line"]
        newraw = f"{mm.group(1)}:{new_line}" + (f"-{int(mm.group(3)) + delta}" if mm.group(3) else "")
        card = KB / m["card"]
        lines = card.read_text().split("\n")
        pat = re.compile(re.escape(raw) + r"(?![\d-])")
        if not (0 < cl <= len(lines)) or len(pat.findall(lines[cl - 1])) != 1:
            continue
        lines[cl - 1] = pat.sub(newraw, lines[cl - 1], count=1)
        card.write_text("\n".join(lines))
        a = anchors["anchors"].pop(key)
        a.update(line=new_line, ref=m["now"])
        anchors["anchors"][f"{m['card']}#{newraw}@{cl}"] = a
        n += 1
    C.ANCHORS.write_text(json.dumps(anchors, indent=1, sort_keys=True) + "\n")
    return n


def main() -> int:
    anchors, moves, review = plan()
    for m, nl in moves:
        print(f"  MOVE   {m['card'].replace('knowledge/', '')}: {m['path'].split('/')[-1]}:{m['line']} -> :{nl}")
    for m, why in review:
        print(f"  READ   {m['card'].replace('knowledge/', '')}: {m['path'].split('/')[-1]}:{m['line']} ({why})")
    if "--apply" in sys.argv and moves:
        print(f"\nrelocated {apply(anchors, moves)} citation(s)")
    print(f"{len(moves)} movable, {len(review)} need a person to re-read the claim")
    return 0


if __name__ == "__main__":
    sys.exit(main())

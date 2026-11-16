#!/usr/bin/env python3
"""PreCompact: carry the non-negotiables through a compaction.

Without this the platform overview and the active change set are the first things summarised
away, and the session quietly resumes guessing at platform rules.
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from e5_root import find_kb
# Resolved, not assumed: the plugin is installed as a COPY outside the KB.
KB = find_kb()
if KB is None:
    sys.exit(0)        # degrade silently; a hook must never block a session
sys.path.insert(0, str(KB / "tools"))

def main():
    keep = []
    ov = KB / "knowledge" / "00-overview.md"
    if ov.is_file():
        keep.append("### platform overview (knowledge/00-overview.md)\n" + ov.read_text())
    d = KB / ".e5" / "changesets"
    if d.is_dir():
        for cs in sorted(d.iterdir(), key=lambda p: -p.stat().st_mtime)[:1]:
            for art in ("design.md", "blast-radius.md", "lld.md", "plan.md"):
                p = cs / art
                if p.is_file():
                    keep.append(f"### active changeset {cs.name} / {art}\n{p.read_text()[:4000]}")
    if not keep: return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreCompact",
        "additionalContext": "PRESERVE VERBATIM - platform non-negotiables and the active "
                             "change set:\n\n" + "\n\n".join(keep)}}))
    return 0

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""SessionStart: load the L0 overview of the platform, plus one freshness line.

knowledge/00-overview.md is what a platform developer must know before any task:
philosophy, the map of areas, dependencies, the subsystem picture, which areas a kind of
change touches, and how to traverse. It is about 1.8k tokens, small enough to load every
session. Everything else is read on demand by path (kb_read) or by exact name
(kb_component, kb_flow, kb_contract). There is no search tool, on purpose.
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from e5_root import find_kb

KB = find_kb()
if KB is None:
    sys.exit(0)        # degrade silently; a hook must never block a session
sys.path.insert(0, str(KB / "tools"))


def main():
    parts = []
    ov = KB / "knowledge" / "00-overview.md"
    if ov.is_file():
        parts.append(ov.read_text())
    try:
        import kb_core as core
        f = core.freshness(brief=True)
        line = (f"e5-platform KB: {f['indexed_components']} components across "
                f"{f['indexed_repos']} repos")
        if f["stale_count"]:
            line += (f" | {f['stale_count']} repo(s) not on a release branch - facts from "
                     f"those are NOT current platform behaviour")
        parts.append(line)
    except Exception as e:
        parts.append(f"e5-platform KB: freshness unavailable ({type(e).__name__})")
    parts.append("Commands: /e5-init, /e5-sync, /e5-feature, /e5-bugfix, /e5-update-knowledge-base. Navigate the knowledge base by "
                 "path (kb_read) and exact name (kb_component, kb_flow, kb_contract, "
                 "kb_dependencies) starting from the overview above. Never guess: if no "
                 "document or extracted fact says it, say it is unknown.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "\n\n".join(parts)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

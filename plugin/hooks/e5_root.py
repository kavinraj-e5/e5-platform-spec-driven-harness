#!/usr/bin/env python3
"""Locate the knowledge base from wherever the plugin happens to be installed.

WHY THIS EXISTS
---------------
`claude plugin install` COPIES the plugin into
`~/.claude*/plugins/cache/<marketplace>/<plugin>/<version>/` and copies only
`hooks/`, `skills/`, `commands/` and `.claude-plugin/`. The knowledge base and `tools/`
stay where they are.

So the two shortcuts that worked in development both broke on install:
  - hooks did `Path(__file__).resolve().parents[2]`, which assumed the hook lived at
    `<kb>/plugin/hooks/`; in the cache it resolves to a version directory
  - plugin.json pointed the MCP server at `${CLAUDE_PLUGIN_ROOT}/../tools/...`, which
    does not exist once copied

Neither failed loudly. The hooks degraded to "knowledge base unavailable" and the MCP
server simply never started, so a teammate would have installed a plugin with no tools and
no error. Hence an explicit resolver, used by every entry point.
"""
from __future__ import annotations
import os
from pathlib import Path

MARKERS = ("registry.yaml", "tools/kb_core.py")


def _is_kb(p: Path) -> bool:
    return all((p / m).exists() for m in MARKERS)


def find_kb() -> Path | None:
    # 1. explicit wins, always
    env = os.environ.get("E5_KB_ROOT", "").strip()
    if env and _is_kb(Path(env)):
        return Path(env)

    # 2. a path recorded when the plugin was packaged. Written by
    #    `tools/prepare_plugin.py`, which is the only thing that knows where the KB was.
    pin = Path(__file__).resolve().parent / "kb_root.txt"
    if pin.is_file():
        cand = Path(pin.read_text().strip())
        if _is_kb(cand):
            return cand

    # 3. development layout: the hook still lives inside the KB
    for up in (2, 1, 3):
        cand = Path(__file__).resolve().parents[up] if len(
            Path(__file__).resolve().parents) > up else None
        if cand and _is_kb(cand):
            return cand

    # 4. walk up from the working directory - covers running inside the KB checkout
    cur = Path.cwd().resolve()
    for cand in (cur, *cur.parents):
        if _is_kb(cand):
            return cand
    return None


def kb_or_die(what: str = "the e5 knowledge base") -> Path:
    kb = find_kb()
    if kb is None:
        raise SystemExit(
            f"e5-platform: cannot locate {what}.\n"
            f"  Set E5_KB_ROOT to the e5-platform-kb directory, e.g.\n"
            f"    export E5_KB_ROOT=/path/to/e5-platform-kb\n"
            f"  (the plugin is installed as a copy and does not contain the knowledge "
            f"base itself)")
    return kb

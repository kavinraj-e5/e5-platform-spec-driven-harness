#!/usr/bin/env python3
"""PreToolUse: block edits inside a registered platform repo without an approved plan.

This is what makes "understand before you code" real rather than advisory.

It is deliberately narrow:
  - only files inside a repo listed in registry.yaml
  - never the knowledge base itself, and never a changeset artifact
  - an audited escape hatch exists, because a hard block with no escape gets the
    plugin uninstalled in a week
"""
import sys, json, re, os, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from e5_root import find_kb
# Resolved, not assumed: the plugin is installed as a COPY outside the KB.
KB = find_kb()
if KB is None:
    # A gate that cannot find the registry cannot know what is in scope. Allowing the
    # edit is the only safe failure: blocking every edit on a misconfigured install
    # would get the plugin removed, and silently blocking is worse than not gating.
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "permissionDecisionReason": "e5-platform: knowledge base not found (set "
                                    "E5_KB_ROOT); edit gate inactive"}}))
    sys.exit(0)
STATE = KB / ".e5"
UNLOCK = STATE / "unlock.json"
AUDIT = STATE / "audit.log"

def registry():
    txt = (KB / "registry.yaml").read_text()
    root = re.search(r'^workspace_root:\s*(\S+)', txt, re.M).group(1)
    repos, cur, inr = [], None, False
    for line in txt.splitlines():
        if line.startswith("repos:"): inr = True; continue
        if inr and re.match(r'^\w', line): inr = False
        if not inr: continue
        m = re.match(r'\s+-\s+id:\s*(\S+)', line)
        if m: cur = {"id": m.group(1)}; repos.append(cur); continue
        m = re.match(r'\s+(\w+):\s*(.*)$', line)
        if m and cur is not None: cur[m.group(1)] = m.group(2).strip()
    return root, repos

def active_changeset():
    """An approved plan unlocks edits. Approval is explicit and written down."""
    d = STATE / "changesets"
    if not d.is_dir(): return None
    for cs in sorted(d.iterdir(), key=lambda p: -p.stat().st_mtime):
        plan = cs / "plan.md"
        if plan.is_file() and re.search(r'^approved:\s*true\s*$', plan.read_text(), re.M | re.I):
            return cs.name
    return None

def unlocked():
    if not UNLOCK.is_file(): return None
    try: u = json.loads(UNLOCK.read_text())
    except Exception: return None
    if time.time() > u.get("expires_at", 0): return None
    return u

def deny(msg):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": msg}}))
    return 0

def main():
    try: payload = json.load(sys.stdin)
    except Exception: return 0
    tgt = (payload.get("tool_input") or {}).get("file_path")
    if not tgt: return 0
    tgt = os.path.abspath(tgt)

    # never gate the knowledge base or changeset artifacts
    if tgt.startswith(str(KB)): return 0

    try: root, repos = registry()
    except Exception: return 0

    hit = None
    for r in repos:
        p = os.path.abspath(os.path.join(root, r.get("path", "")))
        if tgt.startswith(p + os.sep):
            hit = r; break
    if not hit: return 0                      # not a platform repo - not our business

    cs = active_changeset()
    if cs: return 0

    u = unlocked()
    if u:
        try:
            AUDIT.parent.mkdir(parents=True, exist_ok=True)
            with AUDIT.open("a") as fh:
                fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                     "event": "edit-under-unlock", "repo": hit["id"],
                                     "file": tgt, "reason": u.get("reason")}) + "\n")
        except Exception: pass
        return 0

    return deny(
        f"Edit blocked: {tgt} is in the registered platform repo '{hit['id']}' and there is "
        f"no approved plan for an active change set.\n\n"
        f"The E5 platform mandate is to freeze the UX, then decide service ownership, then "
        f"plan, then code. A change here likely touches other repos - work through /e5-feature (blast radius gate) "
        f"first to see the blast radius and merge order.\n\n"
        f"To proceed:\n"
        f"  /e5-feature <what you want and the UX you expect>   run the gated workflow\n"
        f"  e5 unlock --reason \"...\"                            audited override (logged)\n")

if __name__ == "__main__":
    sys.exit(main())

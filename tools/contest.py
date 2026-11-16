#!/usr/bin/env python3
"""Contests - recorded human corrections to the knowledge base.

A contest does NOT assert the human is right. It asserts the fact is DISPUTED, by whom,
and what they said. The knowledge base's whole value is that every fact is verifiable;
a correction has no file:line until someone checks it. Recording the dispute is honest.
Silently overwriting a fact with an unverified claim is how a knowledge base quietly
becomes wrong.

  e5 contest new --kind fact --subject "..." --says "..." [--target ...]
  e5 contest list [--status open] [--mine]
  e5 contest show <id>
  e5 contest stats

This directory is HAND-MANAGED and must never be regenerated. tools/confluence's
write_drafts() unlinks its whole output directory on every run; any human review state
parked there is destroyed. test_contest.py greps for that pattern here.
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys, time
import os
from pathlib import Path

KB = Path(__file__).resolve().parent.parent
CONTESTS = KB / "contests"
sys.path.insert(0, str(Path(__file__).resolve().parent))

VALID_KINDS = ("fact", "rule", "rationale", "procedure")
VALID_STATUS = ("open", "verifying", "accepted", "rejected", "superseded")

def today() -> str:
    return time.strftime("%Y-%m-%d")

def whoami() -> str:
    """Attribution, not authentication.

    `E5_CONTEST_AUTHOR` wins over git config. This matters more than it looks: a correction
    dictated by a colleague, or captured by an assistant running under someone else's
    checkout, would otherwise be filed under whoever owns the local git config. A contest
    whose whole purpose is "someone is askable about this claim" must not name the wrong
    person - that is worse than recording no name, because it looks authoritative.
    """
    env = os.environ.get("E5_CONTEST_AUTHOR", "").strip()
    if env:
        return env
    for args in (["git", "config", "user.email"], ["git", "config", "--global", "user.email"]):
        try:
            r = subprocess.run(args, capture_output=True, text=True, cwd=str(KB), timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return ""

def slug(text: str, n: int = 52) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rstrip("-") or "contest"

def next_id(subject: str) -> str:
    return f"ctr-{today()}-{slug(subject)}"

def _fm(text: str) -> dict:
    if not text.startswith("---"): return {}
    try: block = text.split("---", 2)[1]
    except IndexError: return {}
    out = {}
    for line in block.splitlines():
        m = re.match(r"(\w+):\s*(.*)$", line)
        if m: out[m.group(1)] = m.group(2).strip()
    return out

def load_all() -> list[dict]:
    out = []
    if not CONTESTS.is_dir(): return out
    for p in sorted(CONTESTS.glob("*.md")):
        if p.name.startswith("_") or p.name == "README.md": continue
        t = p.read_text(); fm = _fm(t)
        if fm.get("type") != "contest": continue
        fm["_id"] = p.stem; fm["_path"] = p
        out.append(fm)
    return out

def infer_targets(subject: str, says: str) -> list[str]:
    """Best-effort targets so the overlay fires without the user having to know the
    fact schema. Explicit --target always wins; this only fills the gap."""
    try:
        import kb_core as core
    except Exception:
        return []
    hay = f"{subject} {says}".lower()
    tg = []
    for r in core._registry_repos():
        rid = r.get("id", "")
        if rid and (rid in hay or rid.replace("-", " ") in hay):
            tg.append(f"repo:{rid}")
    seen = set()
    for f in core.facts():
        nm = f["name"].split(".")[-1]
        if len(nm) > 5 and nm.lower() in hay:
            k = f"contract_key:{f['kind']}:{f['name']}"
            if k not in seen:
                seen.add(k); tg.append(k)
        if len(tg) > 12: break
    return tg

def cmd_new(a) -> int:
    if a.kind not in VALID_KINDS:
        print(f"--kind must be one of {VALID_KINDS}"); return 2
    who = a.by or whoami()
    if not who:
        # Attribution is the incentive to be careful. An anonymous correction has
        # neither accountability nor anyone to ask a follow-up question of.
        print("refusing to record an unattributed correction.\n"
              "  set one:  git config user.email you@e5.ai\n"
              "  or pass:  --by you@e5.ai")
        return 2
    targets = a.target or infer_targets(a.subject, a.says)
    if not targets:
        print("no targets resolved, so this contest would never surface on any answer.\n"
              "  pass at least one:  --target repo:workflow-manager\n"
              "                      --target contract_key:GrpcRpc:...\n"
              "                      --target card:knowledge/03-workflow-interactions/foo.md")
        return 2
    cid = a.id or next_id(a.subject)
    p = CONTESTS / f"{cid}.md"
    if p.exists():
        print(f"{p.relative_to(KB)} already exists"); return 2
    CONTESTS.mkdir(parents=True, exist_ok=True)
    body = [
        "---", "type: contest", "status: open", f"kind: {a.kind}",
        "severity: contested", f"subject: {a.subject}", "targets:",
        *[f"  - {t}" for t in targets],
        f"asserted_by: {who}", f"asserted_at: {today()}",
        "harness_said: >", f"  {a.harness_said or '(not captured)'}",
        "human_says: >", f"  {a.says}",
        f"evidence_offered: {a.evidence or 'none - experience'}",
        "verification:", "  outcome:", "  checked_at:", "  note:",
        "resolution:", "  route:", "  detail:",
        "sources: []", f"verified: {today()}", "---", "",
        f"# {a.subject}", "",
        f"**The harness said:** {a.harness_said or '(not captured)'}", "",
        f"**{who} says:** {a.says}", "",
        f"**Evidence offered:** {a.evidence or 'none - experience'}", "",
        "This is recorded as a dispute, not as a correction to the facts. It surfaces on "
        "every answer touching the targets above until someone adjudicates it against the "
        "code. See `contests/README.md` for how that works.", "",
    ]
    p.write_text("\n".join(body))
    print(f"recorded {p.relative_to(KB)}")
    print(f"  attributed to : {who}")
    print(f"  surfaces on   : {', '.join(targets[:6])}{' ...' if len(targets) > 6 else ''}")
    print(f"\nAnyone asking about this now sees the dispute. Next: e5 contest triage {cid}")
    return 0

def cmd_list(a) -> int:
    rows = load_all()
    if a.status: rows = [r for r in rows if r.get("status") == a.status]
    if a.mine:
        me = whoami()
        rows = [r for r in rows if r.get("asserted_by") == me]
    if not rows:
        print("no contests match."); return 0
    print(f"{len(rows)} contest(s)\n")
    for r in rows:
        print(f"  [{r.get('status','open'):9}] {r.get('kind','fact'):10} {r['_id']}")
        print(f"              {r.get('subject','')[:88]}")
        print(f"              by {r.get('asserted_by','?')} on {r.get('asserted_at','?')}")
    return 0

def cmd_show(a) -> int:
    p = CONTESTS / f"{a.id}.md"
    if not p.exists():
        print(f"no contest {a.id!r}"); return 2
    print(p.read_text()); return 0

def cmd_stats(a) -> int:
    """The honest metric is the repeat-correction rate: the same subject contested twice
    means the first fix did not work. A count of captured corrections looks like progress
    and is not."""
    rows = load_all()
    if not rows:
        print("no contests recorded yet.\n"
              "  Nothing to report. That is not the same as the KB being correct -\n"
              "  it means nobody has told it otherwise.")
        return 0
    import collections
    by_status = collections.Counter(r.get("status", "open") for r in rows)
    by_route = collections.Counter(r.get("route", "") for r in rows if r.get("route"))
    tgt = collections.Counter()
    for r in rows:
        for line in r["_path"].read_text().split("targets:", 1)[-1].split("asserted_by:")[0].splitlines():
            m = re.match(r"\s*-\s*(\S+)", line)
            if m: tgt[m.group(1)] += 1
    repeats = {t: n for t, n in tgt.items() if n > 1}
    resolved = sum(by_status[s] for s in ("accepted", "rejected", "superseded"))
    print(f"contests: {len(rows)}   open: {by_status['open']}   resolved: {resolved}\n")
    print("  by status:", dict(by_status))
    if by_route: print("  by route :", dict(by_route))
    print()
    print(f"  REPEAT-CORRECTION RATE: {len(repeats)} subject(s) contested more than once")
    if repeats:
        for t, n in sorted(repeats.items(), key=lambda x: -x[1])[:8]:
            print(f"    {n}x  {t}")
        print("\n  A repeat means the earlier fix did not work. This is the number that")
        print("  tells you whether the knowledge base is actually maturing.")
    else:
        print("    none - no subject has needed correcting twice.")
    stale = [r for r in rows if r.get("status") == "open" and r.get("asserted_at", "") < "2026-09-04"]
    if stale:
        print(f"\n  {len(stale)} open contest(s) older than 30 days - the queue is not being worked.")
    return 0

def main() -> int:
    ap = argparse.ArgumentParser(prog="contest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new")
    p.add_argument("--kind", default="fact", help=f"one of {VALID_KINDS}")
    p.add_argument("--subject", required=True)
    p.add_argument("--says", required=True, help="the correction, verbatim")
    p.add_argument("--harness-said", dest="harness_said", help="what the harness claimed, with its evidence")
    p.add_argument("--evidence", help="file:line, branch, PR or doc backing the correction")
    p.add_argument("--target", action="append", help="repeatable; repo:X / contract_key:K:N / card:path")
    p.add_argument("--by", help="override attribution (defaults to git user.email)")
    p.add_argument("--id")
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("list"); p.add_argument("--status"); p.add_argument("--mine", action="store_true")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("show"); p.add_argument("id"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("stats"); p.set_defaults(fn=cmd_stats)
    a = ap.parse_args()
    return a.fn(a)

if __name__ == "__main__":
    sys.exit(main())

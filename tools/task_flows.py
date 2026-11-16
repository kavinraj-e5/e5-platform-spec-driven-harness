#!/usr/bin/env python3
"""Task flows: the ordered chain of hops for one kind of work.

    python3 tools/task_flows.py list            every flow, one line each
    python3 tools/task_flows.py show <name>     one flow as a chain
    python3 tools/task_flows.py check           validate; exit 1 on an error
    python3 tools/task_flows.py facts           -> graph/flow-facts.jsonl

WHY A FLOW IS NOT AN EDGE
-------------------------
`platform_async` answers "who handles this task" - ONE hop. That is enough for a webhook
and useless for scaling an app worker, which is seven hops across four components with
two branches, resumed from a `phase` string in a database row.

A flow is the ordered sequence. It is AUTHORED, in
knowledge/_reference/joins/task-flows.yaml, because the chain crosses three mechanisms
(polled queue, gRPC, Kafka response) and branches on runtime state - no extractor
produces "then it waits for N responses and calls WorkflowOps once per pod".

WHICH IS WHY `check` EXISTS
---------------------------
Authored means it can rot. Every step is verified against things that are extracted:

  * `from` and `to` must be Components in the catalog
  * `via` must be a known mechanism
  * a `via: task` step must AGREE WITH THE ROUTING TABLE - if the flow says a task goes
    to workflow-manager and the routing table sends that queue to workflow-ops, that is
    an error, not a difference of opinion
  * every step must cite `<repo> <path>:<line>`, and the repo must be a real one
  * every task name in the routing table should belong to a flow - reported as a
    WARNING, so an unauthored flow is visible without blocking anyone

EXTENDING IT
------------
Add an entry to the YAML and run `check`. A flow may serve many task names
(`standard-subsystem-task` serves ten), which is how "the webhook flow is the same for
all webhooks" is expressed once rather than copied.
"""
from __future__ import annotations
import argparse, collections, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "release"))
import minyaml
import catalog as C

KB = HERE.parent
FLOWS = KB / "knowledge" / "flows" / "task-flows.yaml"
OUT = KB / "graph" / "flow-facts.jsonl"

# `poll` and `response` are distinct from `task` and `kafka` on purpose. A subsystem does
# not RECEIVE a task - it polls its own queue - and a reply is not a new task, it is a
# TaskComplete on the response topic. Collapsing them would make every flow look like a
# chain of pushes, which is the wrong mental model for debugging a stuck task.
MECHANISMS = {"task", "grpc", "kafka", "response", "poll", "http"}

# A placeholder in `standard-subsystem-task`: the step is the same shape for ten task
# names and the handler differs per name. Resolved from the routing table at read time
# rather than copied into ten near-identical flows.
PLACEHOLDERS = {"HANDLER", "TASK_NAME"}

_EVIDENCE = re.compile(r'^(\S+)\s+(\S+):(\d+)$')


def load() -> list[dict]:
    if not FLOWS.is_file():
        return []
    return minyaml.load(FLOWS.read_text()).get("flows") or []


def _routing() -> dict:
    """taskName -> handler, from the extracted async facts (not from the flow file)."""
    f = KB / "graph" / "async-facts.jsonl"
    out = {}
    if f.is_file():
        for line in f.open():
            d = json.loads(line)
            if d.get("kind") == "TaskRoute" and d.get("task_name"):
                out[d["task_name"]] = {"handled_by": d.get("handled_by"),
                                       "produced_by": d.get("component"),
                                       "task_type": d.get("task_type")}
    return out


def resolve(flow: dict, task_name: str | None = None) -> list[dict]:
    """A flow's steps with placeholders filled in for one task name."""
    route = _routing().get(task_name or "", {})
    steps = []
    for s in flow["steps"]:
        s = dict(s)
        if s.get("to") in PLACEHOLDERS:
            s["to"] = route.get("handled_by") or s["to"]
        if s.get("from") in PLACEHOLDERS:
            s["from"] = route.get("handled_by") or s["from"]
        if s.get("carries") == "TASK_NAME":
            s["carries"] = task_name or s["carries"]
        steps.append(s)
    return steps


def _covered(flows: list[dict]) -> dict:
    out = {}
    for f in flows:
        for n in ((f.get("applies_to") or {}).get("task_names") or []):
            out[n] = f["name"]
    return out


# --------------------------------------------------------------------------- views
def show(flows: list[dict], name: str, task_name: str | None = None) -> int:
    f = next((x for x in flows if x["name"] == name), None)
    if not f:
        # A TASK name is the more natural thing to type - you know the task that is
        # stuck, not the name someone gave its flow. Accept either, as platform_flow
        # already did; the CLI diverging from the MCP tool is its own small bug.
        for x in flows:
            if name in ((x.get("applies_to") or {}).get("task_names") or []):
                f, task_name = x, name
                break
    if not f:
        known = sorted({x["name"] for x in flows} |
                       {n for x in flows
                        for n in ((x.get("applies_to") or {}).get("task_names") or [])})
        print(f"no flow or task named {name!r}.\nknown: {', '.join(known)}")
        return 2
    print(f"\n{f['name']}")
    print("  " + " ".join((f.get("summary") or "").split()))
    names = (f.get("applies_to") or {}).get("task_names") or []
    if names:
        print(f"\n  applies to {len(names)} task name(s): {', '.join(names)}")
        if not task_name:
            task_name = names[0]
            if len(names) > 1:
                print(f"  (showing the chain for {task_name})")
    print()
    steps = resolve(f, task_name)
    for i, s in enumerate(steps, 1):
        cond = f"   [if {s['condition']}]" if s.get("condition") else ""
        q = f"  via {s['queue']}" if s.get("queue") else ""
        print(f"  {i}. {s['from']:18} --{s['via']:9}--> {s['to']:18} "
              f"{s.get('carries','')}{q}{cond}")
        print(f"       {s.get('evidence')}")
    chain = []
    for s in steps:
        if not chain:
            chain.append(s["from"])
        if chain[-1] != s["to"]:
            chain.append(s["to"])
    print(f"\n  chain: {' -> '.join(chain)}")
    return 0


def listing(flows: list[dict]) -> int:
    route = _routing()
    print(f"{len(flows)} flows\n")
    for f in flows:
        names = (f.get("applies_to") or {}).get("task_names") or []
        types = (f.get("applies_to") or {}).get("task_types") or []
        comps = []
        for s in f["steps"]:
            for c in (s["from"], s["to"]):
                if c not in PLACEHOLDERS and c not in comps:
                    comps.append(c)
        print(f"  {f['name']:32} {len(f['steps'])} steps  {len(comps)} components  "
              f"{len(names) or len(types)} {'task names' if names else 'task types'}")
    uncovered = sorted(set(route) - set(_covered(flows)))
    if uncovered:
        print(f"\n  {len(uncovered)} task name(s) with no authored flow: "
              f"{', '.join(uncovered)}")
    return 0


def check(flows: list[dict]) -> int:
    cat = C.load()
    route = _routing()
    repos = {(c.get("source") or {}).get("repo") for c in cat["Component"].values()}
    errs, warns = [], []
    seen = set()
    for f in flows:
        nm = f.get("name")
        if not nm or nm in seen:
            errs.append(f"flow {nm!r}: missing or duplicate name")
        seen.add(nm)
        if not (f.get("summary") or "").strip():
            errs.append(f"flow {nm}: no summary - a chain with no stated purpose is a diagram")
        if not f.get("steps"):
            errs.append(f"flow {nm}: no steps")
        for i, s in enumerate(f.get("steps") or [], 1):
            where = f"flow {nm} step {i}"
            for side in ("from", "to"):
                c = s.get(side)
                if c in PLACEHOLDERS:
                    continue
                if c not in cat["Component"]:
                    errs.append(f"{where}: {side} {c!r} is not a Component")
            if s.get("via") not in MECHANISMS:
                errs.append(f"{where}: via {s.get('via')!r} not in {sorted(MECHANISMS)}")
            ev = s.get("evidence") or ""
            m = _EVIDENCE.match(ev)
            if not m:
                errs.append(f"{where}: evidence {ev!r} is not '<repo> <path>:<line>'")
            elif m.group(1) not in repos:
                errs.append(f"{where}: evidence names unknown repo {m.group(1)!r}")
            # the load-bearing check: an authored task hop must match the extracted
            # routing table. This is what stops the file drifting into fiction.
            if s.get("via") == "task":
                carried = s.get("carries")
                if carried in route and s.get("to") not in PLACEHOLDERS:
                    actual = route[carried]["handled_by"]
                    if actual and actual != s.get("to"):
                        errs.append(
                            f"{where}: flow says {carried} goes to {s['to']!r} but the "
                            f"routing table says {actual!r}")
        for n in ((f.get("applies_to") or {}).get("task_names") or []):
            if n not in route:
                warns.append(f"flow {nm}: task name {n!r} is in no routing row")
    uncovered = sorted(set(route) - set(_covered(flows)))
    if uncovered:
        warns.append(f"{len(uncovered)} task name(s) have no authored flow: "
                     f"{', '.join(uncovered)}")
    # CONTENT, not format: every cited line must still show its hop at the indexed release,
    # and every routing row must still show its dispatch and its producer. Without this the
    # gate passed while 25 of 33 citations pointed at the wrong line (2026-11-14).
    try:
        import evidence as EV
        errs += EV.verify_flows() + EV.verify_task_names()
    except Exception as e:                      # mirrors absent: say so, do not pass silently
        warns.append(f"evidence content check could not run: {type(e).__name__}: {e}")
    for e in errs:
        print(f"ERROR  {e}")
    for w in warns:
        print(f"WARN   {w}")
    print(f"\n{len(flows)} flows, {sum(len(f.get('steps') or []) for f in flows)} steps "
          f"- {len(errs)} errors, {len(warns)} warnings")
    return 1 if errs else 0


def facts(flows: list[dict]) -> list[dict]:
    out = []
    for f in flows:
        names = (f.get("applies_to") or {}).get("task_names") or [None]
        for i, s in enumerate(resolve(f, names[0]), 1):
            out.append({
                "kind": "FlowStep", "flow": f["name"], "order": i,
                "from": s["from"], "to": s["to"], "via": s["via"],
                "carries": s.get("carries"), "queue": s.get("queue"),
                "condition": s.get("condition"),
                "applies_to_task_names": (f.get("applies_to") or {}).get("task_names"),
                "applies_to_task_types": (f.get("applies_to") or {}).get("task_types"),
                "evidence": s.get("evidence"),
                "provenance": "authored-join", "confidence": "CURATED",
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "show", "check", "facts"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--task", help="resolve placeholders for this task name")
    a = ap.parse_args()
    flows = load()
    if a.cmd == "list":
        return listing(flows)
    if a.cmd == "show":
        if not a.name:
            print("show needs a flow name"); return 2
        return show(flows, a.name, a.task)
    if a.cmd == "facts":
        fs = facts(flows)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in fs))
        print(f"{len(fs)} flow steps -> {OUT.relative_to(KB)}")
        return 0
    return check(flows)


if __name__ == "__main__":
    sys.exit(main())

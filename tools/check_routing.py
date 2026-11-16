#!/usr/bin/env python3
"""Verify the two task-routing switches agree.

This is the check the plan calls out as the highest-value automated verification:
the endpoint->consumer edge is a JOIN of two switch statements in different classes,
keyed on the same string, written in different syntaxes (one constants, one literals),
with no default branch. Adding a task type to one and not the other produces a silent
null virtual queue and the task is dispatched nowhere.

Exit 0 = in sync. Exit 1 = desynchronised. Intended as a CI gate.
"""
import json, sys, collections
from pathlib import Path

KB = Path(__file__).resolve().parent.parent
FACTS = KB / "graph" / "facts.jsonl"

# a rule only counts as task routing if it actually assigns a routing outcome
ROUTING_PROPS = ("sets_task_type", "sets_fb_name", "sets_vq")

def main():
    assigns_type, assigns_vq, risks = {}, {}, []
    for line in FACTS.open():
        f = json.loads(line)
        p = f.get("props", {})
        if f["kind"] == "RoutingRisk":
            risks.append(f); continue
        if f["kind"] != "RoutingRule":
            continue
        if not any(k in p for k in ROUTING_PROPS):
            continue                                  # health-check / decision switches
        for name in p["task_names"]:
            if "sets_task_type" in p or "sets_fb_name" in p:
                assigns_type[name] = {
                    "task_type": p.get("sets_task_type"), "fb_name": p.get("sets_fb_name"),
                    "where": f"{f['path']}:{f['line']}", "form": p["label_form"][0]}
            if "sets_vq" in p:
                assigns_vq[name] = {"vq": p["sets_vq"],
                    "where": f"{f['path']}:{f['line']}", "form": p["label_form"][0]}

    only_type = sorted(set(assigns_type) - set(assigns_vq))
    only_vq   = sorted(set(assigns_vq) - set(assigns_type))
    both      = sorted(set(assigns_type) & set(assigns_vq))

    print(f"task-routing table: {len(both)} task names fully routed\n")
    w = max((len(x) for x in both), default=10)
    for n in both:
        t, v = assigns_type[n], assigns_vq[n]
        print(f"  {n:<{w}}  {t['task_type'] or '-':16} {t['fb_name'] or '-':26} -> {v['vq']}")

    forms = {t["form"].split(':')[0] for t in assigns_type.values()} | \
            {v["form"].split(':')[0] for v in assigns_vq.values()}
    # Blocking vs advisory matters. A gate that fails on a pre-existing structural
    # weakness blocks every PR and gets switched off within a week. Only an actual
    # desynchronisation blocks; fragility warns.
    problems, warnings = [], []
    if len(forms) > 1:
        warnings.append(
            "the two switches use DIFFERENT case-label forms "
            f"({', '.join(sorted(forms))}) so the compiler cannot relate them; "
            "renaming a constant's value would desynchronise them silently")
    if only_type:
        problems.append(f"assigned a taskType/fbName but NO virtual queue -> "
                        f"dispatched nowhere: {', '.join(only_type)}")
    if only_vq:
        problems.append(f"assigned a virtual queue but NO taskType/fbName -> "
                        f"consumer cannot dispatch: {', '.join(only_vq)}")

    if risks:
        print("\nstructural risks detected:")
        for r in risks:
            print(f"  [{r['props']['severity'].upper():6}] {r['path'].split('/')[-1]}: {r['props']['issue']}")

    if warnings:
        print("\nwarnings (advisory, non-blocking):")
        for w in warnings: print(f"  ~ {w}")

    if problems:
        print("\nFAIL - routing tables are desynchronised:")
        for p in problems: print(f"  * {p}")
        return 1
    print(f"\nPASS - both switches cover the same {len(both)} task names")
    return 0

if __name__ == "__main__":
    sys.exit(main())

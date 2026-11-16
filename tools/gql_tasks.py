#!/usr/bin/env python3
"""Which task each GraphQL operation on `interface` sends - the first hop of every flow.

    python3 tools/gql_tasks.py extract   -> graph/gql-task-facts.jsonl

The knowledge base knew `Mutation.pauseOrResumeApp` (the GraphQL schema) and the task
`PAUSE_OR_RESUME_APP` (task-names.yaml), but not that one leads to the other: the link is
three method calls deep (TaskController -> TaskManagerHelperImpl -> TaskService). Joining them
by similar names would be a guess.

So this follows the CALLS, with tools/java_ast.py's declared-type resolution (it matched
bytecode and jdtls exactly in the 2026-11-14 trial; the `name-only` fallback is never used):
from each `@MutationMapping` / `@QueryMapping` handler, breadth-first up to 6 calls deep,
collecting every known task name that a reached method mentions - as a string literal or as
a constant whose value is one. A method naming 3+ different tasks is a dispatch table (the
routing switch) and is not evidence of which task an operation sends; it is skipped.
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "release"))
import kb_core as K
import minyaml
from java_ast import JavaAST

KB = HERE.parent
OUT = KB / "graph" / "gql-task-facts.jsonl"
_MAP = re.compile(r"@(Mutation|Query)Mapping(?:\(\s*(?:name\s*=\s*)?\"(\w+)\"\s*\))?")
DEPTH = 6
_SENDS = re.compile(r"setTaskType\(|\.taskType\(|buildTaskTransport\(|\.performTask\(|"
                    r"sendTaskToTaskManager\(|FB_NAME\s*,|[\"']fbName[\"']\s*,")
_DYNAMIC = re.compile(r"setTaskType\(\s*([a-z]\w*)\s*\)")


def _token_to_task() -> dict:
    """Every token that identifies exactly one routing row -> that row's task name: the task
    name, the fbName, and a task TYPE used by only one row (RELEASE_CREDENTIAL). Shared task
    types (E5_WF_MANAGER, E5_WORKFLOW_OPS) identify nothing and are left out."""
    import collections
    rows = minyaml.load((KB / "knowledge" / "flows" / "task-names.yaml").read_text())["rows"]
    out = {}
    for r in rows:
        out[r["task_name"]] = r["task_name"]
        if r.get("fb_name"):
            out[r["fb_name"]] = r["task_name"]
    per_type = collections.Counter(r.get("task_type") for r in rows if r.get("task_type"))
    for r in rows:
        t = r.get("task_type")
        if t and per_type[t] == 1 and t not in out:
            out[t] = r["task_name"]
            _TYPE_TOKENS.add(t)
    for r in rows:
        if r.get("fb_name") and r["fb_name"] != r["task_name"]:
            _FB_TOKENS.add(r["fb_name"])
    return out


# Where each kind of token is allowed to count (2026-11-14 cross-test: `WorkflowOps`, the task
# type of RestartWorkflow, is also a destination name and produced false links):
_TYPE_TOKENS: set = set()     # only inside setTaskType(...) / .taskType(...)
_FB_TOKENS: set = set()       # only on a line that sets FB_NAME / "fbName"
_TYPE_SITE = re.compile(r"setTaskType\(|\.taskType\(")
_FB_SITE = re.compile(r"FB_NAME|[\"']fbName[\"']")


def _allowed(token: str, line: str) -> bool:
    if token in _TYPE_TOKENS:
        return bool(_TYPE_SITE.search(line))
    if token in _FB_TOKENS:
        return bool(_FB_SITE.search(line))
    return True


def _known_tasks() -> set:
    return set(_token_to_task())


def extract(repo_id: str = "interface") -> list[dict]:
    r = next(x for x in K._registry_repos() if x["id"] == repo_id)
    gd, br = os.path.expanduser(r["mirror"]), r["branch"]
    tok2task = _token_to_task()
    known = set(tok2task)
    facts = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(f"git -C {gd} archive refs/heads/{br} | tar -x -C {tmp}", shell=True, check=True)
        src = Path(tmp) / "src" / "main" / "java"
        ast = JavaAST(src)
        consts = {}
        for c in ast.classes.values():
            for k, v in c.get("consts", {}).items():
                if v in known:
                    consts[k] = v
        # entry points: methods annotated @MutationMapping / @QueryMapping
        entries = []
        for f in sorted(src.rglob("*.java")):
            lines = f.read_text(errors="replace").splitlines()
            for i, l in enumerate(lines):
                m = _MAP.search(l)
                if not m:
                    continue
                mid = None
                for j in range(i + 1, min(i + 6, len(lines))):
                    mid = ast.method_at(str(f), j + 1)
                    if mid and ast.methods[mid]["start"] >= i + 1:
                        break
                if mid:
                    field = m.group(2) or ast.methods[mid]["name"]
                    entries.append((m.group(1), field, mid))
        for kind, field, mid in entries:
            seen, frontier, found = {mid}, [mid], {}
            for _ in range(DEPTH):
                nxt = []
                for x in frontier:
                    meth = ast.methods[x]
                    body = Path(meth["file"]).read_text(errors="replace").splitlines()[meth["start"] - 1:meth["end"]]
                    text = "\n".join(body)
                    named = (set(re.findall(r'"(\w+)"', text)) & known) | \
                            {v for c, v in consts.items() if re.search(rf"\b{re.escape(c)}\b", text)}
                    if len(named) >= 3:
                        # a method naming 3+ tasks is a DISPATCH TABLE (the routing switch,
                        # already in task-names.yaml), not where an operation picks its task
                        named = set()
                    if not _SENDS.search(text):
                        # only a method that BUILDS or SENDS a task is evidence; a method that
                        # formats a task's RESULT (e.g. buildWorkflowResourceProvisioningTaskStatus)
                        # merely mentions the name
                        named = set()
                    for dm in _DYNAMIC.finditer(text):
                        if not named and dm.group(1) not in consts:
                            ln = meth["start"] + text[:dm.start()].count("\n")
                            found.setdefault(f"<dynamic: {dm.group(1)}>", (x, ln))
                    for lit in set(re.findall(r'"(\w+)"', text)) & named:
                        for i, l in enumerate(body):
                            if f'"{lit}"' in l and _allowed(lit, l):
                                found.setdefault(lit, (x, meth["start"] + i))
                                break
                    for cname, val in consts.items():
                        if val not in named:
                            continue
                        for i, l in enumerate(body):
                            if re.search(rf"\b{re.escape(cname)}\b", l) and _allowed(val, l):
                                found.setdefault(val, (x, meth["start"] + i))
                                break
                    for call in meth["calls"]:
                        targets, how = ast.resolve_call(x, call)
                        if how == "name-only":
                            continue
                        for t in targets:
                            if t not in seen:
                                seen.add(t); nxt.append(t)
                frontier = nxt
                if not frontier:
                    break
            handler = ast.methods[mid]
            for token, (at_mid, line) in sorted(found.items()):
                task = tok2task.get(token, token)
                m2 = ast.methods[at_mid]
                facts.append({"kind": "GraphQLSendsTask", "component": repo_id, "repo": repo_id,
                              "graphql": f"{kind}.{field}", "task_name": task, "token": token,
                              "handler": f"{handler['cls'].split('.')[-1]}.{handler['name']}",
                              "path": str(Path(m2["file"]).relative_to(Path(tmp))), "line": line,
                              "handler_path": str(Path(handler["file"]).relative_to(Path(tmp))),
                              "handler_line": handler["start"], "branch": br, "commit": r.get("commit"),
                              "provenance": "code", "confidence": "EXTRACTED"})
    return facts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["extract"])
    ap.parse_args()
    facts = extract()
    uniq = {}
    for f in facts:
        uniq.setdefault((f["graphql"], f["task_name"]), f)
    facts = list(uniq.values())
    OUT.write_text("".join(json.dumps(f, sort_keys=True) + "\n" for f in facts))
    ops = {f["graphql"] for f in facts}
    print(f"{len(facts)} GraphQL->task links over {len(ops)} operations -> {OUT.relative_to(KB)}")
    for f in facts:
        print(f"  {f['graphql']:40} -> {f['task_name']:34} ({f['path'].split('/')[-1]}:{f['line']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

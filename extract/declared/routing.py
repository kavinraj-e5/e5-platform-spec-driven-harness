"""The routing table: which taskName reaches which destination.

This is the load-bearing cross-repo link. Once queue and topic VALUES stopped being
extracted (`docs/2026-10-13-extraction-redesign.md`), the only thing left that joins two
repositories from source alone is the **task name** — a string one repo writes into a
switch label and another repo compares in its own switch. No injected value is involved,
so it is a fact about the code and not about a deployment.

The platform writes that table TWICE, in two classes, in two different forms: one with
string literals, one with named constants. The compiler cannot compare them, so a rename
on one side desynchronises them silently. That is what `tools/check_routing.py` blocks
on, and it needs the label form to do it.
"""
from __future__ import annotations

from extract.core import Repo, fact
from extract.parse.java import JavaFile

# Routing lives in a handful of named classes. This is a SELECTION rule, not a parsing
# one — kept from the regex version so the port is comparable, and because a switch in
# an unrelated class is not the routing table however much it looks like one.
FILE_HINTS = ("TaskManagerHelper", "TaskManagerProtocol", "RoutingTable",
              "TaskRoute", "Routing", "TaskHandler", "TaskAction")

_FB_KEYS = ("InterfaceConstants.FB_NAME", '"fbName"')
_VQ_KEYS = ("InterfaceConstants.VQ_NAME", '"vqName"')


def _member(arg: str, holder: str) -> str:
    """`TaskType.E5_WF_MANAGER.name()` with holder `TaskType` -> `E5_WF_MANAGER`.

    The platform writes both `Task.EXPORT_STATE` and `Task.EXPORT_STATE.name()`, and the
    joinable identity is the enum constant in the middle either way. The regex captured
    `(\w+)` and so happened to stop in the right place; taking the segment after the
    holder states that rather than relying on where a character class runs out.
    """
    parts = arg.split(".")
    if len(parts) < 2 or parts[0] != holder:
        return ""
    return parts[1] if parts[1].isidentifier() else ""


def _constants(repo: Repo) -> dict:
    """Every `final String` in the repo, so a constant case label resolves to its value.

    Repo-wide rather than per-file because the labels and the constants are in different
    classes by design: `InterfaceConstants` exists precisely so the wire value is written
    once.
    """
    table = {}
    for path in repo.files(".java", include_tests=True):
        src = repo.read(path)
        if "String" not in src:
            continue
        for name, (value, _line) in JavaFile(src).constants().items():
            table.setdefault(name, value)
    return table


def _destination(arm) -> dict:
    """What this arm sets, read from its OWN calls.

    The regex version read a 400-character window after the case label, which crosses
    into the following arms: an arm that sets nothing inherited its neighbour's
    destination, and the reader had no way to tell. An arm's calls belong to that arm.

    `sets_vq` is a Java FIELD NAME (`workflowManagerVQ`), never a queue name. The value
    that field holds arrives from config at deploy time and is deliberately not read.
    """
    props = {}
    for c in arm.calls:
        if c.name == "setTaskType" and c.args:
            tt = _member(c.args[0], "TaskType")
            if tt:
                props.setdefault("sets_task_type", tt)
        if c.name != "put" or len(c.args) < 2:
            continue
        fb = _member(c.args[1], "Task") if c.args[0] in _FB_KEYS else ""
        if fb:
            props.setdefault("sets_fb_name", fb)
        if c.args[0] in _VQ_KEYS and c.args[1].isidentifier():
            props.setdefault("sets_vq", c.args[1])
    if not props:
        props["sets"] = " ".join(arm.body.split())[:140]
    return props


def routing(repo: Repo) -> list[dict]:
    out = []
    consts = _constants(repo)
    for path in repo.files(".java", include_tests=True):
        if not any(h in path.rsplit("/", 1)[-1] for h in FILE_HINTS):
            continue
        src = repo.read(path)
        if "switch" not in src:
            continue
        f = JavaFile(src)
        switches = f.switches()
        for sw in switches:
            for arm in sw.arms:
                labels, forms = [], []
                for text, is_literal in arm.labels:
                    if is_literal:
                        labels.append(text)
                        forms.append("literal")
                    elif text.rsplit(".", 1)[-1] in consts:
                        # The one the regex could not reach: its label character class
                        # had no `.`, so `case InterfaceConstants.X ->` matched nothing
                        # and the arm was invisible.
                        labels.append(consts[text.rsplit(".", 1)[-1]])
                        forms.append(f"constant:{text}")
                    else:
                        labels.append(text)
                        forms.append("unresolved")
                out.append(fact("RoutingRule", f"{repo.id}:{path}:{arm.line}",
                                repo.id, path, arm.line, "code",
                                task_names=labels, label_form=forms, style=sw.style,
                                selector=sw.selector, dispatch_method=sw.owner,
                                source_file=path.rsplit("/", 1)[-1],
                                **_destination(arm)))

        # ---- risks. A finding, not a fact about a symbol, so it is keyed on the file.
        # Per-switch rather than per-file: the regex asked whether the WORD `default`
        # appeared anywhere in the text, so one unrelated switch with a default arm
        # silenced the finding for the routing switch beside it.
        bad = [s for s in switches
               if "taskName" in s.selector and s.arms and not s.has_default]
        if bad:
            out.append(fact("RoutingRisk", f"{repo.id}:{path}:no-default", repo.id,
                            path, 1, "code",
                            issue="switch on taskName has no default branch; "
                                  "an unknown taskName leaves the target null",
                            severity="high",
                            switch_lines=[s.line for s in bad]))

        # The same payload key written two ways in one file. Read from the parse tree,
        # so the key named in a comment or a log message does not count.
        reads_const = any(o == "InterfaceConstants" and fld == "FB_NAME"
                          for o, fld, _ in f.field_reads())
        writes_raw = any(s.value == "fbName" for s in f.strings())
        if reads_const and writes_raw:
            out.append(fact("RoutingRisk", f"{repo.id}:{path}:mixed-fbname-key", repo.id,
                            path, 1, "code",
                            issue='fbName payload key written both as '
                                  'InterfaceConstants.FB_NAME and as the raw literal '
                                  '"fbName" in the same file',
                            severity="medium"))
    return out

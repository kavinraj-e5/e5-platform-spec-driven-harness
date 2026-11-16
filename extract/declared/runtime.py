"""What a deployable can be asked to do: handler classes, worker roles, db bindings."""
from __future__ import annotations

from extract.core import Repo, fact
from extract.parse.java import JavaFile
from extract.parse.pyfile import PyFile

JAVAISH = (".java", ".kt")

# A role's task surface. `*InboundActionHandler` is the engine side,
# `*TemporalActivityImpl` the worker side, `Aggregator` the aggregator.
_HANDLER_SUFFIXES = ("InboundActionHandler", "TemporalActivityImpl", "Aggregator",
                     "TaskHandler", "ActionHandler")


def handlers(repo: Repo) -> list[dict]:
    """The classes that actually do the work, per role.

    Equivalent in value to an RPC list for a gRPC service: the answer to "what can this
    role be asked to do". There is no proto to read it out of, which is why it needs its
    own extractor rather than being a gap in the existing ones.

    The regex version matched `(?:class|interface)\\s+(\\w*TaskHandler\\w*)` anywhere in
    the text, so a comment mentioning TaskHandler or a string containing it produced a
    class that does not exist. This reads declarations.
    """
    out, seen = [], set()
    for path in repo.files(*JAVAISH, ".py", include_tests=True):
        src = repo.read(path)
        if not any(s in src for s in _HANDLER_SUFFIXES):
            continue
        if path.endswith(".py"):
            decls = [(c.name, "class", c.line, "") for c in PyFile(src).classes()]
        else:
            # class and interface ONLY. Including enums made the parser report
            # `AggregatorTargetType` - an enum of target types - as a handler, and
            # Java's `@interface` (an annotation type) is a third thing again: the
            # regex matched the token `interface` inside `public @interface E5Aggregator`
            # and reported a marker annotation as a handler class.
            decls = [(t.name, t.kind, t.line, t.extends) for t in JavaFile(src).types()
                     if t.kind in ("class", "interface")]
        for name, kind, line, extends in decls:
            if not any(s in name for s in _HANDLER_SUFFIXES):
                continue
            # A short name beginning I/Base/Abstract is a marker or a scaffold, not a
            # handler. Parenthesised because in the regex version the precedence of
            # `or` and `and` made this condition accidental rather than stated.
            if name.startswith(("I", "Base", "Abstract")) and len(name) < 6:
                continue
            if name in seen:
                continue
            seen.add(name)
            out.append(fact("Handler", name, repo.id, path, line, "code",
                            declared_as=kind, extends=extends or None))
    return out


def worker_roles(repo: Repo) -> list[dict]:
    """One fact per WORKER_MODE value the image accepts.

    The single most load-bearing fact about the workflow plane: five services that look
    separate in Kubernetes are the SAME image, and the only thing separating them is
    this switch. A repo-keyed index cannot represent that at all.

    The file must read the env var AND the modes must be switch labels. The regex
    version matched `case X ->` anywhere in such a file, so an unrelated switch in the
    same class contributed phantom roles, and it needed a hand-kept deny-list
    (`TRUE`, `FALSE`, `NULL`, `DEFAULT`) to suppress them.
    """
    out = []
    for path in repo.files(*JAVAISH, ".py", include_tests=True):
        src = repo.read(path)
        if "WORKER_MODE" not in src:
            continue
        if not path.endswith(JAVAISH):
            continue                      # python roles are read by profiles, not here
        f = JavaFile(src)
        reads_mode = any(s.value == "WORKER_MODE" for s in f.strings())
        if not reads_mode:
            continue
        for sw in f.switches():
            for arm in sw.arms:
                for text, is_literal in arm.labels:
                    mode = text.rsplit(".", 1)[-1]
                    if len(mode) < 3 or not mode.isupper():
                        continue
                    out.append(fact("WorkerRole", mode, repo.id, path, arm.line, "code",
                                    dispatch_file=path, selector=sw.selector,
                                    label_is_literal=is_literal))
    return out


def db_bindings(repo: Repo) -> list[dict]:
    """Which database binding a role opens.

    Distinguishes the roles sharply: AKST binds the akst schema, wf-engine
    CORE_INTERNAL, wf-worker STATE.
    """
    out, seen = [], set()
    for path in repo.files(*JAVAISH, include_tests=True):
        f = JavaFile(repo.read(path))
        for obj, fld, line in f.field_reads():
            if obj != "DBAccessType":
                continue
            if (fld, path) in seen:
                continue
            seen.add((fld, path))
            out.append(fact("DbBinding", fld, repo.id, path, line, "code"))
    return out

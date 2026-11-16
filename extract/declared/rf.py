"""The Robot Framework keyword surface - what an RPA step in a workflow can call."""
from __future__ import annotations

from extract.core import Repo, fact
from extract.parse.pyfile import PyFile

# The fb-worker is a Robot Framework library: `rf` in rf-fb-worker-core. Its contract
# with a workflow is the KEYWORD SET. There is no proto, no schema and no annotation;
# the surface is the public module-level functions of the keyword modules, and without
# this the component that performs the actual automation contributes nothing but a
# handful of import lines.
_MODULES = ("keywords.py", "decorators.py", "interfaces.py", "validators.py")


def rf_keywords(repo: Repo) -> list[dict]:
    """Public, module-level functions only.

    `top_level` is the whole reason this moved to a parser. The regex
    `^def\\s+([a-z][a-z0-9_]*)\\s*\\(` with re.M also matched methods inside classes and
    nested helper functions, so a private inner function became part of a component's
    published surface - measured: `execute_akst_task` in keywords.py is nested and was
    being reported as a keyword.
    """
    out = []
    for path in repo.files(".py", include_tests=True):
        if path.rsplit("/", 1)[-1] not in _MODULES:
            continue
        f = PyFile(repo.read(path))
        if not f.ok:
            # Reported, never silently empty: an unparseable file and a file with no
            # keywords must not look the same.
            out.append(fact("RpaKeywordParseError", path.rsplit("/", 1)[-1], repo.id,
                            path, 1, "code", confidence="UNKNOWN"))
            continue
        for fn in f.functions():
            if not fn.top_level or fn.name.startswith("_"):
                continue
            out.append(fact("RpaKeyword", fn.name, repo.id, path, fn.line, "code",
                            module=path.rsplit("/", 1)[-1],
                            decorators=list(fn.decorators) or None))
    return out

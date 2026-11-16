"""Dependencies a Component declares on published artifacts."""
from __future__ import annotations

from extract.core import Repo, fact
from extract.parse.java import JavaFile
from extract.parse.pyfile import PyFile

# Imports from a PUBLISHED artifact. These are the cross-boundary contract edges no
# source extractor could otherwise see - the fb-worker's entire AKST contract arrives
# this way, which is why that repo contains no proto.
_SDK_ROOTS = ("com.e5.platform.sdk", "com.e5.common.utils", "com.e5.state",
              "e5.appsdk", "e5_task_sdk_python", "e5.functionblock")


def sdk_imports(repo: Repo) -> list[dict]:
    """Which published artifact each Component depends on, at package granularity.

    `inferred-from-import` rather than `code`: the import proves a dependency on the
    package, not what the package contains. The artifact's own contents need
    tools/artifacts.py, and until that runs this fact is a pointer, not a definition.
    """
    out, seen = [], set()
    for path in repo.files(".java", ".kt", ".py", include_tests=True):
        src = repo.read(path)
        if not any(r.split(".")[0] in src for r in _SDK_ROOTS):
            continue
        parser = PyFile if path.endswith(".py") else JavaFile
        for fqn, line in parser(src).imports():
            if not fqn.startswith(_SDK_ROOTS):
                continue
            # Collapse to the owning artifact root; per-class imports are noise.
            parts = fqn.rstrip(".").split(".")
            art = ".".join(parts[:4]) if len(parts) > 4 else fqn.rstrip(".")
            if (art, path) in seen:
                continue
            seen.add((art, path))
            out.append(fact("SdkDependency", art, repo.id, path, line,
                            "inferred-from-import"))
    return out

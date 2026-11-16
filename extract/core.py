"""The fact schema and the one git accessor.

`fact()` is byte-compatible with `tools/extractors.fact()` on purpose: every port is
verified by DIFFING its output against the regex extractor it replaces, and a diff is
only meaningful if the shape is identical.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

KB = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", "build", ".gradle", "target", "node_modules", "__pycache__",
             ".venv", "dist", ".idea", ".pytest_cache", "bin", "out", ".mvn"}

# One definition, used by every extractor that needs to know. Four modules previously
# carried their own near-copy of this and they had drifted: one matched `Tests?.java$`
# and another did not, so the same file was a fixture to one extractor and production
# code to the next.
TEST_PATH = re.compile(r'(^|/)(test|tests|__tests__)/|(^|/)test_|_test\.(py|java)$'
                       r'|Tests?\.java$|\.spec\.|src/test/')


def is_test(path: str) -> bool:
    return bool(TEST_PATH.search(path))


def fact(kind, name, repo, path, line, provenance, confidence="EXTRACTED", **props):
    """A fact is an OCCURRENCE; a contract is an IDENTITY.

    Identical to `tools/extractors.fact` so new and old output can be compared field by
    field. `id` is unique per occurrence; `contract_key` is the repo-independent identity
    that cross-repo joins and release diffs key on.
    """
    return {"id": f"{kind}:{repo}:{path}:{line}:{name}", "contract_key": f"{kind}:{name}",
            "kind": kind, "name": name, "repo": repo,
            "path": path, "line": line, "provenance": provenance,
            "confidence": confidence, "props": props}


# --------------------------------------------------------------------- git, once
@dataclass
class Repo:
    """A bare mirror at one ref, with its tree and blobs read once.

    This replaces four separate `_git` helpers (`async_graph`, `build_graph`,
    `codegraph`, `citations`), each spawning its own subprocesses. `codegraph` already
    records why that matters: one `git show` per file meant thousands of spawns and blew
    a two-minute budget. Here the tree is one `ls-tree` and each blob is read at most
    once per run.
    """
    id: str
    gitdir: str
    ref: str
    commit: str = ""
    branch: str = ""
    _tree: list | None = field(default=None, repr=False)
    _blobs: dict = field(default_factory=dict, repr=False)

    def _git(self, *args) -> str:
        p = subprocess.run(["git", "--git-dir", self.gitdir, *args],
                           capture_output=True, text=True, errors="replace")
        return p.stdout if p.returncode == 0 else ""

    def tree(self) -> list[str]:
        if self._tree is None:
            self._tree = [p for p in self._git("ls-tree", "-r", "--name-only",
                                               self.ref).splitlines()
                          if p and not any(d in p.split("/") for d in SKIP_DIRS)]
        return self._tree

    def files(self, *suffixes: str, include_tests: bool = False) -> list[str]:
        out = [p for p in self.tree() if p.endswith(suffixes)]
        return out if include_tests else [p for p in out if not is_test(p)]

    def read(self, path: str) -> str:
        if path not in self._blobs:
            self._blobs[path] = self._git("show", f"{self.ref}:{path}")
        return self._blobs[path]

    def stamp(self, facts: list[dict]) -> list[dict]:
        """Every fact carries the commit it came from, not just the run's timestamp.

        Repo-level provenance in versions.json only ever describes the LAST index run,
        which is how a snapshot of an old checkout can claim today's commit.
        """
        for f in facts:
            f["commit"] = self.commit
            f["branch"] = self.branch
        return facts


def from_registry(only: list[str] | None = None) -> list[Repo]:
    """Bare mirrors from registry.yaml. Never a working copy - the original defect was
    indexing whatever branch a checkout happened to be sitting on, including a year-old
    `main`."""
    import sys
    sys.path.insert(0, str(KB / "tools"))
    import mirror as M                                  # one parser for registry.yaml
    out = []
    for r in M.registry()[1]:
        if only and r["id"] not in only:
            continue
        gitdir = os.path.expanduser(r.get("mirror") or "")
        if not gitdir or not os.path.isdir(gitdir):
            continue
        out.append(Repo(id=r["id"], gitdir=gitdir, ref=r["branch"],
                        commit=r.get("commit", ""), branch=r.get("branch", "")))
    return out


@dataclass
class DirRepo:
    """The same read interface over a filesystem tree instead of a bare mirror.

    `tools/sync.py` materialises each component's tree once and passes a root path to
    every extractor; the ported extractors take a source object instead. This adapter is
    what lets the new code be called from the old pipeline without `sync.py` changing,
    and without the front ends ever learning about the filesystem.
    """
    id: str
    root: Path
    _tree: list | None = field(default=None, repr=False)

    def tree(self) -> list[str]:
        if self._tree is None:
            out = []
            for p in self.root.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(self.root)
                if any(d in rel.parts for d in SKIP_DIRS):
                    continue
                out.append(str(rel))
            self._tree = sorted(out)
        return self._tree

    def files(self, *suffixes: str, include_tests: bool = False) -> list[str]:
        out = [p for p in self.tree() if p.endswith(suffixes)]
        return out if include_tests else [p for p in out if not is_test(p)]

    def read(self, path: str) -> str:
        try:
            return (self.root / path).read_text(errors="replace")
        except OSError:
            return ""


def as_dir(repo: Repo):
    """Materialise a repo into a temp dir, for the OLD extractors only.

    `tools/extractors.py` takes a filesystem root. The new front ends take text, so they
    never need this; it exists so `extract.cli diff` can run both sides from the same
    bare mirror and compare.
    """
    import tempfile
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        with tempfile.TemporaryDirectory() as d:
            pr = subprocess.run(["git", "--git-dir", repo.gitdir, "archive",
                                 "--format=tar", repo.ref], capture_output=True)
            subprocess.run(["tar", "-x", "-C", d], input=pr.stdout, capture_output=True)
            yield Path(d)
    return _ctx()

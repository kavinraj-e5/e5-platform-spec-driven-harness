#!/usr/bin/env python3
"""Build-time dependency graph: who embeds which library, at which version.

    python3 tools/build_graph.py extract     # -> graph/build-facts.jsonl
    python3 tools/build_graph.py report      # human table
    python3 tools/build_graph.py check       # exit 1 on protocol version skew

WHY THIS EXISTS
---------------
The platform's most-depended-on code is invisible to every other extractor here, because
every other extractor reads RUNTIME surfaces - protos, GraphQL, Kafka topics, SQL. A
library has none of those. It has a coordinate and a version, and it is INLINED into a
deployable at build time.

So the question "which running things carry task-sdk 0.7.6?" had no representation at all.
The catalog recorded `pinned_by: platform-core/build.gradle` - ONE consumer, as a string -
for an artifact its own header said 10 of 22 repos depend on. Measured here: 8 consumers
across 4 different versions.

WHAT THIS IS NOT
----------------
Not a resolver. It reads DECLARED coordinates from build files; it does not resolve the
transitive graph, because that needs a network and a credential and would make the KB's
facts depend on JFrog being up. A declared pin is a fact about the repo, which is what is
wanted. Transitive closure is explicitly out of scope and `api` vs `implementation` is
recorded so a reader can tell which deps are re-exported.

READ-ONLY, FROM BARE MIRRORS, AT THE INDEXED BRANCH - the same contract as every other
extractor here. Never a working copy.
"""
from __future__ import annotations
import argparse, collections, json, os, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "release"))
import minyaml

KB = HERE.parent
OUT = KB / "graph" / "build-facts.jsonl"

BUILD_FILES = ("build.gradle", "build.gradle.kts", "pom.xml", "pyproject.toml",
               # settings.gradle is not a build file and is collected anyway: in Gradle
               # the published artifactId DEFAULTS to the project name, which is declared
               # only here. Without it the publisher side of every artifact was
               # `artifact: None` - hardcoded, not merely unparsed - so the join between
               # "who publishes e5-platform-core" and the nine repos that depend on it
               # could not close on a coordinate and had to fall back to the repo id.
               "settings.gradle", "settings.gradle.kts")

# An internal coordinate. Deliberately a prefix list and not "anything not on Maven
# Central": a guess about what is internal would silently drop a real dependency the day
# someone publishes under a new group.
INTERNAL = re.compile(r'^(e5[.\-_]|platform\.core$|com\.e5$|e5$)', re.I)

# gradle: implementation 'g:a:v'   /   implementation("g:a:v")
_G_COLON = re.compile(
    r"""(implementation|api|compileOnly|runtimeOnly|annotationProcessor|testImplementation)"""
    r"""\s*[\(\s]\s*['"]([\w.\-]+):([\w.\-]+):([\w.\-]+)['"]""")
# gradle: implementation group: 'g', name: 'a', version: 'v'
_G_MAP = re.compile(
    r"""(implementation|api|compileOnly|runtimeOnly|annotationProcessor|testImplementation)"""
    r"""\s+group:\s*['"]([\w.\-]+)['"]\s*,\s*name:\s*['"]([\w.\-]+)['"]\s*,"""
    r"""\s*version:\s*["']([^"']+)["']""")
_MVN = re.compile(
    r"<groupId>([\w.\-]+)</groupId>\s*<artifactId>([\w.\-]+)</artifactId>\s*"
    r"<version>([^<${}]+)</version>")
# pyproject package name and version, single or double quoted, [tool.poetry] or [project].
# Section-aware: a value like `authors = ["x"]` must not end the section early.
_PY_KV = re.compile(r"""^(name|version)\s*=\s*["']([^"']+)["']""", re.M)


class _M:
    def __init__(self, n, v): self._g = (None, n, v)
    def group(self, i): return self._g[i]


def _py_meta(body: str):
    for header in ("[tool.poetry]", "[project]"):
        lines, inside, sect = body.splitlines(), False, []
        for l in lines:
            s = l.strip()
            if s == header:
                inside = True; continue
            if inside and s.startswith("[") and not s.startswith("[" + header[1:-1] + "."):
                break
            if inside:
                sect.append(l)
        kv = dict(_PY_KV.findall("\n".join(sect)))
        if kv.get("name") and kv.get("version"):
            return _M(kv["name"], kv["version"])
    return None


_POETRY = re.compile(r"^(e5[\w_\-]*)\s*=\s*['\"]([^'\"]+)['\"]", re.M)
# gradle project coordinates
_GROUP = re.compile(r"^group\s*=?\s*['\"]([\w.\-]+)['\"]", re.M)
_VERSION = re.compile(r"^version\s*=?\s*['\"]([\w.\-]+)['\"]", re.M)
# The repo id is NOT a usable fallback: `platform-core` publishes `e5-platform-core`, and
# `wf-scaffolding` publishes `SampleWorkflow`. Guessing from the repo name would have
# produced a coordinate that matches nothing while looking resolved.
_ROOTNAME = re.compile(r"^\s*rootProject\.name\s*=\s*['\"]([\w.\-]+)['\"]", re.M)
_ARTIFACT_ID = re.compile(r"artifactId\s*=?\s*['\"]([\w.\-]+)['\"]")


def _registry() -> list[dict]:
    return minyaml.load((KB / "registry.yaml").read_text())["repos"]


def _git(gitdir: str, *args: str) -> str:
    p = subprocess.run(["git", "--git-dir", gitdir, *args],
                       capture_output=True, text=True, timeout=120)
    return p.stdout if p.returncode == 0 else ""


def _build_files(gitdir: str, branch: str) -> dict[str, str]:
    """Every build file at or near the root. Depth-capped: a monorepo's hundredth
    sub-module pom is noise, and devops-pipeline alone has ten."""
    listing = _git(gitdir, "ls-tree", "-r", "--name-only", branch)
    out = {}
    for f in listing.splitlines():
        if os.path.basename(f) in BUILD_FILES and f.count("/") <= 2:
            body = _git(gitdir, "show", f"{branch}:{f}")
            if body:
                out[f] = body
    return out


def _deps(path: str, body: str) -> list[tuple]:
    """(group, artifact, version, configuration) for internal coordinates only."""
    found = []
    base = os.path.basename(path)
    if base.startswith("build.gradle"):
        for rx in (_G_COLON, _G_MAP):
            for m in rx.finditer(body):
                found.append((m.group(2), m.group(3), m.group(4), m.group(1)))
    elif base == "pom.xml":
        for m in _MVN.finditer(body):
            found.append((m.group(1), m.group(2), m.group(3), "maven"))
    elif base == "pyproject.toml":
        for m in _POETRY.finditer(body):
            found.append(("pypi", m.group(1), m.group(2), "poetry"))
    return [d for d in found if INTERNAL.match(d[0]) or d[0] == "pypi"]


_ARG = re.compile(r'^\s*ARG\s+(\w+)\s*=\s*(\S+)', re.M)
_FROM = re.compile(r'^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)', re.M)


def _image_bases(rid: str, gitdir: str, branch: str, commit, dockerfiles: list) -> list[dict]:
    """`FROM <image>:<tag>` per Dockerfile, with ARG defaults substituted.

    The registry host (`${ECR_REGISTRY}/...`) is deploy-time configuration and is dropped;
    the image NAME and TAG are code. An unresolved `$ARG` with no default is recorded as
    unresolved rather than guessed."""
    out = []
    for path in dockerfiles:
        out += image_bases_from_text(rid, path, _git(gitdir, "show", f"{branch}:{path}"),
                                     branch, commit)
    return out


def image_bases_from_text(rid: str, path: str, body: str, branch: str, commit) -> list[dict]:
    out = []
    if True:
        args = dict(_ARG.findall(body))
        for m in _FROM.finditer(body):
            ref = m.group(1)
            for _ in range(3):                      # nested ARG defaults
                ref = re.sub(r'\$\{?(\w+)\}?', lambda x: args.get(x.group(1), x.group(0)), ref)
            unresolved = "$" in ref
            ref = ref.split("/")[-1] if not unresolved else ref
            name, _, tag = ref.partition(":")
            if name.lower() in ("scratch",) or name.startswith("builder"):
                continue
            out.append({"kind": "BuildsFromImage", "repo": rid, "path": path,
                        "image": name, "tag": tag or None, "unresolved": unresolved,
                        "line": body[:m.start()].count("\n") + 1,
                        "branch": branch, "commit": commit,
                        "provenance": "dockerfile", "confidence": "EXTRACTED"})
    return out


def extract() -> list[dict]:
    facts: list[dict] = []
    for r in _registry():
        gitdir = os.path.expanduser(r["mirror"])
        if not os.path.isdir(gitdir):
            print(f"  skip {r['id']}: no mirror at {gitdir}", file=sys.stderr)
            continue
        branch, rid = r["branch"], r["id"]
        files = _build_files(gitdir, branch)
        listing = _git(gitdir, "ls-tree", "-r", "--name-only", branch).splitlines()
        dockerfiles = [f for f in listing
                       if os.path.basename(f).startswith("Dockerfile") and f.count("/") <= 2]
        deployable = bool(dockerfiles)
        facts += _image_bases(rid, gitdir, branch, r.get("commit"), dockerfiles)
        # Say WHAT was read, so "no internal dependency" and "nothing was read" differ.
        # Without this, a repo with a pom.xml and no e5 dependency looked exactly like a
        # repo whose build files the extractor cannot parse (2026-11-14).
        n_internal = sum(len(_deps(p, b)) for p, b in files.items())
        facts.append({"kind": "BuildFileScanned", "repo": rid,
                      "files": sorted(files) + sorted(dockerfiles),
                      "internal_dependencies": n_internal,
                      "parsed": bool(files) or bool(dockerfiles),
                      "branch": branch, "commit": r.get("commit"),
                      "provenance": "build-file", "confidence": "EXTRACTED"})
        if not files:
            continue
        # One artifact id per repo, from settings.gradle, shared by its build files.
        root_name = None
        for path, body in files.items():
            if os.path.basename(path).startswith("settings.gradle"):
                m = _ROOTNAME.search(body)
                if m and path.count("/") == 0:      # the ROOT settings file, not a module's
                    root_name = m.group(1)
        for path, body in sorted(files.items()):
            if os.path.basename(path).startswith("settings.gradle"):
                continue
            if os.path.basename(path) == "pyproject.toml" and path.count("/") == 0:
                pm = _py_meta(body)
                if pm:
                    facts.append({"kind": "Publishes", "repo": rid, "path": path, "group": "pypi",
                                  "artifact": pm.group(1), "version": pm.group(2),
                                  "branch": branch, "commit": r.get("commit"),
                                  "provenance": "build-file", "confidence": "EXTRACTED"})
            g, v = _GROUP.search(body), _VERSION.search(body)
            if g and v and path.startswith("build.gradle"):
                facts.append({
                    "kind": "Publishes", "repo": rid, "path": path,
                    "group": g.group(1),
                    "artifact": (_ARTIFACT_ID.search(body) or [None, root_name])[1]
                    if _ARTIFACT_ID.search(body) else root_name,
                    "version": v.group(1),
                    "branch": branch, "commit": r.get("commit"),
                    "provenance": "build-file", "confidence": "EXTRACTED",
                })
            for grp, art, ver, cfg in _deps(path, body):
                facts.append({
                    "kind": "DependsOnArtifact", "repo": rid, "path": path,
                    "group": grp, "artifact": art, "version": ver,
                    "configuration": cfg,
                    # `api` is the one that matters: it RE-EXPORTS the dependency to
                    # everything downstream, which is how every variant inherits
                    # task-sdk 0.7.6 without naming it.
                    "transitive": cfg == "api",
                    "deployable_repo": deployable,
                    "branch": branch, "commit": r.get("commit"),
                    "provenance": "build-file", "confidence": "EXTRACTED",
                })
    facts.sort(key=lambda f: (f["kind"], f["repo"], f.get("artifact") or "",
                              f.get("path") or ""))
    return facts


def _consumers(facts: list[dict]) -> dict:
    by = collections.defaultdict(list)
    for f in facts:
        if f["kind"] == "DependsOnArtifact":
            by[f["artifact"]].append(f)
    return by


def report(facts: list[dict]) -> None:
    by = _consumers(facts)
    print(f"{len(facts)} build facts from {len({f['repo'] for f in facts})} repos\n")
    print("INTERNAL ARTIFACTS, by number of consuming repos")
    for art, fs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        vers = sorted({f["version"] for f in fs})
        flag = "  <-- VERSION SKEW" if len(vers) > 1 else ""
        print(f"\n  {art}   {len(fs)} consumers   versions={vers}{flag}")
        for f in sorted(fs, key=lambda f: f["repo"]):
            star = " (api, re-exported)" if f["transitive"] else ""
            print(f"      {f['repo']:24} {f['version']:10} {f['configuration']}{star}")


def check(facts: list[dict]) -> int:
    """Version skew on a SHARED CONTRACT is an error; skew on anything else is noise.

    The task SDK is the wire format between every plane. Two deployables on different
    majors of it are not a tidiness problem, they are a protocol mismatch that no source
    gate and no compile gate can see, because each repo compiles perfectly against the
    version it pins."""
    by = _consumers(facts)
    rc = 0
    for art, fs in sorted(by.items()):
        vers = sorted({f["version"] for f in fs})
        if len(vers) < 2:
            continue
        shared = "task-sdk" in art or "task_sdk" in art
        lvl = "ERROR" if shared else "warn "
        if shared:
            rc = 1
        print(f"{lvl}  {art}: {len(vers)} versions in use {vers}")
        for f in sorted(fs, key=lambda f: (f["version"], f["repo"])):
            print(f"         {f['version']:10} {f['repo']}")
    if rc == 0:
        print("no skew on a shared contract")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["extract", "report", "check"])
    a = ap.parse_args()
    facts = extract()
    if a.cmd == "extract":
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("".join(json.dumps(f, sort_keys=True) + "\n" for f in facts))
        print(f"{len(facts)} facts -> {OUT.relative_to(KB)}")
        return 0
    if a.cmd == "report":
        report(facts)
        return 0
    return check(facts)


if __name__ == "__main__":
    sys.exit(main())

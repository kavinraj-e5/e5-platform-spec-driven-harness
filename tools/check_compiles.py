#!/usr/bin/env python3
"""COMPILE GATE - does the branch the KB indexes actually build?

Every other gate in this harness reads source. Source that parses can still fail to
typecheck, and that is exactly what happened: `deployment-mgmt release/v1.8.3` had been
red since 2026-09-30 and no gate noticed, because every gate that looked at it was
reading text. See disagreements/dms-latest-release-does-not-compile.md.

Two decisions worth stating, both learned from that incident:

  1. **Test sources count.** The original card recorded "no other call site is affected -
     checked", because `compileJava` was the only thing run. `compileTestJava` found a
     second stale call site in the test tree. A gate that compiles main only would have
     reproduced the same blind spot, so this one compiles both.

  2. **Not-applicable is declared, not inferred.** Applicability is keyed on the
     registry's own `language` / `build` fields, the same way tools/profiles.py keys
     extraction on a declared profile. A repo this gate skips must be skipped for a
     reason that is written down and checkable - otherwise a new JVM repo joins the
     estate and is silently never compiled.

Toolchain resolution is deliberately boring: use what the repo ships, fall back to what
the host has, and only reach for Docker when the host JDK cannot satisfy the language
level the build asks for. Three repos in this estate declare Java 21 and one resolves a
private dependency from JFrog - both are configuration facts, not code faults, and the
gate must not report them as compile failures.

A caution about reading this gate's output, learned the expensive way: "needs java 21"
appears in the `unbuildable` detail whether or not the JDK is the problem. For weeks it
read as "three repos need JDK 21 while the host runs 17" when JDK 21 was installed in
~/.jdks and detected - the actual blocker was `NO_MAVEN` in the same line, because apt
will not install Maven without root and `shutil.which` only looks on PATH. The declared
language level and the missing tool are two findings; print both, and believe the
specific one.

Exit codes follow the house convention used by check_staleness.py:
    0  every applicable repo compiled
    1  at least one repo FAILED TO COMPILE          <- the gate's actual purpose
    2  at least one repo could not be built at all  <- setup gap, advisory
"""
from __future__ import annotations
import argparse, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workspace as ws

KB = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("E5_CACHE", Path.home() / ".e5" / "cache"))

# Which (language, build) pairs this gate claims to handle. Anything else is reported as
# not-applicable WITH ITS REASON, so the skip list is auditable rather than silent.
APPLICABLE = {("java", "gradle"), ("java", "maven")}

NOT_APPLICABLE_REASON = {
    ("python", "poetry"):     "interpreted - no compile step; a syntax gate would be a different tool",
    ("python", "-"):          "interpreted - no compile step",
    ("csharp", "msbuild"):    "Windows/.NET toolchain, not present on the index host",
    ("dockerfile", "docker"): "an image build, not a compile; needs a registry and a base image pull",
    ("yaml", "-"):            "configuration only - nothing to compile",
}

# Host JDKs, keyed on feature version.
#
# /usr/lib/jvm is NOT the whole story and assuming it was sent this gate to Docker for a
# repo that builds fine locally: the JDK 21 that satisfies deployment-mgmt's toolchain
# lives in ~/.jdks, installed by IntelliJ. Gradle's own auto-detection finds it; a scan
# that only knows about the system path does not.
_JDK_DIRS = (Path("/usr/lib/jvm"), Path("/Library/Java/JavaVirtualMachines"),
             Path.home() / ".jdks", Path.home() / ".sdkman" / "candidates" / "java")


def _host_jdks() -> dict[int, Path]:
    out: dict[int, Path] = {}
    for base in _JDK_DIRS:
        if not base.is_dir():
            continue
        for d in base.iterdir():
            m = re.search(r"(?:^|[^0-9])(\d+)(?:[.-]|$)", d.name)
            javac = d / "bin" / "javac"
            if m and javac.exists():
                v = int(m.group(1))
                # java-1.17.0-... and java-17-... are the same JDK; skip the 1.x spelling
                if v == 1:
                    continue
                out.setdefault(v, d)
    return out


# Maven, keyed the same way and for the same reason. `shutil.which("mvn")` alone reported
# NO_MAVEN on a host that has one: apt will not install Maven without root, so it lives in
# ~/.e5/tools (or sdkman, or /opt). That read as "three repos need JDK 21 and the host runs
# 17" for weeks - the JDK was never the blocker, the missing build tool was.
_MVN_DIRS = (Path.home() / ".e5" / "tools" / "maven",
             Path.home() / ".sdkman" / "candidates" / "maven" / "current",
             Path("/opt/maven"), Path("/usr/share/maven"))


def _host_maven() -> str | None:
    if os.environ.get("E5_MVN"):
        return os.environ["E5_MVN"]
    found = shutil.which("mvn")
    if found:
        return found
    for base in _MVN_DIRS:
        exe = base / "bin" / "mvn"
        if exe.exists() and os.access(exe, os.X_OK):
            return str(exe)
    return None


def _required_java(tree: Path, build: str) -> int | None:
    """The language level the build asks for, or None if it never says.

    Not a full build-script evaluation - just the declarations that actually appear in
    this estate. A miss returns None and the host default is used, which is the right
    failure mode: the compiler itself then reports the mismatch.
    """
    texts = []
    if build == "maven":
        p = tree / "pom.xml"
        if p.exists():
            texts.append(p.read_text(errors="replace"))
        pats = [r"<java\.version>\s*(\d+)", r"<maven\.compiler\.(?:release|source|target)>\s*(\d+)",
                r"<release>\s*(\d+)\s*</release>"]
    else:
        for name in ("build.gradle", "build.gradle.kts"):
            p = tree / name
            if p.exists():
                texts.append(p.read_text(errors="replace"))
        pats = [r"JavaLanguageVersion\.of\(\s*(\d+)", r"JavaVersion\.VERSION_(\d+)",
                r"(?:source|target)Compatibility\s*=?\s*['\"]?(\d+)"]
    found = []
    for t in texts:
        for pat in pats:
            found += [int(m) for m in re.findall(pat, t)]
    return max(found) if found else None


def _docker_image(build: str, java: int) -> str:
    return f"maven:3.9-eclipse-temurin-{java}" if build == "maven" else f"eclipse-temurin:{java}-jdk"


def _jfrog_env() -> dict:
    """JFrog credentials as env, presence-only logging. Never written to a file.

    audit-service resolves e5-notification-hub-java-sdk from element5.jfrog.io; without
    this it fails with a 401 that looks like a build break and is not one.
    """
    try:
        import credentials
        got = credentials.jfrog_creds()
    except Exception:
        return {}
    if not got:
        return {}
    u, t = got
    return {"JFROG_USER": u, "JFROG_TOKEN": t}


# settings.xml holds PLACEHOLDERS only - the secret travels in the environment and is
# never persisted, which is the same rule tools/credentials.py enforces everywhere else.
_MVN_SETTINGS = """<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">
  <servers>
    <server>
      <id>artifactory-resolve</id>
      <username>${env.JFROG_USER}</username>
      <password>${env.JFROG_TOKEN}</password>
    </server>
  </servers>
</settings>
"""


def _export(repo: str, branch: str, dest: Path) -> str | None:
    """git archive from the BARE MIRROR. Never a working copy - house rule, and it also
    guarantees we compile exactly the indexed commit rather than whatever is checked out."""
    bare = CACHE / f"{repo}.git"
    if not bare.is_dir():
        return f"no bare mirror at {bare}"
    dest.mkdir(parents=True, exist_ok=True)
    p1 = subprocess.Popen(["git", "-C", str(bare), "archive", branch], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    assert p1.stdout is not None and p1.stderr is not None
    p2 = subprocess.Popen(["tar", "-x", "-C", str(dest)], stdin=p1.stdout)
    p1.stdout.close()
    p2.communicate()
    err = p1.stderr.read().decode(errors="replace").strip()
    if p1.wait() != 0 or p2.returncode != 0:
        return f"git archive {branch} failed: {err[:200]}"
    if not any(dest.iterdir()):
        return f"git archive {branch} produced nothing"
    return None



def _cached_gradle(tree: Path) -> str | None:
    """The gradle the wrapper WOULD have downloaded, if it is already in the dist cache."""
    props = tree / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if not props.exists():
        return None
    m = re.search(r"gradle-([\d.]+)-(?:bin|all)\.zip", props.read_text(errors="replace"))
    if not m:
        return None
    base = Path(os.environ.get("GRADLE_USER_HOME", Path.home() / ".gradle")) / "wrapper" / "dists"
    for kind in ("bin", "all"):
        for hashed in sorted(base.glob(f"gradle-{m.group(1)}-{kind}/*")):
            exe = hashed / f"gradle-{m.group(1)}" / "bin" / "gradle"
            if exe.exists():
                return str(exe)
    return None


def _cmd(tree: Path, build: str, java: int | None, jdks: dict, m2: Path,
         settings: Path, goals: tuple[str, str] | None = None) -> tuple[list[str], dict, str]:
    """(argv, extra_env, how) for compiling main + test sources.

    `goals` overrides the (maven, gradle) task pair. The gate itself always compiles both
    source sets - see the docstring - but tools/scip_pilot.py needs main classes only, and
    MUST reuse this resolver rather than grow a second one. The first version of that
    pilot hardcoded `./gradlew` and so reported every Maven repo as having no call graph.
    """
    mvn_goal, gradle_goal = goals or ("test-compile", "compileJava compileTestJava")
    env = {}
    # Only Maven needs this. `maven.compiler.release` is honoured by the JDK Maven is
    # running on, so a host JDK below the declared level is a hard stop; Gradle resolves
    # its own toolchain and is handled below.
    use_docker = build == "maven" and java is not None and java not in jdks

    if build == "maven":
        assert not use_docker or java is not None  # narrows `java` for _docker_image
        if java and java in jdks:
            env["JAVA_HOME"] = str(jdks[java])
        goals = ["-B", *mvn_goal.split(), "-Dmaven.repo.local=/m2" if use_docker else f"-Dmaven.repo.local={m2}"]
        if use_docker:
            argv = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                    "-e", "JFROG_USER", "-e", "JFROG_TOKEN",
                    "-v", f"{tree}:/src", "-v", f"{m2}:/m2", "-v", f"{settings}:/settings.xml:ro",
                    "-w", "/src", _docker_image(build, java), "mvn", "-s", "/settings.xml"] + goals
            return argv, env, f"docker {_docker_image(build, java)}"
        mvn = _host_maven()
        if not mvn:
            return [], env, "NO_MAVEN"
        return [mvn, "-s", str(settings)] + goals, env, f"host mvn (java {java or 'default'})"

    # ---- gradle
    #
    # Gradle is NOT given the Docker treatment, and that is deliberate. It has
    # first-class toolchain support: `java { toolchain { languageVersion } }` is resolved
    # by Gradle itself, with auto-detection and auto-download enabled by default. Forcing
    # a JAVA_HOME or a container here overrides a mechanism that already works and breaks
    # builds that were green - which is exactly what the first version of this gate did to
    # deployment-mgmt. Let the wrapper decide; if it cannot satisfy the toolchain it says
    # so, and that surfaces as `unbuildable` rather than a false compile failure.
    tasks = [*gradle_goal.split(), "--console=plain", "-q"]
    wrapper_jar = tree / "gradle" / "wrapper" / "gradle-wrapper.jar"
    gradlew = tree / "gradlew"
    if gradlew.exists() and wrapper_jar.exists():
        gradlew.chmod(0o755)
        return [str(gradlew)] + tasks, env, f"./gradlew (java {java or 'default'})"
    # workflow-manager does not commit gradle-wrapper.jar; the wrapper is not the build.
    # Its gradle-wrapper.properties still names the distribution it wants, and that is a
    # better answer than whatever `gradle` happens to be on PATH - this host has 4.4.1,
    # which cannot run a build written for 8.8. Prefer the named version if it is cached.
    g = _cached_gradle(tree) or os.environ.get("E5_GRADLE") or shutil.which("gradle")
    if not g:
        return [], env, "NO_GRADLE_WRAPPER_JAR_AND_NO_GRADLE"
    return [g] + tasks, env, f"gradle {g.split('/')[-3] if 'dists' in g else 'on PATH'} - wrapper jar absent"


_ERR = re.compile(r"(?:^|\s)(?:\[ERROR\].*\.java:\[|.*\.java:\d+: error:)")


def check_repo(repo: str, meta: dict, keep: bool = False) -> dict:
    lang, build = meta.get("language", "-"), meta.get("build", "-")
    branch = meta.get("branch", "")
    if (lang, build) not in APPLICABLE:
        reason = NOT_APPLICABLE_REASON.get((lang, build))
        return {"repo": repo, "status": "n/a", "detail": reason or
                f"UNDECLARED: language={lang} build={build} is neither compilable nor "
                f"listed in NOT_APPLICABLE_REASON - add it deliberately",
                "undeclared": reason is None}

    work = Path(tempfile.mkdtemp(prefix=f"e5-compile-{repo}-"))
    tree = work / "src"
    m2 = Path(os.environ.get("E5_M2", Path.home() / ".e5" / "m2"))
    m2.mkdir(parents=True, exist_ok=True)
    settings = work / "settings.xml"
    settings.write_text(_MVN_SETTINGS)
    try:
        err = _export(repo, branch, tree)
        if err:
            return {"repo": repo, "status": "unbuildable", "detail": err}

        java = _required_java(tree, build)
        argv, extra, how = _cmd(tree, build, java, _host_jdks(), m2, settings)
        if not argv:
            return {"repo": repo, "status": "unbuildable",
                    "detail": f"no toolchain: {how} (needs java {java or '?'})"}

        env = {**os.environ, **extra, **_jfrog_env()}
        r = subprocess.run(argv, cwd=tree, env=env, capture_output=True, text=True,
                           timeout=int(os.environ.get("E5_COMPILE_TIMEOUT", "900")),
                           stdin=subprocess.DEVNULL)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            return {"repo": repo, "status": "ok", "detail": how}
        errs = [l.strip() for l in out.splitlines() if _ERR.search(l)]
        if errs:
            return {"repo": repo, "status": "FAILED", "detail": how,
                    "errors": errs[:5], "n_errors": len(errs)}
        tail = [l for l in out.splitlines() if l.startswith("[ERROR]") or "error:" in l
                or "What went wrong" in l]
        return {"repo": repo, "status": "unbuildable",
                "detail": f"{how}: {' '.join(tail[:3])[:300] or out.strip()[-300:]}"}
    except subprocess.TimeoutExpired:
        return {"repo": repo, "status": "unbuildable", "detail": "timed out"}
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="one repo id; default is every registered repo")
    ap.add_argument("--keep", action="store_true", help="leave the export for inspection")
    a = ap.parse_args()

    _, repos = ws.registry()
    if a.repo:
        if a.repo not in repos:
            print(f"no repo {a.repo!r} in registry. Known: {', '.join(sorted(repos))}")
            return 2
        repos = {a.repo: repos[a.repo]}

    # Streamed, not collected-then-printed. A full-estate pass takes minutes and a run
    # that shows nothing until the end is indistinguishable from a hung one.
    w = max(len(r) for r in repos)
    results = []
    for repo, meta in repos.items():
        r = check_repo(repo, meta, keep=a.keep)
        results.append(r)
        mark = {"ok": "ok      ", "FAILED": "FAILED  ", "n/a": "n/a     ",
                "unbuildable": "no-build"}[r["status"]]
        print(f"{r['repo'].ljust(w)}  {mark}  {r['detail']}", flush=True)
        for e in r.get("errors", []):
            print(f"{' ' * w}            {e}", flush=True)

    failed = [r for r in results if r["status"] == "FAILED"]
    unbuildable = [r for r in results if r["status"] == "unbuildable"]
    undeclared = [r for r in results if r.get("undeclared")]
    ok = [r for r in results if r["status"] == "ok"]

    print(f"\n{len(ok)} compiled, {len(failed)} FAILED, {len(unbuildable)} could not be built, "
          f"{len([r for r in results if r['status'] == 'n/a'])} not applicable")

    if undeclared:
        print("\nUNDECLARED (language/build pair this gate neither compiles nor excuses):")
        for r in undeclared:
            print(f"  {r['repo']}: {r['detail']}")
        return 2
    if failed:
        print("\nThe KB indexes a tree that does not compile. Anyone branching from it "
              "inherits a broken build before writing a line.")
        return 1
    return 2 if unbuildable else 0


if __name__ == "__main__":
    sys.exit(main())

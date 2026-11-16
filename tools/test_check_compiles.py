"""Tests for the compile gate.

None of these compile anything. Running real builds in the unit suite would make it
take minutes and depend on a JDK, a network and JFrog; the gate's own behaviour is
exercised against the estate by running it, and what is pinned here is the logic that
decides WHAT to build and HOW - which is where its two real bugs were.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_compiles as cc
import workspace as ws


# --------------------------------------------------------------- applicability
def test_every_registry_language_build_pair_is_declared():
    """The drift guard, and the reason this gate keys on the registry at all.

    A new repo joining the estate with an unfamiliar toolchain must make this fail.
    Silence is the failure mode profiles.py was written to prevent, and a compile gate
    that quietly skips the one repo nobody can build is worse than no gate.
    """
    _, repos = ws.registry()
    undeclared = sorted({
        (m.get("language", "-"), m.get("build", "-"))
        for m in repos.values()
        if (m.get("language", "-"), m.get("build", "-")) not in cc.APPLICABLE
        and (m.get("language", "-"), m.get("build", "-")) not in cc.NOT_APPLICABLE_REASON
    })
    assert not undeclared, (
        f"language/build pairs this gate neither compiles nor excuses: {undeclared}. "
        f"Add to APPLICABLE or to NOT_APPLICABLE_REASON with a stated reason - do not "
        f"let a repo be skipped by accident.")


def test_undeclared_pair_is_reported_not_skipped():
    r = cc.check_repo("made-up", {"language": "rust", "build": "cargo", "branch": "x"})
    assert r["status"] == "n/a"
    assert r["undeclared"] is True
    assert "UNDECLARED" in r["detail"]


def test_declared_not_applicable_is_not_flagged():
    r = cc.check_repo("workflow-ops", {"language": "python", "build": "poetry", "branch": "x"})
    assert r["status"] == "n/a"
    assert not r.get("undeclared")
    assert "compile" in r["detail"]


def test_every_java_repo_in_the_registry_is_applicable():
    """17 Java repos; the gate must claim all of them, not just the deployable services."""
    _, repos = ws.registry()
    java = {k: v for k, v in repos.items() if v.get("language") == "java"}
    assert len(java) >= 17
    for k, v in java.items():
        assert (v.get("language"), v.get("build")) in cc.APPLICABLE, f"{k} not covered"


# --------------------------------------------------------------- java level
@pytest.mark.parametrize("build,text,expect", [
    ("gradle", "java {\n toolchain {\n  languageVersion = JavaLanguageVersion.of(21)\n }\n}", 21),
    ("gradle", "sourceCompatibility = '17'\ntargetCompatibility = '17'", 17),
    ("gradle", "sourceCompatibility = JavaVersion.VERSION_11", 11),
    ("gradle", "plugins { id 'java' }", None),
    ("maven", "<properties><java.version>21</java.version></properties>", 21),
    ("maven", "<maven.compiler.release>17</maven.compiler.release>", 17),
    ("maven", "<project></project>", None),
])
def test_required_java_reads_the_declaration(tmp_path, build, text, expect):
    (tmp_path / ("pom.xml" if build == "maven" else "build.gradle")).write_text(text)
    assert cc._required_java(tmp_path, build) == expect


def test_required_java_takes_the_highest_when_several_are_declared():
    """A mixed declaration must not silently pick the lenient one."""
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "pom.xml").write_text(
        "<java.version>17</java.version><maven.compiler.release>21</maven.compiler.release>")
    assert cc._required_java(d, "maven") == 21


# --------------------------------------------------------------- toolchain choice
def test_gradle_is_never_sent_to_docker(tmp_path):
    """Regression. The first version of this gate forced a container whenever the host
    had no matching JDK, which broke deployment-mgmt - a repo that builds fine, because
    Gradle resolves `JavaLanguageVersion.of(21)` itself. Gradle owns its toolchain."""
    (tmp_path / "gradlew").write_text("#!/bin/sh\n")
    (tmp_path / "gradle" / "wrapper").mkdir(parents=True)
    (tmp_path / "gradle" / "wrapper" / "gradle-wrapper.jar").write_bytes(b"x")
    argv, _, how = cc._cmd(tmp_path, "gradle", java=21, jdks={}, m2=tmp_path,
                           settings=tmp_path / "s.xml")
    assert argv and argv[0] != "docker", f"gradle was containerised: {how}"
    assert "gradlew" in argv[0]


def test_gradle_falls_back_to_host_gradle_when_the_wrapper_jar_is_absent(tmp_path, monkeypatch):
    """workflow-manager does not commit gradle-wrapper.jar. That is not a build failure."""
    (tmp_path / "gradlew").write_text("#!/bin/sh\n")
    monkeypatch.setattr(cc.shutil, "which", lambda n: "/usr/bin/gradle" if n == "gradle" else None)
    monkeypatch.delenv("E5_GRADLE", raising=False)
    argv, _, how = cc._cmd(tmp_path, "gradle", java=None, jdks={}, m2=tmp_path,
                           settings=tmp_path / "s.xml")
    assert argv[0] == "/usr/bin/gradle"
    assert "wrapper jar absent" in how


def test_maven_uses_docker_only_when_the_host_jdk_is_too_old(tmp_path, monkeypatch):
    """Maven honours <release> on the JDK it runs under, so here the version does bind."""
    monkeypatch.setenv("E5_MVN", "/opt/mvn")   # this host has no mvn; pin one
    have_21 = {21: Path("/opt/jdk21")}
    argv, env, how = cc._cmd(tmp_path, "maven", java=21, jdks=have_21, m2=tmp_path,
                             settings=tmp_path / "s.xml")
    assert argv[0] != "docker" and env.get("JAVA_HOME") == "/opt/jdk21"

    argv, _, how = cc._cmd(tmp_path, "maven", java=21, jdks={17: Path("/opt/jdk17")},
                           m2=tmp_path, settings=tmp_path / "s.xml")
    assert argv[0] == "docker" and "temurin-21" in how


def test_both_main_and_test_sources_are_compiled(tmp_path, monkeypatch):
    """The whole point. compileJava alone reproduced the blind spot in the DMS card."""
    monkeypatch.setenv("E5_MVN", "/opt/mvn")
    (tmp_path / "gradlew").write_text("#!/bin/sh\n")
    (tmp_path / "gradle" / "wrapper").mkdir(parents=True)
    (tmp_path / "gradle" / "wrapper" / "gradle-wrapper.jar").write_bytes(b"x")
    argv, _, _ = cc._cmd(tmp_path, "gradle", None, {}, tmp_path, tmp_path / "s.xml")
    assert "compileJava" in argv and "compileTestJava" in argv

    argv, _, _ = cc._cmd(tmp_path, "maven", None, {}, tmp_path, tmp_path / "s.xml")
    assert "test-compile" in argv, "mvn compile skips the test tree; test-compile does not"


def test_host_jdk_scan_covers_more_than_usr_lib_jvm():
    """Regression. Scanning only /usr/lib/jvm missed the JDK 21 in ~/.jdks and sent a
    green repo to a container that then could not fetch its Gradle distribution."""
    names = {str(p) for p in cc._JDK_DIRS}
    assert any(".jdks" in n for n in names)
    assert any("sdkman" in n for n in names)
    assert any("/usr/lib/jvm" == n for n in names)


# --------------------------------------------------------------- credentials
def test_settings_template_holds_no_literal_secret():
    assert "${env.JFROG_USER}" in cc._MVN_SETTINGS
    assert "${env.JFROG_TOKEN}" in cc._MVN_SETTINGS
    for bad in ("password>AK", "token>", "cmVmdG", "glpat-", "ghp_"):
        assert bad not in cc._MVN_SETTINGS.replace("${env.JFROG_TOKEN}", "")


def test_credentials_travel_in_the_environment_only():
    """The secret must never be written into the settings file the build reads."""
    src = Path(cc.__file__).read_text()
    assert "jfrog_creds()" in src
    # the only place a credential value is placed is a dict handed to subprocess env
    assert 'settings.write_text(_MVN_SETTINGS)' in src
    assert 'settings.write_text(f"' not in src, "settings.xml must not be f-string formatted"


# --------------------------------------------------------------- error detection
@pytest.mark.parametrize("line", [
    "/src/com/X.java:230: error: incompatible types: Status cannot be converted to String",
    "[ERROR] /src/com/X.java:[97,30] incompatible types",
])
def test_compiler_errors_are_recognised_in_both_toolchain_formats(line):
    assert cc._ERR.search(line), f"not matched: {line}"


@pytest.mark.parametrize("line", [
    "[ERROR] Failed to execute goal ... release version 21 not supported",
    "warning: Not generating equals and hashCode",
    "> Could not resolve all files for configuration ':compileClasspath'.",
])
def test_non_compile_failures_are_not_mistaken_for_compile_errors(line):
    """A missing JDK or an unreachable repository is a setup gap, not a broken branch.
    Reporting it as FAILED would block a build over something the platform did not do."""
    assert not cc._ERR.search(line), f"wrongly matched: {line}"


# --------------------------------------------------------------- exit codes
def _main_with(monkeypatch, results):
    monkeypatch.setattr(sys, "argv", ["check_compiles.py"])
    monkeypatch.setattr(ws, "registry", lambda: ("/tmp", {r["repo"]: {} for r in results}))
    it = iter(results)
    monkeypatch.setattr(cc, "check_repo", lambda _r, _m, keep=False: next(it))
    return cc.main()


def test_exit_1_only_on_a_real_compile_failure(monkeypatch):
    assert _main_with(monkeypatch, [
        {"repo": "a", "status": "ok", "detail": "-"},
        {"repo": "b", "status": "FAILED", "detail": "-", "errors": ["X.java:1: error: x"]},
    ]) == 1


def test_exit_2_when_something_could_not_be_built(monkeypatch):
    assert _main_with(monkeypatch, [
        {"repo": "a", "status": "ok", "detail": "-"},
        {"repo": "b", "status": "unbuildable", "detail": "no toolchain"},
    ]) == 2


def test_exit_0_when_everything_applicable_compiled(monkeypatch):
    assert _main_with(monkeypatch, [
        {"repo": "a", "status": "ok", "detail": "-"},
        {"repo": "b", "status": "n/a", "detail": "interpreted", "undeclared": False},
    ]) == 0


def test_an_undeclared_pair_outranks_a_clean_run(monkeypatch):
    """Exit 2 even when nothing failed: an unclassified repo means the gate does not
    know what it is not checking."""
    assert _main_with(monkeypatch, [
        {"repo": "a", "status": "ok", "detail": "-"},
        {"repo": "b", "status": "n/a", "detail": "UNDECLARED: ...", "undeclared": True},
    ]) == 2


# --------------------------------------------------------------- CI wiring
def test_ci_stage_calls_the_gate_and_splits_the_exit_codes():
    g = (Path(__file__).resolve().parent.parent / "ci" / "jenkins-kb-index.groovy").read_text()
    assert "tools/check_compiles.py --repo ${repoId}" in g
    assert "compileRc == 1" in g and "error(" in g
    assert "compileRc == 2" in g and "unstable(" in g


def test_cached_gradle_distribution_beats_whatever_is_on_path(tmp_path, monkeypatch):
    """workflow-manager omits gradle-wrapper.jar but its properties still name 8.8.
    Falling through to the host's `gradle` picked up 4.4.1, which cannot run that build
    and reported a green repo as unbuildable."""
    wrap = tmp_path / "tree" / "gradle" / "wrapper"
    wrap.mkdir(parents=True)
    (wrap / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.8-bin.zip\n")
    gh = tmp_path / "gradlehome"
    exe = gh / "wrapper" / "dists" / "gradle-8.8-bin" / "abc123" / "gradle-8.8" / "bin" / "gradle"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setenv("GRADLE_USER_HOME", str(gh))
    assert cc._cached_gradle(tmp_path / "tree") == str(exe)


def test_cached_gradle_is_absent_when_nothing_names_a_distribution(tmp_path):
    assert cc._cached_gradle(tmp_path) is None

"""Tests for the build-time dependency extractor.

The parser tests use literal build-file fragments rather than the mirrors, so they fail
on a parser regression rather than on a repo that moved. The estate tests use the real
facts, because the whole point of the tool is a claim about the real estate.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "release"))
import build_graph as B               # noqa: E402
import catalog as C                   # noqa: E402

KB = HERE.parent


# --------------------------------------------------------------------- parsing
def test_both_gradle_dependency_spellings_are_parsed():
    """The estate uses both, in the same repo. A parser that handled only the colon form
    reported `SampleWorkflow` as having ZERO consumers while variant-scaffolding was
    sitting on it - a false negative that read exactly like a real finding."""
    colon = "    implementation ( 'e5.task.sdk.java:e5-task-sdk-java:0.7.4') {"
    mapform = ("    implementation group: 'platform.core', name: 'e5-platform-core', "
               "version: \"1.9.12\"")
    assert B._deps("build.gradle", colon) == [
        ("e5.task.sdk.java", "e5-task-sdk-java", "0.7.4", "implementation")]
    assert B._deps("build.gradle", mapform) == [
        ("platform.core", "e5-platform-core", "1.9.12", "implementation")]


def test_maven_and_poetry_are_parsed():
    pom = ("<dependency><groupId>e5.notification.hub.java.sdk</groupId>"
           "<artifactId>e5-notification-hub-java-sdk</artifactId>"
           "<version>0.1.4</version></dependency>")
    assert B._deps("pom.xml", pom) == [
        ("e5.notification.hub.java.sdk", "e5-notification-hub-java-sdk", "0.1.4", "maven")]
    assert B._deps("pyproject.toml", "e5_task_sdk_python = '1.0.9'\n") == [
        ("pypi", "e5_task_sdk_python", "1.0.9", "poetry")]


def test_third_party_dependencies_are_not_collected():
    """This is a graph of the PLATFORM's internal coupling. Spring and Hibernate pins are
    real but belong to whoever maintains the repo, and including them would bury the
    eight edges that matter under four hundred that do not."""
    body = ("    implementation 'org.springframework.boot:spring-boot-starter-web:3.3.4'\n"
            "    implementation 'io.grpc:grpc-netty:1.58.0'\n")
    assert B._deps("build.gradle", body) == []


def test_api_configuration_is_recorded_as_transitive():
    """`api` re-exports. platform-core declares task-sdk `api`, which is why every variant
    compiles against 0.7.6 without naming it anywhere. Losing this distinction would make
    the dependency graph claim six repos pin a version they never mention."""
    facts = [json.loads(l) for l in (KB / "graph" / "build-facts.jsonl").open()]
    core = [f for f in facts if f["repo"] == "platform-core"
            and f.get("artifact") == "e5-task-sdk-java" and f["kind"] == "DependsOnArtifact"]
    assert core and core[0]["transitive"] is True, "platform-core's api re-export lost"
    impl = [f for f in facts if f["repo"] == "interface"
            and f.get("artifact") == "e5-task-sdk-java" and f["kind"] == "DependsOnArtifact"]
    assert impl and impl[0]["transitive"] is False


# --------------------------------------------------------------------- the estate
def test_the_task_sdk_has_many_consumers_not_one():
    """The catalog recorded `pinned_by: platform-core/build.gradle` - a single string -
    for the wire protocol of the whole platform. That was not wrong so much as
    unrepresentable: there was one field and there are eight consumers."""
    facts = [json.loads(l) for l in (KB / "graph" / "build-facts.jsonl").open()]
    sdk = [f for f in facts if f.get("artifact") == "e5-task-sdk-java"
           and f["kind"] == "DependsOnArtifact"]
    assert len(sdk) >= 7, f"only {len(sdk)} task-sdk consumers found"
    assert len({f["version"] for f in sdk}) >= 2, \
        "the version skew this tool exists to surface has silently gone away - verify " \
        "that is real before relaxing this"


def test_skew_on_the_shared_protocol_fails_and_skew_elsewhere_does_not():
    """A library that two repos pin differently is a maintenance note. The TASK SDK is
    the wire format between planes, and every repo compiles cleanly against whichever
    version it pins - so no source gate and no compile gate can see this."""
    assert B.check([
        {"kind": "DependsOnArtifact", "artifact": "e5-task-sdk-java",
         "version": "0.5.2", "repo": "a", "configuration": "implementation"},
        {"kind": "DependsOnArtifact", "artifact": "e5-task-sdk-java",
         "version": "0.7.6", "repo": "b", "configuration": "implementation"},
    ]) == 1
    assert B.check([
        {"kind": "DependsOnArtifact", "artifact": "some-util",
         "version": "1.0", "repo": "a", "configuration": "implementation"},
        {"kind": "DependsOnArtifact", "artifact": "some-util",
         "version": "1.1", "repo": "b", "configuration": "implementation"},
    ]) == 0


def test_every_library_in_the_catalog_is_either_consumed_or_says_why_not():
    """A Library nobody depends on is either dead or mis-declared. Both are worth
    knowing, and neither was visible before: `maven-publish` is applied by 13 of 14
    Gradle repos, so publishing proves nothing about whether a thing is a library."""
    facts = [json.loads(l) for l in (KB / "graph" / "build-facts.jsonl").open()]
    # consumers outside the platform count: rf-fb-worker-core and non-rpa-worker-core are
    # used only by customer apps (graph/consumer-build-facts.jsonl, 2026-11-14)
    cons = KB / "graph" / "consumer-build-facts.jsonl"
    if cons.is_file():
        facts += [json.loads(l) for l in cons.open()]
    norm = lambda a: (a or "").lower().replace("-", "_")
    consumed = {norm(f.get("artifact")) for f in facts if f["kind"] == "DependsOnArtifact"}
    cat = C.load()
    libs = {n: c for n, c in cat["Component"].items() if c.get("kind") == "Library"}
    assert libs, "no Libraries in the catalog - the kind field is not being set"
    unconsumed = []
    for n, c in libs.items():
        coord = (c.get("publishes") or c.get("artifact") or "")
        art = coord.split(":")[-1]
        if art and norm(art) not in consumed:
            unconsumed.append(n)
    # eventstore-client-sdk really does have no consumer in this estate; state-service
    # and app-sdk are consumed under names that differ from their catalog coordinate.
    assert set(unconsumed) <= {"eventstore-client-sdk", "state-service", "app-sdk",
                               "task-sdk-java", "task-sdk-python", "rf-fb-worker-core",
                               "wf-scaffolding"}, \
        f"unexpected Library with no consumer: {unconsumed}"


def test_extraction_is_deterministic():
    """Same mirrors in, byte-identical facts out - the idempotence contract every other
    extractor here is held to."""
    a = B.extract()
    b = B.extract()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_no_credential_reaches_the_facts():
    """Build files carry `ARTIFACTORY_CREDS_USR` and friends. The extractor reads them
    and must not carry them anywhere."""
    body = (KB / "graph" / "build-facts.jsonl").read_text()
    for bad in ("ARTIFACTORY_CREDS_PSW", "JFROG_PASSWORD", "password", "ghp_"):
        assert bad not in body, f"{bad} leaked into build-facts.jsonl"

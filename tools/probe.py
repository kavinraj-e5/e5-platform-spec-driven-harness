#!/usr/bin/env python3
"""Measure a repository's integration signals, and PROPOSE a profile. Never decide one.

  python3 tools/probe.py --repo workflow-api            # a catalogued component
  python3 tools/probe.py --url element5inc/e5-new-svc   # something not yet onboarded
  python3 tools/probe.py --all                          # the whole estate, as a table

WHY PROPOSE AND NOT DECIDE
--------------------------
Auto-detection is the failure mode where a service carrying one stray proto is classified as
gRPC and its real Kafka edges are never looked for. The caller gets measurements, the profiles
those measurements are consistent with, and the signals that NO profile explains. A human
picks, and writes the one thing a probe can never produce: what the service is *for*.

The output is deliberately dull. Counts, with the paths that produced them, so a proposal can
be argued with. Anything this tool cannot explain it reports as unexplained rather than
rounding down to a default.
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mirror as M
import profiles as P

KB = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- git helpers
def _tree(bare: Path, ref: str) -> list[str]:
    r = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "--name-only", ref],
                       capture_output=True, text=True)
    return r.stdout.splitlines() if r.returncode == 0 else []


def _show(bare: Path, ref: str, path: str) -> str:
    r = subprocess.run(["git", "-C", str(bare), "show", f"{ref}:{path}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _grep_count(bare: Path, ref: str, pattern: str, paths: tuple = ()) -> int:
    """Files matching, not occurrences - occurrence counts are noisy and unstable.

    `paths` matters more than it looks: WORKER_MODE read in .java means the repo IS a role,
    while WORKER_MODE declared in .yaml means it CONFIGURES roles. workflow-config declares
    it 547 times and is not a role. Same string, opposite meaning, separated by pathspec.
    """
    cmd = ["git", "-C", str(bare), "grep", "-l", "-I", "-E", pattern, ref]
    if paths:
        cmd += ["--", *paths]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return len([l for l in r.stdout.splitlines() if l.strip()])


# --------------------------------------------------------------------------- the signals
def measure(bare: Path, ref: str) -> dict:
    """Every number here must be cheap, deterministic, and explainable by a path."""
    files = _tree(bare, ref)
    ext = lambda *s: [f for f in files if f.endswith(s)]

    protos = ext(".proto")
    graphql = ext(".graphqls", ".graphql")
    sql = ext(".sql")
    java = ext(".java")
    py = ext(".py")

    source = ext(".java", ".py", ".kt", ".go", ".ts", ".js")
    sig = {
        "files": len(files),
        "source_files": len(source),
        # a repo with a readable source tree is NOT a published-artifact-only contract
        "has_source": 1 if source else 0,
        "protos": len(protos),
        "graphql_files": len(graphql),
        "sql_files": len(sql),
        "java_files": len(java),
        "python_files": len(py),
        # a bare mention of "kafka" matches comments, config keys and dependency lines.
        # `kafka_bindings` counts only files that actually construct a producer/consumer.
        "kafka_refs": _grep_count(bare, ref, r"[Kk]afka"),
        "kafka_bindings": _grep_count(
            bare, ref,
            r"@KafkaListener|KafkaConsumer\(|KafkaProducer\(|KafkaTemplate|"
            r"StreamsBuilder|KafkaOutboundEndpoint|KafkaInboundEndpoint"),
        # a ServiceType-style enum is the single most reliable outbound-target encoding
        "service_type_enum": len([f for f in files if re.search(r"ServiceType\.(java|py)$", f)]),
        # reading the mode in CODE makes a repo a role; DECLARING it in yaml makes a repo
        # a build input. workflow-config declares it 547 times and is not a role.
        "worker_mode_reads": _grep_count(bare, ref, r"WORKER_MODE",
                                         paths=("*.java", "*.py", "*.kt")),
        "worker_mode_declared": _grep_count(bare, ref, r"WORKER_MODE",
                                            paths=("*.yaml", "*.yml", "*.json")),
        "grpc_impl_base": _grep_count(bare, ref, r"ImplBase\b"),
        "grpc_servicer": _grep_count(bare, ref, r"\w+Servicer\b"),
        "graphql_client": _grep_count(bare, ref, r"GraphQLClient|mutation\s+\w+\s*\("),
        "spring_controller": _grep_count(bare, ref, r"@(Controller|MutationMapping|QueryMapping)"),
        "kafka_listener": _grep_count(bare, ref, r"@KafkaListener|KafkaConsumer\("),
        "has_deployment_configs": len([f for f in files if "deployment-config" in f]),
    }

    # published-artifact dependencies: where the real contracts live on this platform
    coords = set()
    buildfiles = [f for f in files if f.endswith(("build.gradle", "build.gradle.kts",
                                                  "pom.xml", "pyproject.toml",
                                                  "requirements.txt"))]
    publishes = False
    for bf in buildfiles[:8]:
        txt = _show(bare, ref, bf)
        if re.search(r"maven-publish|jfrog|artifactory|push_to_jfrog", txt, re.I):
            publishes = True
        for m in re.finditer(r"['\"]((?:com\.)?e5[a-zA-Z0-9_.:-]*):([0-9][^'\"]*)['\"]", txt):
            coords.add(f"{m.group(1)}:{m.group(2)}")
        for m in re.finditer(r"([a-zA-Z0-9_.-]*e5[a-zA-Z0-9_.-]*)\s*=\s*['\"]([0-9][^'\"]*)['\"]", txt):
            coords.add(f"{m.group(1)}@{m.group(2)}")
    sig["jfrog_coords"] = len(coords)
    sig["jfrog_published"] = int(publishes)
    sig["_coords"] = sorted(coords)
    sig["_buildfiles"] = buildfiles[:8]
    sig["_sample"] = {"protos": protos[:3], "graphql": graphql[:3], "sql": sql[:3]}
    return sig


# --------------------------------------------------------------------------- the proposal
def _consistent(sig: dict, prof: P.Profile) -> tuple[bool, list[str]]:
    """A profile is consistent if none of its `expect` bounds are violated."""
    broken = []
    for key, want in prof.expect.items():
        got = sig.get(key)
        if got is None:
            continue                      # signal this probe does not measure yet
        if want == 0 and got != 0:
            broken.append(f"{key}={got}, profile asserts 0")
        elif want == "+" and not got:
            broken.append(f"{key}=0, profile expects some")
    return (not broken), broken


def propose(sig: dict) -> dict:
    """Rank the profiles this measurement is consistent with. Explain the rest."""
    ok, rejected = [], {}
    for name, prof in P.PROFILES.items():
        if name == "excluded":
            continue                       # never proposed; only ever chosen deliberately
        fits, broken = _consistent(sig, prof)
        if fits:
            # score by how many `indicates` hints are satisfied - a weak nudge, not a verdict
            score = 0
            for key, want in prof.indicates.items():
                got = sig.get(key)
                if got is None:
                    continue
                if (want == "+" and got) or (want == 0 and not got):
                    score += 1
            ok.append((score, name, prof.summary))
        else:
            rejected[name] = broken
    ok.sort(key=lambda x: (-x[0], x[1]))

    # Signals that no candidate profile accounts for. This is the important output: it is
    # how a genuinely new integration mechanism announces itself instead of being absorbed.
    unexplained = []

    # Ambiguity is a finding, not something to break by ranking. A tie at the top means
    # the measurement does not distinguish two integration styles, and picking the first
    # alphabetically would be exactly the silent auto-classification this tool exists to
    # avoid.
    if len(ok) >= 2 and ok[0][0] == ok[1][0]:
        tied = [n for sc, n, _ in ok if sc == ok[0][0]]
        unexplained.append(
            f"AMBIGUOUS: {tied} score equally. The signals measured here do not "
            f"distinguish them - decide from what the service is FOR, or add a signal "
            f"that tells them apart.")
    if ok and ok[0][0] <= 1:
        unexplained.append(
            f"WEAK: best candidate {ok[0][1]!r} matches only {ok[0][0]} hint. The "
            f"measurement is too thin to propose a profile with any confidence.")

    if sig.get("graphql_client") and not sig.get("graphql_files"):
        unexplained.append("acts as a GraphQL CLIENT (no schema of its own) - "
                           "subsystem/edge, or a mechanism not yet profiled")
    if sig.get("worker_mode_reads") and sig.get("protos"):
        unexplained.append("reads WORKER_MODE *and* ships protos - may be several roles "
                           "in one repo; split into Components before choosing a profile")
    if sig.get("jfrog_published") and not sig.get("files"):
        unexplained.append("publishes an artifact but has no readable tree")
    if sig.get("jfrog_published") and not sig.get("has_deployment_configs"):
        unexplained.append("publishes an artifact and has NO deployment config - likely a "
                           "build-input or contract, not a deployed Component. The probe "
                           "cannot tell these apart; it is a catalog decision.")
    if sig.get("worker_mode_declared") and not sig.get("worker_mode_reads"):
        unexplained.append("DECLARES WORKER_MODE in config but never reads it in code - "
                           "this configures roles rather than being one (build-input)")
    if not any([sig.get("protos"), sig.get("graphql_files"), sig.get("sql_files"),
                sig.get("kafka_refs"), sig.get("worker_mode_reads")]):
        unexplained.append("NO recognised integration signal at all - do not guess a "
                           "profile; establish how this service communicates first")
    return {"candidates": ok, "rejected": rejected, "unexplained": unexplained}


# --------------------------------------------------------------------------- reporting
SHOW = ["files", "source_files", "protos", "graphql_files", "sql_files", "java_files",
        "python_files", "kafka_refs", "kafka_bindings", "service_type_enum",
        "worker_mode_reads", "worker_mode_declared", "grpc_impl_base",
        "grpc_servicer", "graphql_client", "spring_controller", "kafka_listener",
        "jfrog_coords", "jfrog_published", "has_deployment_configs"]


def report(name: str, ref: str, sig: dict, pr: dict) -> str:
    out = [f"\n{'=' * 78}", f"PROBE  {name}  @ {ref}", "=" * 78, "\nmeasured signals:"]
    for k in SHOW:
        v = sig.get(k)
        if v:
            out.append(f"  {k:<24} {v}")
    skipped = [k for k in SHOW if not sig.get(k)]
    if skipped:
        out.append(f"  {'(zero)':<24} {', '.join(skipped)}")
    if sig.get("_coords"):
        out.append("\npublished artifacts it depends on:")
        for c in sig["_coords"][:8]:
            out.append(f"  {c}")

    out.append("\nprofiles consistent with this measurement:")
    if pr["candidates"]:
        for score, pname, summ in pr["candidates"]:
            out.append(f"  [{score}] {pname}")
            out.append(f"       {summ}")
    else:
        out.append("  NONE - every profile's expectations are violated.")

    if pr["rejected"]:
        out.append("\nruled out, and why:")
        for pname, broken in sorted(pr["rejected"].items()):
            out.append(f"  {pname:<24} {'; '.join(broken)}")

    if pr["unexplained"]:
        out.append("\n*** UNEXPLAINED SIGNALS - read these before choosing ***")
        for u in pr["unexplained"]:
            out.append(f"  ! {u}")

    out.append("\nthis tool does not choose. a human must supply:")
    out.append("  domain:   workflow-plane | subsystems | credentials | external")
    out.append("  system:   one of the profiles above")
    out.append("  role:     what this service is FOR, in prose  <- no probe can produce this")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", help="registry/catalog id, probed at its recorded branch")
    ap.add_argument("--url", help="org/name not yet onboarded; resolves latest release branch")
    ap.add_argument("--all", action="store_true", help="table across the whole estate")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    _root, repos = M.registry()
    byid = {r["id"]: r for r in repos}

    if a.all:
        rows = []
        print(f"{'repo':<22} {'proto':>5} {'gql':>4} {'sql':>4} {'kafka':>5} {'svcT':>5} "
              f"{'wMode':>6} {'jfrog':>5}  top candidate")
        print("-" * 104)
        for r in sorted(repos, key=lambda x: x["id"]):
            bare = M.CACHE / (r["id"] + ".git")
            if not bare.is_dir():
                continue
            sig = measure(bare, r["branch"])
            pr = propose(sig)
            top = pr["candidates"][0][1] if pr["candidates"] else "NONE"
            flag = "  !" if pr["unexplained"] else ""
            print(f"{r['id']:<22} {sig['protos']:>5} {sig['graphql_files']:>4} "
                  f"{sig['sql_files']:>4} {sig['kafka_refs']:>5} {sig['service_type_enum']:>5} "
                  f"{sig['worker_mode_reads']:>6} {sig['jfrog_coords']:>5}  {top}{flag}")
            rows.append({"repo": r["id"], "signals": sig, "proposal": pr})
        print("\n! = has unexplained signals; probe --repo <id> for detail")
        if a.json:
            print(json.dumps(rows, indent=1, default=str))
        return 0

    if a.repo:
        r = byid.get(a.repo)
        if not r:
            print(f"unknown id {a.repo!r}; known: {', '.join(sorted(byid))}")
            return 2
        bare, ref, name = M.CACHE / (a.repo + ".git"), r["branch"], a.repo
    elif a.url:
        full = a.url if "/" in a.url else f"element5inc/{a.url}"
        name = full.split("/")[-1]
        bare = M.CACHE / (name + ".git")
        if not bare.is_dir():
            print(f"no mirror at {bare}.\n  run: python3 tools/mirror.py sync --repo {name}")
            return 2
        try:
            rels = sorted([b for b in M.ls_release_branches(full) if M.vkey(b)], key=M.vkey)
        except Exception as e:
            print(f"could not list release branches: {e}")
            return 2
        if not rels:
            print(f"{full} has no release/vX.Y.Z branch - refusing to guess a ref")
            return 2
        ref = rels[-1]
    else:
        print("need --repo, --url or --all")
        return 2

    sig = measure(bare, ref)
    pr = propose(sig)
    if a.json:
        print(json.dumps({"name": name, "ref": ref, "signals": sig, "proposal": pr},
                         indent=1, default=str))
    else:
        print(report(name, ref, sig, pr))
    return 0


if __name__ == "__main__":
    sys.exit(main())

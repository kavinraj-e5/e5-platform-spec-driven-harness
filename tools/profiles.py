#!/usr/bin/env python3
"""Extraction profiles: which extractors apply to a Component, and which must find nothing.

WHY THIS EXISTS
---------------
Before profiles, all eight extractors ran against all 22 repos. The four workflow-plane repos
yielded 12 facts out of 3,852 - 0.3% - because every extractor was written for
proto/SQL/GraphQL subsystems and silently returned nothing elsewhere. Nobody noticed, because
an extractor that finds nothing is indistinguishable from a service that has nothing, and from
an extractor that is broken.

THE LOAD-BEARING HALF IS `na`, NOT `runs`
-----------------------------------------
`runs` is an optimisation: skip work that cannot pay off. `na` is a *claim* - "a Component on
this profile has no gRPC surface" - and CI fails when the claim stops holding. That is what
turns silence into a checkable assertion, and it is the mechanism by which a service that
changes shape gets noticed instead of quietly under-extracted.

ADDING A PROFILE IS A DELIBERATE ACT
------------------------------------
When `probe.py` finds signals no profile explains, it reports them and stops. It must not fall
back to a default. A new integration mechanism in the estate should cost a reviewed profile
definition, not a silent extractor patch.
"""
from __future__ import annotations

# Extractor keys as registered in extractors.EXTRACTORS.
# `kafka` and `virtual_queues` were here and are deleted: both keyed on a value the
# devops pipeline injects at deploy time, so no profile can run them correctly. The
# producer->consumer link they were reaching for is `routing` plus the authored join
# `_reference/joins/task-names.yaml`, which agree on the TASK NAME from two code sites.
ALL_EXTRACTORS = {"proto", "graphql", "events",
                  "routing", "migrations", "config",
                  # workflow-plane: the roles have no protos and no GraphQL, so their
                  # contracts are the WORKER_MODE split, the handlers they register and
                  # the published SDKs they import
                  "worker_roles", "handlers", "sdk_imports", "db_bindings",
                  "rf_keywords"}


class Profile:
    """One integration style.

    runs      extractors that may legitimately produce facts here
    na        extractors asserted to produce NOTHING; a hit is a CI failure
    expect    measured signal bounds, verified per Component by sync.py
    indicates signals whose presence suggests this profile (probe hints only - never
              authoritative, because a single stray proto must not reclassify a service)
    """

    def __init__(self, name, summary, runs, na, expect=None, indicates=None):
        self.name = name
        self.summary = summary
        self.runs = set(runs)
        self.na = set(na)
        self.expect = expect or {}
        self.indicates = indicates or {}
        unknown = (self.runs | self.na) - ALL_EXTRACTORS
        if unknown:
            raise ValueError(f"{name}: unknown extractors {unknown}")
        overlap = self.runs & self.na
        if overlap:
            raise ValueError(f"{name}: {overlap} both runs and n/a - contradictory")

    def __repr__(self):
        return f"<Profile {self.name}>"


# ---------------------------------------------------------------------------
# The profiles. Keyed by Component `system`, not by repo.
#
# Signal counts in `expect` are bounds, not exact values: a service may legitimately
# gain a table. They exist to catch a service changing INTEGRATION STYLE - which is
# the thing that silently breaks extraction.
# ---------------------------------------------------------------------------
PROFILES = {

    "workflow/execution": Profile(
        name="workflow/execution",
        summary="A WORKER_MODE role inside the variant image. Talks to the task plane "
                "through a published SDK; owns no schema and serves no RPC of its own.",
        # `virtual_queues` was missing here, and this is the profile that needs it most:
        # fb-worker and wf-worker are the workflow-plane POLLERS. rf-fb-worker-core
        # declares its queue key at conf/settings.py:23 (`VIRTUAL_QNAME = os.getenv(
        # "VIRTUAL_QNAME", "")`) and polls it at api/keywords.py:659
        # (`isTaskAvailable(virtualQName=VIRTUAL_QNAME, ...)`). Because the extractor
        # never ran here, every workflow-plane queue looked unresolvable from code and
        # the gap was wrongly blamed on the cluster snapshot.
        runs=["worker_roles", "handlers", "sdk_imports", "db_bindings",
              "rf_keywords", "config", "events"],
        na=["proto", "graphql", "migrations"],
        expect={"protos": 0, "graphql_files": 0, "sql_files": 0},
        indicates={"worker_mode_reads": "+", "jfrog_coords": "+", "protos": 0,
                   "source_files": "+"},
    ),

    "workflow/task-plane": Profile(
        name="workflow/task-plane",
        summary="AKST / task-manager. Serves the task-dequeue RPCs that every other "
                "Component polls.",
        runs=["proto", "routing", "config", "worker_roles", "handlers", "sdk_imports", "db_bindings"],
        na=["graphql", "migrations"],
        expect={"graphql_files": 0, "sql_files": 0},
        indicates={"protos": "+", "task_service": "+"},
    ),

    "subsystem/control": Profile(
        name="subsystem/control",
        summary="gRPC subsystem that also participates in the task plane. Holds a "
                "ServiceType enum naming its outbound targets.",
        runs=["proto", "routing", "events", "config",
              "handlers", "sdk_imports"],
        na=["graphql", "migrations"],
        expect={"graphql_files": 0, "sql_files": 0},
        indicates={"protos": "+", "service_type_enum": "+"},
    ),

    "subsystem/gateway": Profile(
        name="subsystem/gateway",
        summary="The front door. Serves GraphQL to external callers, calls subsystems over "
                "gRPC, and produces tasks. The only Component that is both a server and a "
                "client of two different protocols.",
        runs=["graphql", "proto", "routing", "events", "config"],
        na=["migrations"],
        expect={"graphql_files": "+", "sql_files": 0},
        indicates={"graphql_files": "+", "spring_controller": "+", "protos": "+"},
    ),

    "subsystem/data": Profile(
        name="subsystem/data",
        summary="The schema owner. Pure sink: serves gRPC, owns the tables, calls nobody.",
        runs=["proto", "migrations", "config"],
        na=["graphql"],
        expect={"graphql_files": 0, "sql_files": "+", "outbound_edges": 0},
        indicates={"sql_files": "+", "protos": "+"},
    ),

    "subsystem/event-driven": Profile(
        name="subsystem/event-driven",
        summary="Kafka consumer/producer with its own tables. No gRPC surface at all.",
        runs=["events", "migrations", "config"],
        na=["proto", "graphql"],
        expect={"protos": 0, "graphql_files": 0, "kafka_bindings": "+", "graphql_client": 0},
        indicates={"protos": 0, "kafka_bindings": "+", "sql_files": "+"},
    ),

    "subsystem/edge": Profile(
        name="subsystem/edge",
        summary="Outward-facing API that reaches the platform as a CLIENT - via GraphQL "
                "or HTTP - rather than as a gRPC peer.",
        runs=["graphql", "migrations", "config", "events"],
        na=["proto"],
        expect={"protos": 0},
        indicates={"protos": 0, "graphql_client": "+"},
    ),

    "contract/artifact": Profile(
        name="contract/artifact",
        summary="A published jar or wheel that IS a contract. No source checkout; facts "
                "come from artifact introspection, never from source extractors.",
        runs=[],
        na=sorted(ALL_EXTRACTORS),
        # has_source is the discriminator. Without it this profile has no bounds and so is
        # "consistent" with everything - the probe proposed it for interface and platform-core
        # on its first run, which is exactly backwards.
        expect={"has_source": 0},
        indicates={"jfrog_published": "+", "has_source": 0},
    ),

    "build-input": Profile(
        name="build-input",
        summary="Scaffolding, templates or layered config. Composes other artifacts into a "
                "deployable; is not itself a Component.",
        runs=["config"],
        na=["proto", "graphql", "migrations", "routing"],
        expect={"protos": 0, "graphql_files": 0, "sql_files": 0},
        indicates={"worker_mode_declared": "+", "jfrog_coords": "+", "source_files": 0},
    ),

    # Added 2026-11-14, deliberately (not by widening build-input): the task SDK turned out
    # to have a source repo (e5-task-sdk-java), and its protos ARE the transport task
    # protocol. Source extraction of those protos gives the wire format with file:line.
    "contract/source": Profile(
        name="contract/source",
        summary="A published library whose SOURCE is a platform contract - the task protocol "
                "SDK. Its protos define the wire format, so they are extracted; it serves "
                "nothing, so GraphQL, migrations and routing must find nothing.",
        runs=["proto", "config"],
        na=["graphql", "migrations", "routing"],
        expect={"protos": "+", "graphql_files": 0, "sql_files": 0},
        indicates={"jfrog_coords": "+", "protos": "+"},
    ),

    "excluded": Profile(
        name="excluded",
        summary="Deliberately not part of the knowledge base - tooling, CI, generators. "
                "Named so that its absence is a decision rather than an oversight.",
        runs=[],
        na=sorted(ALL_EXTRACTORS),
        expect={},
        indicates={},
    ),
}


def get(system: str) -> Profile:
    if system not in PROFILES:
        raise KeyError(
            f"no profile for system {system!r}. Known: {sorted(PROFILES)}.\n"
            f"  Adding a profile is deliberate - see the module docstring. Do NOT widen an "
            f"existing profile to absorb a service whose integration style differs.")
    return PROFILES[system]


def extractors_for(system: str) -> set:
    return set(get(system).runs)


def na_for(system: str) -> set:
    return set(get(system).na)


def summary_table() -> str:
    w = max(len(p) for p in PROFILES)
    out = [f"{'profile'.ljust(w)}  {'runs':<46} n/a"]
    out.append("-" * (w + 90))
    for name, p in PROFILES.items():
        out.append(f"{name.ljust(w)}  {','.join(sorted(p.runs)) or '-':<46} "
                   f"{','.join(sorted(p.na)) or '-'}")
    return "\n".join(out)


if __name__ == "__main__":
    print(summary_table())
    print(f"\n{len(PROFILES)} profiles covering {len(ALL_EXTRACTORS)} extractors")
    # every extractor must be claimed by at least one profile, or it is dead code
    claimed = set().union(*(p.runs for p in PROFILES.values()))
    # An extractor a profile neither RUNS nor asserts N/A is simply never run there,
    # and nothing used to say so. `virtual_queues` was unlisted on workflow/execution
    # for exactly this reason. These are reported rather than raised: filling them in
    # means asserting an extractor finds nothing, and a wrong N/A assertion fails CI on
    # a correct estate. Each one is a deliberate human call.
    gaps = {n: sorted(ALL_EXTRACTORS - (p.runs | p.na)) for n, p in PROFILES.items()}
    gaps = {n: g for n, g in gaps.items() if g}
    if gaps:
        print(f"\n{len(gaps)} profile(s) neither run nor assert N/A for some extractor:")
        for n, g in sorted(gaps.items()):
            print(f"  {n:26} unlisted: {', '.join(g)}")

    orphan = ALL_EXTRACTORS - claimed
    print(f"extractors no profile runs: {orphan or 'none'}")

#!/usr/bin/env python3
"""The catalog: System -> Component, keyed on ROLE rather than repository.

  python3 tools/catalog.py list
  python3 tools/catalog.py show wf-engine
  python3 tools/catalog.py check          # structural validation, no git access
  python3 tools/catalog.py registry       # emit the legacy registry.yaml view

WHY ROLE AND NOT REPOSITORY
---------------------------
`platform-core` is ONE repository that becomes SIX different services depending on
`WORKER_MODE` (E5WorkflowEngine.java:347-453), plus a library consumed by variant-scaffolding.
A repo-keyed model cannot express that, and the old registry.yaml did not: it listed
`platform-core` and `task-manager` as two repos with the *identical* tree SHA, and pointed the
`task-manager` deployment at the wrong one entirely - the real deployed task-manager is
`e5-akst-service` (cluster image `e5-platform-ss-task-manager:v1.2.2-RELEASE-AKST-...`).

That single mis-keying made three workflow roles look like missing source when they were
packages all along, and produced a confidently wrong answer about which repo to change.

Reads a YAML subset via tools/release/minyaml so that kb_core and every CI gate stay free of
third-party dependencies.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "release"))
import minyaml
import profiles as P

KB = HERE.parent
CATALOG = KB / "catalog"

KINDS = {"System", "Component", "Resource", "API"}

# WHY THERE IS NO `Domain` LEVEL ANY MORE (2026-10-12)
# ----------------------------------------------------
# There was one: Domain -> System -> Component, where Domain was the runtime deployment
# topology (`workflow-plane`, `subsystems`, ...). Because every Component had to sit in
# some Domain, everything that is NEVER DEPLOYED was filed under `subsystems` - a domain
# whose defining property is a Kubernetes namespace pattern. platform-core, task-sdk,
# app-sdk and wf-scaffolding all landed there; 11 of the 23 members of `subsystems` were
# not subsystems, and app development had no home at all.
#
# One axis cannot carry capability, packaging and topology at once. So topology stopped
# being the classifier and became two fields on the Component:
#
#   kind   WHAT IS IT       Library | Deployable | Role | BaseImage | Config | Tooling | External
#   plane  WHERE DOES IT RUN  workflow | subsystem | external | none
#
# A Library now has `plane: none` - which is a statement, not a gap - instead of being
# assigned a namespace it will never occupy.
KINDS_OF_COMPONENT = {"Library", "Deployable", "Role", "BaseImage", "Config",
                      "Tooling", "External", "Capability"}
PLANES = {"workflow", "subsystem", "external", "none"}
AUDIENCES = {"workflow-developer", "app-developer", "support", "hub-reporting",
             "all", "none"}
# Ownership and lifecycle.
#
# There was no owner field on any of the 37 Components; accountability lived as prose in
# constitution/ownership.md, which nothing reads and nothing checks. A portal would make
# `owner` required on every Component and that is the right instinct - but making it
# required HERE would fail the catalog for all 37 on the day it was added, and the usual
# response to that is a bulk commit of plausible-looking owners. An invented owner is
# worse than a missing one: it reads as accountability and answers to nobody.
#
# So the field is optional, defaults to UNASSIGNED, and the count is reported as a
# WARNING that someone has to look at. Filling it in is a human pass.
UNASSIGNED = "UNASSIGNED"
LIFECYCLES = {"production", "experimental", "deprecated", "unknown"}

REQUIRED = {
    "System":    ["name", "audience", "summary"],
    # `role` is prose and is REQUIRED: a Component with no stated purpose is a row in a
    # table, not knowledge. The probe cannot generate it, which is the point.
    "Component": ["name", "system", "kind", "plane", "audience", "profile",
                  "role", "source"],
    "Resource":  ["name", "kind_of", "summary"],
    "API":       ["name", "kind_of", "provided_by"],
}


class CatalogError(ValueError):
    pass


def _entities() -> list[dict]:
    out = []
    if not CATALOG.is_dir():
        return out
    for f in sorted(CATALOG.rglob("*.yaml")):
        try:
            doc = minyaml.load(f.read_text())
        except Exception as e:
            raise CatalogError(f"{f.relative_to(KB)}: {e}") from None
        if not isinstance(doc, dict):
            raise CatalogError(f"{f.relative_to(KB)}: top level must be a mapping")
        for key in ("systems", "components", "resources", "apis"):
            for item in (doc.get(key) or []):
                item["_file"] = str(f.relative_to(KB))
                # `_entity` not `kind`: a Component now carries its OWN `kind`
                # (Library / Deployable / Role), which is the user-facing classification.
                # Reusing one key for both made "Deployable" look like an unknown entity type.
                item["_entity"] = (key[:-1].capitalize() if key != "apis" else "API")
                out.append(item)
    return out


# `source.repo` carries two sentinels that are NOT repositories: EXTERNAL (a third-party
# system) and UNKNOWN (a component whose home nobody has recorded). Counting them inflates
# any "how many repos" figure, and `unindexed` does not filter them reliably - four
# UNKNOWN components are not marked unindexed, which is how `freshness()` reported 24
# indexed repos against a registry of 23, and how "24 repos" reached the docs.
# Defined here because catalog.py owns the catalog; citations.py, workspace.py and
# scip_pilot.py each grew their own copy of this literal, which is what let kb_core miss it.
NOT_A_REPO = frozenset({None, "", "EXTERNAL", "UNKNOWN"})

_cache: dict = {}


def load(refresh: bool = False) -> dict:
    """Return {kind: {name: entity}} plus a flat index."""
    if "cat" in _cache and not refresh:
        return _cache["cat"]
    ents = _entities()
    bykind: dict = {k: {} for k in KINDS}
    for e in ents:
        k = e.get("_entity")
        if k not in KINDS:
            raise CatalogError(f"{e.get('_file')}: unknown entity type {k!r}")
        name = e.get("name")
        if not name:
            raise CatalogError(f"{e.get('_file')}: entity with no name")
        if name in bykind[k]:
            raise CatalogError(f"duplicate {k} {name!r} "
                               f"({bykind[k][name]['_file']} and {e['_file']})")
        if k == "Component":
            e.setdefault("owner", UNASSIGNED)
            e.setdefault("lifecycle", "unknown")
        bykind[k][name] = e
    _cache["cat"] = bykind
    return bykind


# --------------------------------------------------------------------------- validation
def check() -> list[str]:
    """Structural problems only - nothing here touches git or the network."""
    errs = []
    try:
        cat = load(refresh=True)
    except CatalogError as e:
        return [str(e)]

    for kind, items in cat.items():
        for name, e in items.items():
            for field in REQUIRED[kind]:
                if not e.get(field):
                    errs.append(f"{kind} {name}: missing required field {field!r} "
                                f"({e['_file']})")

    for name, s in cat["System"].items():
        if s.get("audience") not in AUDIENCES:
            errs.append(f"System {name}: audience {s.get('audience')!r} is not one of "
                        f"{sorted(AUDIENCES)}")

    for name, c in cat["Component"].items():
        sysname = c.get("system")
        if sysname not in cat["System"]:
            errs.append(f"Component {name}: system {sysname!r} is not a declared System")
        if c.get("kind") not in KINDS_OF_COMPONENT:
            errs.append(f"Component {name}: kind {c.get('kind')!r} is not one of "
                        f"{sorted(KINDS_OF_COMPONENT)}")
        if c.get("plane") not in PLANES:
            errs.append(f"Component {name}: plane {c.get('plane')!r} is not one of "
                        f"{sorted(PLANES)}")
        if c.get("audience") not in AUDIENCES:
            errs.append(f"Component {name}: audience {c.get('audience')!r} is not one of "
                        f"{sorted(AUDIENCES)}")
        # The profile is now carried by the Component, not inherited from its System.
        # That decoupling is what let the taxonomy be rewritten without re-indexing a
        # single repo: `system` answers whose problem this is, `profile` answers which
        # extractors apply, and the two no longer have to agree.
        prof = c.get("profile")
        if prof and prof not in P.PROFILES:
            errs.append(f"Component {name}: profile {prof!r} is not defined in profiles.py")
        # A Library that is never deployed must not claim a runtime placement, and a
        # Deployable must have one. This is the invariant the old model could not state.
        if c.get("kind") in ("Library", "Config", "BaseImage") and c.get("plane") != "none":
            errs.append(f"Component {name}: kind {c['kind']} is not deployed, so plane "
                        f"must be 'none', not {c.get('plane')!r}")
        if c.get("kind") == "Deployable" and c.get("plane") == "none":
            errs.append(f"Component {name}: a Deployable must declare a plane")
        if c.get("kind") == "Role" and not c.get("worker_mode"):
            errs.append(f"Component {name}: a Role is selected out of a Deployable and "
                        f"must say by what - `worker_mode` is missing")
        src = c.get("source") or {}
        if not isinstance(src, dict):
            errs.append(f"Component {name}: `source` must be a mapping")
            continue
        if not src.get("repo"):
            errs.append(f"Component {name}: source.repo is required")
        # A Component that claims no code cannot be verified against anything. Three
        # legitimate shapes, and nothing else:
        #   source.paths            indexed from a repo
        #   indexed_from: artifact  indexed from a published jar/wheel (no source exists)
        #   unindexed: true         declared but not indexed, with the reason in `role`
        if not src.get("paths") and not c.get("unindexed") \
                and c.get("indexed_from") not in ("artifact", "composed"):
            errs.append(f"Component {name}: source.paths is required "
                        f"(or `indexed_from: artifact`, or `unindexed: true` with the "
                        f"reason in `role`)")
        # `indexed_from: composed` - the library's code IS indexed, but through the
        # Components that own its packages. Without the covered_by check this would be an
        # unfalsifiable excuse for having no paths, which is how platform-core went from
        # "indexed via its roles" to having no entity at all.
        if c.get("indexed_from") == "composed":
            cov = c.get("covered_by") or []
            if not cov:
                errs.append(f"Component {name}: indexed_from: composed but no "
                            f"`covered_by` naming the Components that cover it")
            for other in cov:
                if other not in cat["Component"]:
                    errs.append(f"Component {name}: covered_by {other!r} is not a "
                                f"declared Component")
        if c.get("indexed_from") == "artifact" and not c.get("artifact"):
            errs.append(f"Component {name}: indexed_from: artifact but no `artifact` "
                        f"coordinate to fetch")

    for name, c in cat["Component"].items():
        lc = c.get("lifecycle")
        if lc not in LIFECYCLES:
            errs.append(f"Component {name}: lifecycle {lc!r} is not one of "
                        f"{sorted(LIFECYCLES)}")
        owner = c.get("owner")
        if not owner or not str(owner).strip():
            errs.append(f"Component {name}: `owner` is present but empty - omit it "
                        f"or name someone")

    for name, api in cat["API"].items():
        for p in (api.get("provided_by") or []):
            if p not in cat["Component"]:
                errs.append(f"API {name}: provided_by {p!r} is not a declared Component")
    return errs


def warnings() -> list[str]:
    """Things a human should fix, that must not fail a build.

    An unowned Component is a real gap and a loud one, but failing CI for it would
    block every unrelated change until 37 owners were invented in one sitting - which
    is precisely how catalogs fill up with names that answer to nobody."""
    cat = load()
    out = []
    unowned = sorted(n for n, c in cat["Component"].items()
                     if c.get("owner") == UNASSIGNED)
    if unowned:
        out.append(f"{len(unowned)}/{len(cat['Component'])} Components have no owner: "
                   + ", ".join(unowned[:6]) + (" ..." if len(unowned) > 6 else ""))
    unknown_lc = sorted(n for n, c in cat["Component"].items()
                        if c.get("lifecycle") == "unknown")
    if unknown_lc:
        out.append(f"{len(unknown_lc)}/{len(cat['Component'])} Components have no "
                   f"declared lifecycle")
    return out


def resolve_profile(component: str) -> P.Profile:
    cat = load()
    c = cat["Component"].get(component)
    if not c:
        raise KeyError(f"no Component {component!r}")
    prof = c.get("profile") or cat["System"][c["system"]].get("profile")
    return P.get(prof)


def verify_sources(repo_filter: str | None = None) -> list[str]:
    """Every Component's path globs must resolve to real files at its indexed commit.

    This is the check the repo-keyed registry could not express, and the reason three
    workflow roles looked like missing source: nothing asserted that a declared role maps
    to code that exists. It is also the only thing that catches ROLE DRIFT - a package
    moving between roles changes which Component owns which code without touching any
    contract, so no contract diff would notice.
    """
    import fnmatch, subprocess
    sys.path.insert(0, str(HERE))
    import mirror as M

    _root, repos = M.registry()
    branch = {r["id"]: r["branch"] for r in repos}
    errs = []
    cat = load()
    for cname, c in sorted(cat["Component"].items()):
        if c.get("unindexed") or c.get("indexed_from") == "artifact":
            continue
        src = c.get("source") or {}
        repo = src.get("repo")
        if repo_filter and repo != repo_filter:
            continue
        bare = M.CACHE / (f"{repo}.git")
        if not bare.is_dir():
            errs.append(f"{cname}: no mirror for repo {repo!r} at {bare}")
            continue
        ref = branch.get(repo)
        if not ref:
            # catalog may name a repo the legacy registry does not; resolve from the mirror
            r = subprocess.run(["git", "-C", str(bare), "for-each-ref", "--sort=-refname",
                                "--format=%(refname:short)", "refs/heads/release/*"],
                               capture_output=True, text=True)
            rels = [b for b in r.stdout.splitlines() if M.vkey(b)]
            if not rels:
                errs.append(f"{cname}: repo {repo!r} has no release branch to verify against")
                continue
            ref = sorted(rels, key=M.vkey)[-1]
        tree = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "--name-only", ref],
                              capture_output=True, text=True).stdout.splitlines()
        for glob in (src.get("paths") or []):
            pat = glob.replace("/**", "/*")
            hit = any(fnmatch.fnmatch(f, pat) or f.startswith(glob.replace("/**", "/"))
                      or fnmatch.fnmatch(f, glob) for f in tree)
            if not hit:
                errs.append(f"{cname}: source path {glob!r} matches NOTHING in "
                            f"{repo}@{ref} - role drift, or a typo")
    return errs


def components_by_repo() -> dict:
    """repo -> [Component]. One repo legitimately yields many."""
    out: dict = {}
    for name, c in load()["Component"].items():
        out.setdefault((c.get("source") or {}).get("repo"), []).append(name)
    return out


# --------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("check")
    v = sub.add_parser("verify"); v.add_argument("--repo")
    sub.add_parser("registry")
    p = sub.add_parser("show"); p.add_argument("name")
    a = ap.parse_args()

    if a.cmd == "check":
        errs = check()
        for e in errs:
            print(f"ERROR  {e}")
        warns = warnings()
        for w in warns:
            print(f"WARN   {w}")
        cat = load()
        n = {k: len(v) for k, v in cat.items() if v}
        print(f"\n{n} — {len(errs)} errors, {len(warns)} warnings")
        return 1 if errs else 0

    if a.cmd == "verify":
        errs = verify_sources(a.repo)
        for e in errs:
            print(f"ERROR  {e}")
        n = len([c for c in load()["Component"].values() if not c.get("unindexed")])
        print(f"\n{n} indexed Components verified against their mirrors — "
              f"{len(errs)} errors")
        return 1 if errs else 0

    cat = load()
    if a.cmd == "list":
        for sname, sdef in sorted(cat["System"].items()):
            members = {n: c for n, c in cat["Component"].items()
                       if c.get("system") == sname}
            print(f"\n{sname}  [for: {sdef.get('audience','?')}]  "
                  f"— {' '.join((sdef.get('summary') or '').split())[:70]}")
            for cname, c in sorted(members.items(),
                                   key=lambda kv: (kv[1].get("kind", ""), kv[0])):
                src = c.get("source") or {}
                extra = []
                if c.get("worker_mode"): extra.append(f"WORKER_MODE={c['worker_mode']}")
                if c.get("unindexed"):   extra.append("UNINDEXED")
                if c.get("realises"):    extra.append(f"realises={c['realises']}")
                print(f"    {c.get('kind','?'):<11} {cname:<24} "
                      f"{c.get('plane','?'):<10} {src.get('repo','-'):<22} "
                      f"{' '.join(extra)}")
        tot = len(cat["Component"])
        repos = len({(c.get('source') or {}).get('repo') for c in cat["Component"].values()})
        print(f"\n{tot} Components across {repos} repos "
              f"({len(cat['Resource'])} Resources, {len(cat['API'])} APIs)")
        return 0

    if a.cmd == "show":
        for kind in KINDS:
            if a.name in cat[kind]:
                e = dict(cat[kind][a.name])
                print(json.dumps(e, indent=1, default=str))
                if kind == "Component":
                    pr = resolve_profile(a.name)
                    print(f"\nprofile {pr.name}\n  runs: {sorted(pr.runs)}\n"
                          f"  n/a : {sorted(pr.na)}\n  expect: {pr.expect}")
                return 0
        print(f"no entity named {a.name!r}")
        return 2

    if a.cmd == "registry":
        # back-compat view so existing tooling keeps working during the transition
        print("# GENERATED from catalog/ by tools/catalog.py registry — do not hand-edit")
        print("apiVersion: e5.kb/v1\nkind: Registry\nsystem: e5-platform\nrepos:")
        seen = set()
        for cname, c in sorted(cat["Component"].items()):
            src = c.get("source") or {}
            repo = src.get("repo")
            if not repo or repo in seen:
                continue
            seen.add(repo)
            print(f"  - id: {repo}")
            print(f"    components: [{', '.join(sorted(components_by_repo()[repo]))}]")
            if src.get("remote"):
                print(f"    remote: {src['remote']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())

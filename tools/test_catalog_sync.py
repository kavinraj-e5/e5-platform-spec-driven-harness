#!/usr/bin/env python3
"""Tests for the update loop. Each one defends a property the old indexer did not have.

The fact count is a fixture; these are the deliverable. Every test here corresponds to a
way the loop could rot back into "runs everything everywhere and reports nothing".
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog as C
import profiles as P
import sync as S
import mirror as M

KB = Path(__file__).resolve().parent.parent
FACTS = KB / "graph" / "facts.jsonl"


# --------------------------------------------------------------- the catalog itself
def test_catalog_is_structurally_valid():
    errs = C.check()
    assert not errs, "catalog/ is invalid:\n" + "\n".join(f"  {e}" for e in errs)


def test_every_component_states_its_purpose():
    """`role` is prose a probe cannot generate. A Component without it is a table row."""
    thin = []
    for name, c in C.load()["Component"].items():
        role = (c.get("role") or "").strip()
        if len(role) < 40:
            thin.append(f"{name} ({len(role)} chars)")
    assert not thin, (
        "these Components have no meaningful `role` prose: " + ", ".join(thin) +
        "\n  The catalog's value over a repo list IS this field."
    )


def test_component_roles_resolve_to_source():
    """Every declared path glob must match real files at the indexed commit.

    This is what the repo-keyed registry could not assert, and why three workflow roles
    looked like missing source when they were packages in platform-core all along. It is
    also the ONLY check that catches role drift: moving a package between roles changes
    which Component owns which code without touching any contract, so no contract diff
    would ever notice.
    """
    errs = C.verify_sources()
    assert not errs, "\n".join(f"  {e}" for e in errs)


def test_no_duplicate_repo_entries():
    """Two ids pointing at one git tree double-count every fact in it.

    registry.yaml listed `platform-core` and `task-manager` with the IDENTICAL tree SHA,
    which duplicated 71 facts and pointed the task-manager deployment at the wrong repo
    entirely - the real one is e5-akst-service.
    """
    _root, repos = M.registry()
    trees = {}
    dupes = []
    for r in repos:
        bare = M.CACHE / f"{r['id']}.git"
        if not bare.is_dir():
            continue
        p = subprocess.run(["git", "-C", str(bare), "rev-parse", f"{r['branch']}^{{tree}}"],
                           capture_output=True, text=True)
        sha = p.stdout.strip()
        if not sha:
            continue
        if sha in trees:
            dupes.append(f"{r['id']} and {trees[sha]} are the same tree {sha[:12]}")
        trees[sha] = r["id"]
    assert not dupes, "\n".join(f"  {d}" for d in dupes)


# --------------------------------------------------------------- the profiles
def test_every_extractor_is_claimed_by_a_profile():
    """An extractor no profile runs is dead code that still has to be maintained."""
    claimed = set().union(*(p.runs for p in P.PROFILES.values()))
    orphan = P.ALL_EXTRACTORS - claimed
    assert not orphan, f"no profile runs {orphan}"


def test_profiles_never_both_run_and_disclaim():
    for name, prof in P.PROFILES.items():
        overlap = prof.runs & prof.na
        assert not overlap, f"{name}: {overlap} is both run and asserted absent"


def test_profile_declared_na_holds():
    """The N/A assertions must actually hold on the current estate.

    This is the load-bearing half of the whole design. An extractor that finds nothing is
    indistinguishable from a service that has nothing, and from a broken extractor - which
    is how four workflow-plane repos came to yield 12 facts out of 3,852 unnoticed. A
    failure here means a Component changed integration style; move it to another system
    rather than widening the profile to make this pass.
    """
    out = S.run(dry=True)
    # dry run tells us what WOULD be extracted; the assertions are checked during a real
    # run, so verify the last completed run left no N/A violations recorded
    assert out is not None
    res = S.run(force=True)
    na_errs = [e for e in res["errors"] if "asserts NO" in e]
    assert not na_errs, "\n".join(f"  {e}" for e in na_errs)


def test_workflow_plane_is_not_blind():
    """The regression this rewrite exists to prevent.

    Before profiles, the four workflow-plane repos produced 12 facts out of 3,852 - 0.3%
    for the part of the platform that does the actual automation work. If this number
    collapses again, an extractor has silently stopped applying.
    """
    # Asserted on `plane`, not on a system-name prefix. The taxonomy was rewritten on
    # 2026-10-12 and `workflow/execution` ceased to exist as a system name; a test keyed
    # on that string would have gone quietly green against zero matching facts, which is
    # the exact failure mode this test exists to catch.
    import catalog as C
    wf_components = {n for n, c in C.load()["Component"].items()
                     if c.get("plane") == "workflow"}
    facts = [json.loads(l) for l in FACTS.open()]
    wf = [f for f in facts if f.get("component") in wf_components]
    assert len(wf) > 100, (
        f"only {len(wf)} workflow-plane facts - the plane is blind again. "
        f"Before the profile rewrite this was 12."
    )
    # and the fb-worker specifically: it performs the automation, and its surface is a
    # Robot Framework keyword set that no generic extractor would ever find
    kinds = {f["kind"] for f in facts if f.get("component") == "fb-worker"}
    assert "RpaKeyword" in kinds, (
        "fb-worker contributes no RpaKeyword facts - the component that runs the "
        "automation is back to being invisible"
    )


def test_every_worker_mode_is_claimed_by_a_component():
    """A dispatched WORKER_MODE that no Component declares is an invisible role."""
    facts = [json.loads(l) for l in FACTS.open()]
    modes = {f["name"] for f in facts if f["kind"] == "WorkerRole"}
    declared = {c.get("worker_mode") for c in C.load()["Component"].values()
                if c.get("worker_mode")}
    orphan = modes - declared
    assert not orphan, (
        f"WORKER_MODE {sorted(orphan)} dispatched by the image but claimed by no "
        f"Component - the role is invisible to every query"
    )


def test_worker_roles_are_attributed_to_their_own_component():
    """The WORKER_MODE switch is one repo-level file, so whichever Component's glob holds
    it would otherwise be credited with every role in the image."""
    facts = [json.loads(l) for l in FACTS.open()]
    cat = C.load()["Component"]
    wrong = []
    for f in facts:
        if f["kind"] != "WorkerRole":
            continue
        owner = f.get("component")
        if cat.get(owner, {}).get("worker_mode") != f["name"]:
            wrong.append(f"{f['name']} attributed to {owner}")
    assert not wrong, "\n".join(f"  {w}" for w in wrong)


# --------------------------------------------------------------- the loop's properties
def test_reindex_is_idempotent():
    """Same commits in, byte-identical facts out.

    Not cosmetic: without it, running the loop on every push produces a diff every time
    and people stop reading the diffs.
    """
    before = hashlib.md5(FACTS.read_bytes()).hexdigest()
    res = S.run()
    after = hashlib.md5(FACTS.read_bytes()).hexdigest()
    assert res["new"] == 0, f"a no-change run extracted {res['new']} facts"
    assert before == after, "a no-change run rewrote graph/facts.jsonl"


def test_extraction_never_rewrites_authored_prose():
    """Cards are human-owned. The loop may only write graph/.

    The user authors Domains, roles and rationale; the machine extracts contracts. If a
    sync can touch knowledge/, an update silently destroys authored knowledge - which is
    the one thing that must never happen.
    """
    snap = {p: hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted((KB / "knowledge").rglob("*.md"))}
    snap.update({p: hashlib.md5(p.read_bytes()).hexdigest()
                 for p in sorted((KB / "catalog").rglob("*.yaml"))})
    S.run(force=True)
    changed = [str(p.relative_to(KB)) for p, h in snap.items()
               if hashlib.md5(p.read_bytes()).hexdigest() != h]
    assert not changed, f"sync modified authored files: {changed}"


def test_facts_carry_component_and_system():
    """Facts are keyed on Component now, not repo. A fact without one cannot be routed
    into the Domain/System hierarchy an LLM traverses."""
    facts = [json.loads(l) for l in FACTS.open()]
    missing = [f["id"] for f in facts if not f.get("component") or not f.get("system")][:5]
    assert not missing, f"facts with no component/system: {missing}"


def test_no_duplicate_fact_ids():
    """Component globs nest in this estate, and a naive match double-counts.

    wf-worker sits at `wre/engine/worker/**`, inside wf-engine's `wre/engine/**`, so every
    shared file was attributed to both roles - 12 facts duplicated. sync.owns() resolves it
    by most-specific glob. A failure here means two Components claim the same file.
    """
    import collections
    facts = [json.loads(l) for l in FACTS.open()]
    counts = collections.Counter(f["id"] for f in facts)
    dupes = {k: v for k, v in counts.items() if v > 1}
    if dupes:
        detail = []
        for fid in list(dupes)[:4]:
            owners = sorted({f["component"] for f in facts if f["id"] == fid})
            detail.append(f"  {fid[:64]} claimed by {owners}")
        raise AssertionError(f"{len(dupes)} duplicated fact ids:\n" + "\n".join(detail))


def test_component_globs_resolve_to_one_owner():
    """Every file in a multi-Component repo must have exactly one owner."""
    cat = C.load()
    byrepo = {}
    for n, c in cat["Component"].items():
        if c.get("unindexed"):
            continue
        byrepo.setdefault((c.get("source") or {}).get("repo"), []).append(n)
    for repo, comps in byrepo.items():
        if len(comps) < 2:
            continue
        # a path under the deepest glob must be owned by exactly that Component
        for n in comps:
            for g in (cat["Component"][n].get("source") or {}).get("paths") or []:
                probe = g.replace("/**", "/Probe.java")
                owners = [m for m in comps if S.owns(probe, m, cat, repo)]
                assert len(owners) == 1, (
                    f"{probe} in {repo} is owned by {owners} - globs overlap ambiguously"
                )




def test_plugin_is_prepared_for_install():
    """`claude plugin install` copies hooks/skills/commands and leaves the KB behind, so
    the copy needs a stamped path and an in-plugin launcher. Without them the install
    fails SILENTLY - no tools, no error - which is the worst of the three outcomes."""
    plug = KB / "plugin"
    pin = plug / "hooks" / "kb_root.txt"
    launcher = plug / "mcp_launch.sh"
    assert pin.is_file(), ("plugin/hooks/kb_root.txt missing - run "
                           "`python3 tools/prepare_plugin.py` before installing")
    # Shipped blank (it names one machine's path); `./e5 setup` stamps it on each machine.
    # Blank is the fresh-clone state, not a broken install.
    if pin.read_text().strip():
        assert Path(pin.read_text().strip()) == KB, (
            f"kb_root.txt points at {pin.read_text().strip()!r}, but this KB is {KB} - "
            f"the KB moved; re-run tools/prepare_plugin.py and reinstall")
    assert launcher.is_file(), "plugin/mcp_launch.sh missing - run prepare_plugin.py"
    import os
    assert os.access(launcher, os.X_OK), "plugin/mcp_launch.sh is not executable"
    import json as _j
    mcp = _j.loads((plug / ".claude-plugin" / "plugin.json").read_text())["mcpServers"]
    cmd = mcp["e5-platform-kb"]["command"]
    assert "/../" not in cmd, (
        f"plugin.json MCP command {cmd!r} reaches outside the plugin directory; that path "
        f"does not exist once the plugin is copied on install")


def test_hooks_do_not_assume_they_live_inside_the_kb():
    """Every hook must resolve the KB, not compute it from its own location."""
    bad = []
    for h in (KB / "plugin" / "hooks").glob("*.py"):
        if h.name == "e5_root.py":
            continue
        src = h.read_text()
        if "parents[2]" in src and "find_kb" not in src:
            bad.append(h.name)
    assert not bad, (f"{bad} derive the KB from __file__.parents - that breaks when the "
                     f"plugin is installed as a copy")





# CLI subcommands that are deliberately shell-only: operational one-offs, not things to
# run mid-conversation. Anything NOT listed here must have a slash command. Keeping the
# exception list rather than the inclusion list is the point - a new command defaults to
# "must be reachable from Claude Code" instead of being silently invisible.
SHELL_ONLY = {
    # Owner decision 2026-11-14: only the user-facing commands have slash commands
    # (/e5-init, /e5-sync, /e5-feature, /e5-bugfix, /e5-update-knowledge-base). Every
    # other subcommand is INTERNAL - called by the skills or by sync - and must not
    # appear in the / menu, because the old 14-command menu confused users.
    "kb",                                                      # navigation (MCP covers it)
    "routing", "flows", "cite", "estate", "patterns", "libs",  # gates run by sync/check
    "catalog", "probe",                                        # update-knowledge-base aids
    "codegraph", "layers", "extract",                          # extractors run by sync
    "checkouts",                  # platform/ checkouts: a step of `sync all` (/e5-sync)
    "workspace", "contest", "serve",                           # e5-implement, e5-correct, MCP
    "unlock", "lock", "check", "fresh",                        # audited / maintenance
    "setup", "update",            # run in a shell BEFORE/AROUND Claude Code: setup installs the
                                  # plugin itself, update replaces it (INSTALL.md)
}


def test_every_user_facing_cli_command_has_a_slash_equivalent():
    """Derived from the CLI, not hardcoded.

    The first version listed the five commands that existed at the time. `e5 creds` was
    added afterwards and the list was not, so the guard that existed specifically to catch
    "CLI command with no slash command" missed exactly that - and the user found it. A
    guard with a hardcoded inventory only ever catches the thing you already fixed.
    """
    import re
    cli = set(re.findall(r'cmd == "([a-z-]+)"', (KB / "e5").read_text()))
    assert cli, "could not parse any subcommand out of ./e5"
    slash = {p.stem.removeprefix("e5-") for p in (KB / "plugin" / "commands").glob("*.md")}
    missing = sorted(c for c in cli - SHELL_ONLY if c not in slash)
    assert not missing, (
        f"CLI has {missing} with no slash command - invisible inside Claude Code.\n"
        f"  Add plugin/commands/e5-<name>.md, or add it to SHELL_ONLY with a reason.")


def test_shell_only_list_has_no_dead_entries():
    """An exception list that outlives its command quietly stops protecting anything."""
    import re
    cli = set(re.findall(r'cmd == "([a-z-]+)"', (KB / "e5").read_text()))
    stale = sorted(SHELL_ONLY - cli)
    assert not stale, f"SHELL_ONLY names commands the CLI no longer has: {stale}"


def test_slash_commands_do_not_assume_the_kb_is_adjacent():
    """A command runs from the installed COPY, which does not contain the KB."""
    bad = []
    for p in (KB / "plugin" / "commands").glob("*.md"):
        s = p.read_text()
        if "python3 tools/" in s and "kb_root.txt" not in s and "E5_KB_ROOT" not in s:
            bad.append(p.name)
    assert not bad, (f"{bad} run `python3 tools/...` without saying how to locate the KB; "
                     f"from the installed copy there is no tools/ directory")


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            bad += 1
            print(f"FAIL  {fn.__name__}\n{e}")
        except Exception:
            bad += 1
            print(f"ERROR {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)


# ---------------------------------------------------------------------------
# owner / lifecycle
# ---------------------------------------------------------------------------
def test_owner_defaults_to_unassigned_rather_than_being_invented():
    """Making `owner` required would have failed the catalog for all 37 Components on
    the day it was added, and the usual response to that is a bulk commit of
    plausible-looking owners. An invented owner reads as accountability and answers to
    nobody, so the field defaults and the gap is reported as a warning."""
    import catalog as C
    cat = C.load()
    for name, c in cat["Component"].items():
        assert c.get("owner"), f"{name} has no owner key at all"
        assert c.get("lifecycle") in C.LIFECYCLES, f"{name}: {c.get('lifecycle')!r}"
    assert any("no owner" in w for w in C.warnings()), \
        "unowned Components must be reported, not silently defaulted"


def test_an_invalid_lifecycle_is_an_error_not_a_warning():
    import catalog as C
    assert "experimental" in C.LIFECYCLES and "nonsense" not in C.LIFECYCLES


# The Backstage descriptor export and its four tests were REMOVED on 2026-10-12.
#
# Backstage's entity model has a single classification axis - Component.spec.system ->
# System.spec.domain - which is structurally the thing this catalog stopped doing. Keeping
# an exporter for it meant keeping a Domain level alive with nothing but the exporter to
# justify it, and a derived view of a rejected model is not an exit door, it is a second
# model to keep correct. If a developer portal ever becomes a goal in its own right, this
# catalog plugs in as a custom entity provider and the mapping gets written then, against
# whatever the portal actually needs.



def test_registry_rewrite_keeps_a_trailing_comment_out_of_the_last_entry(tmp_path, monkeypatch):
    """`registry_update --apply` ended an entry only at a lowercase top-level key, so the
    column-0 comment documenting `binary_dependencies:` was swept into whichever repo was
    listed last, and the owned keys were re-emitted below it. It happened to
    variant-scaffolding, then to credential-distribution-service the moment it was
    appended. YAML still parsed; the file no longer said what its author wrote."""
    import registry_update as RU
    reg = tmp_path / "registry.yaml"
    reg.write_text(
        "repos:\n"
        "  - id: last-repo\n"
        "    # an indented note inside the entry - must stay inside it\n"
        "    role: subsystem\n"
        "    remote: https://github.com/element5inc/old.git\n"
        "\n"
        "# documents the block BELOW, not the repo above\n"
        "binary_dependencies:\n"
        "  - id: something\n")
    monkeypatch.setattr(RU, "REG", reg)
    changes = {"last-repo": ({}, {"remote": "https://github.com/element5inc/new.git",
                                  "branch": "release/v1.0.0", "status": "ok"})}
    RU.rewrite(changes, apply=True)
    out = reg.read_text().splitlines()
    c = out.index("# documents the block BELOW, not the repo above")
    assert out[c + 1] == "binary_dependencies:", "the comment drifted away from its block"
    entry = out[:c]
    assert "    branch: release/v1.0.0" in entry, "owned keys landed outside the entry"
    assert "    # an indented note inside the entry - must stay inside it" in entry
    first = reg.read_text()
    RU.rewrite(changes, apply=True)
    assert reg.read_text() == first, "a second apply changed the file"


def test_a_deployment_absent_from_the_snapshot_is_recorded_not_omitted():
    """CDS was onboarded after the qa snapshot and got no `deployed_in_qa` at all, so no
    card could inherit the gap. `resolve` now records NOT_OBSERVED - and NOT_OBSERVED is a
    sentinel. (The lifecycle proposer that misread it was removed on 2026-11-14.)"""
    import mirror as M
    assert M.NOT_OBSERVED == "not-observed"
    _, repos = M.registry()
    cds = next(r for r in repos if r["id"] == "credential-distribution-service")
    assert cds.get("deployment") and cds.get("deployed_in_qa") == M.NOT_OBSERVED
    for r in repos:
        if r.get("deployment"):
            assert r.get("deployed_in_qa"), f"{r['id']}: deployable but no deployed_in_qa"

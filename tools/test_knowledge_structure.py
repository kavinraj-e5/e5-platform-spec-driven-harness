"""Tests for the SHAPE of the knowledge base, not its contents.

The hierarchy (2026-11-14): 00-overview.md -> <system>/README.md -> <area>/README.md ->
component documents, plus cross-cutting flows/, contracts/, tables/. Nothing is found by
keyword search, so a document that is not linked from above is a document nobody reads,
and a dead link is a dead end. A structure nothing checks decays back into whatever is
convenient.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "release"))
import catalog as C            # noqa: E402
import gen_system_readmes as G  # noqa: E402

KB = HERE.parent
KNOW = KB / "knowledge"
SYSTEM_DIRS = G.DIRS
_LINK = re.compile(r'\]\((\.\.?/[^)#]+\.md)(?:#[^)]*)?\)')


def _cards():
    return [p for p in sorted(KNOW.rglob("*.md"))]


def test_every_relative_card_link_resolves():
    broken = []
    for p in _cards():
        for m in _LINK.finditer(p.read_text()):
            if not (p.parent / m.group(1)).resolve().is_file():
                broken.append(f"{p.relative_to(KB)} -> {m.group(1)}")
    assert not broken, f"{len(broken)} dead card links:\n  " + "\n  ".join(broken[:25])


def test_the_overview_exists_and_stays_small():
    """L0 is loaded on every session. Past ~2.5k tokens it starts costing more than it saves."""
    ov = KNOW / "00-overview.md"
    assert ov.is_file(), "knowledge/00-overview.md - the L0 document - is missing"
    assert len(ov.read_text()) // 4 <= 2500, "00-overview.md is over its 2.5k-token budget"


def test_every_path_the_overview_names_exists():
    """The overview is the map the model navigates by. A path in it that does not exist
    sends the model to a dead end on its first step."""
    body = (KNOW / "00-overview.md").read_text()
    missing = []
    for ref in re.findall(r'`([a-z0-9-]+(?:/[a-z0-9.-]+)*/?(?:\.md)?)`', body):
        if "/" not in ref and not ref.endswith(".md"):
            continue                                   # a name, not a path
        if not (KNOW / ref.rstrip("/")).exists():
            missing.append(ref)
    assert not missing, f"00-overview.md names paths that do not exist: {missing}"


def test_every_system_and_area_has_a_folder_and_a_readme():
    cat = C.load()
    for sysname, dirname in SYSTEM_DIRS.items():
        d = KNOW / dirname
        assert sysname in cat["System"], f"{dirname}/ has no System in the catalog"
        assert (d / "README.md").is_file(), f"{dirname}/README.md missing"
        for area in G.AREAS.get(sysname, {}):
            assert (d / area / "README.md").is_file(), f"{dirname}/{area}/README.md missing"


def test_every_area_member_is_a_catalog_component_of_that_system():
    cat = C.load()["Component"]
    for sysname, areas in G.AREAS.items():
        for area, (_, members, _) in areas.items():
            for m in members:
                assert m in cat, f"{area}: {m} is not in the catalog"
                assert cat[m].get("system") == sysname, \
                    f"{area}: {m} belongs to {cat[m].get('system')}, not {sysname}"


def test_no_system_folder_holds_a_card_for_another_systems_component():
    owner = {n: c.get("system") for n, c in C.load()["Component"].items()}
    misfiled = []
    for sysname, dirname in SYSTEM_DIRS.items():
        for p in (KNOW / dirname).rglob("*.md"):
            if p.stem in owner and owner[p.stem] != sysname:
                misfiled.append(f"{dirname}/{p.relative_to(KNOW / dirname)} but "
                                f"{p.stem} is in {owner[p.stem]}")
    assert not misfiled, "cards filed under the wrong system:\n  " + "\n  ".join(misfiled)


def test_the_retired_layout_is_gone():
    """Numbered folders, `_reference`, `_platform`, the word-match routing table and the
    unverified disagreement pile must not come back."""
    for stale in ("01-workflow-development", "02-app-development", "03-workflow-interactions",
                  "04-reporting", "05-communication", "06-platform-delivery",
                  "_reference", "_platform", "routing-table.yaml"):
        assert not (KNOW / stale).exists(), f"knowledge/{stale} is back"
    assert not (KB / "disagreements").exists(), "disagreements/ is back - use known-issues/"


def test_unreleased_designs_live_only_in_in_flight():
    leaked = []
    for p in _cards():
        if "in-flight" in p.relative_to(KNOW).parts:
            continue
        head = p.read_text()[:600]
        if re.search(r'^state:\s*in-flight', head, re.M):
            leaked.append(str(p.relative_to(KNOW)))
    assert not leaked, f"in-flight designs outside knowledge/in-flight/: {leaked}"


def test_every_known_issue_has_a_rule_that_re_verifies_it():
    import patterns as P
    ruled = {r["card"] for r in P.rules() if r.get("card")}
    for p in (KNOW / "known-issues").glob("*.md"):
        rel = p.relative_to(KB).as_posix()
        assert rel in ruled, f"{rel} has no semgrep rule; it cannot be re-verified"


def test_library_cards_sit_in_their_area():
    for comp, area in (("platform-core", "workflow-development/workflow-library"),
                       ("rf-fb-worker-core", "app-development/app-library")):
        hits = list(KNOW.rglob(f"{comp}.md"))
        assert len(hits) == 1, f"{comp}.md: {len(hits)} copies"
        assert hits[0].parent == KNOW / area, f"{comp}.md is at {hits[0].relative_to(KNOW)}"

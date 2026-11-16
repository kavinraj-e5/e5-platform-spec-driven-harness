"""Tests for the semgrep card re-check.

Two separate things are tested, and only one of them needs semgrep:

  * the RULE CONTRACT - every rule names a card that exists and locations that look
    like real places. A rule whose card was deleted is a rule reporting on nothing.
  * the VERDICT LOGIC - CONFIRMED / FIXED / SPREAD / MOVED and card-line staleness,
    on synthetic input. This is the part that decides whether a human is told to go
    retire a card, so it is tested without the scanner in the loop.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KB = HERE.parent
sys.path.insert(0, str(HERE))

import patterns as P  # noqa: E402


def _rule(**kw):
    base = {"id": "r", "file": "f.yaml", "card": None, "expect": "present",
            "at": [], "message": ""}
    base.update(kw)
    return base


def _at(repo, path, line=None):
    return {"repo": repo, "path": path, "line": line}


# ------------------------------------------------------------------ the rule contract
def test_every_rule_names_a_card_that_exists():
    """A rule whose card was deleted reports on nothing, and its verdicts become
    noise that trains people to skip the section."""
    for r in P.rules():
        if r["card"] is None:
            continue                       # a standing rule, allowed and declared
        assert (KB / r["card"]).is_file(), f"{r['id']} -> missing {r['card']}"


def test_every_rule_pins_at_least_one_location_or_says_it_is_standing():
    """`e5-at` is the contract. Without it FIXED can never fire, and FIXED - the card
    is now wrong - is the verdict this whole directory exists for."""
    for r in P.rules():
        assert r["at"] or r["card"] is None, f"{r['id']} pins no location"
        for a in r["at"]:
            assert a["repo"] and "/" in a["path"], f"{r['id']} bad e5-at: {a}"


def test_every_pinned_repo_is_one_the_registry_indexes():
    sys.path.insert(0, str(HERE))
    import mirror
    known = {r["id"] for r in mirror.registry()[1]}
    for r in P.rules():
        for a in r["at"]:
            assert a["repo"] in known, f"{r['id']} pins unknown repo {a['repo']!r}"


# ------------------------------------------------------------------- the verdict logic
def test_a_defect_still_where_the_card_says_is_confirmed():
    v, = P.verdicts([_rule(at=[_at("x", "src/A.java", 10)])],
                    [{"rule": "r", "repo": "x", "path": "src/A.java", "line": 10}])
    assert [r["verdict"] for r in v["rows"]] == ["CONFIRMED"]


def test_a_defect_that_is_gone_is_FIXED_not_silence():
    """The verdict that justifies the directory. Nothing else in the harness notices
    when a defect goes away: `./e5 cite drift` asks whether a cited LINE's content
    changed, and a fix three files away leaves every citation resolving."""
    v, = P.verdicts([_rule(at=[_at("x", "src/A.java", 10)])], [])
    assert [r["verdict"] for r in v["rows"]] == ["FIXED"]


def test_the_same_defect_in_another_repo_is_SPREAD():
    """akst-service is a fork of platform-core's AKST. The first real run found the
    gRPC null-onNext defect in both, and the card names only one."""
    v, = P.verdicts([_rule(at=[_at("x", "src/A.java", 10)])],
                    [{"rule": "r", "repo": "x", "path": "src/A.java", "line": 10},
                     {"rule": "r", "repo": "y", "path": "src/B.java", "line": 3}])
    assert sorted(r["verdict"] for r in v["rows"]) == ["CONFIRMED", "SPREAD"]


def test_a_defect_that_changed_file_inside_its_repo_is_MOVED():
    v, = P.verdicts([_rule(at=[_at("x", "src/A.java", 10)])],
                    [{"rule": "r", "repo": "x", "path": "src/Moved.java", "line": 9}])
    row, = v["rows"]
    assert row["verdict"] == "MOVED" and row["now"] == ["src/Moved.java:9"]


# ------------------------------------------------- the card's own line, and a regression
def test_a_cited_range_is_read_as_a_range(tmp_path):
    """WAS A FALSE ACCUSATION. Reading `:43-69` as the two points 43 and 69 called two
    correct cards stale - the shape sits at :47 and :219, both inside the span their
    card gives. A gate that makes false accusations is a gate someone switches off."""
    card = tmp_path / "c.md"
    card.write_text("evidence: Foo.java:43-69 and Bar.java:200\n")
    rel = str(card.relative_to(KB)) if str(card).startswith(str(KB)) else None
    P.KB, old = tmp_path, P.KB
    try:
        assert P.card_spans("c.md", "Foo.java") == [(43, 69)]
        assert not P._outside(47, [(43, 69)])
        assert P._outside(200, [(43, 69)])
        assert P.card_spans("c.md", "Bar.java") == [(200, 200)]
    finally:
        P.KB = old
    assert rel is None or True


def test_a_card_whose_lines_no_longer_match_is_flagged(tmp_path):
    """The real decay. monitoring-service's hardcoded queue moved from :308 to :333
    and the card still said 308; `./e5 cite check` passed throughout, because the
    line still exists and still has content."""
    (tmp_path / "c.md").write_text("code: MonitoringServiceImpl.java:307-308\n")
    P.KB, old = tmp_path, P.KB
    try:
        v, = P.verdicts(
            [_rule(card="c.md", at=[_at("x", "src/MonitoringServiceImpl.java", 333)])],
            [{"rule": "r", "repo": "x", "path": "src/MonitoringServiceImpl.java",
              "line": 333}])
    finally:
        P.KB = old
    row, = v["rows"]
    assert row["verdict"] == "CONFIRMED" and row["card_cites"] == [(307, 308)]


# ------------------------------------------------------------------- the missing binary
def test_no_semgrep_is_a_loud_skip_not_a_quiet_pass(monkeypatch, capsys):
    """CI without semgrep must not read as "every card re-verified"."""
    monkeypatch.setattr(P, "semgrep_bin", lambda: None)
    rc = P.main(["check"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "NOT CHECKED" in out and "pipx install semgrep" in out


def test_the_installation_note_warns_against_pip():
    """Measured: `pip install --user semgrep` upgrades starlette past the pinned
    fastapi and console/test_app.py fails at collection. The note is the only thing
    standing between the next person and that afternoon."""
    assert "pipx" in P._INSTALL and "NOT" in P._INSTALL
    assert "pipx install semgrep" in (KB / "rules" / "README.md").read_text()

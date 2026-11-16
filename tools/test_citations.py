"""Tests for the evidence gate.

The parsing and attribution tests use literal strings, so a regression there fails on
the parser rather than on a repo that moved. The estate tests run the real scan, because
the claim the tool makes is a claim about the real knowledge base.

Several tests below exist because the FIRST version of this gate got the answer
confidently wrong - it reported correct citations as broken. Each of those is marked.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import citations as X                 # noqa: E402

KB = HERE.parent
_SCAN = None


def scan():
    global _SCAN
    if _SCAN is None:
        _SCAN = X.scan()
    return _SCAN


# ------------------------------------------------------------------- parsing
def test_the_regex_does_not_match_a_version_or_a_port_or_a_field_tag():
    """`v1.2.3`, `:50051` and `string name = 2;` are not citations. An extension list
    that is open turns all three into evidence and the coverage number becomes a lie."""
    for s in ("release/v1.2.3", "endpoint :50051 com.e5.Service", "int32 id = 2;",
              "at 10:30 UTC", "see task.protocol:4"):
        assert not X._CITE.findall(s), f"false citation in {s!r}"
    assert X._CITE.findall("TaskController.java:113") == [("TaskController.java",
                                                           "113", "")]
    assert X._CITE.findall("a/b/C.py:12-30") == [("a/b/C.py", "12", "30")]


def test_a_bare_line_reference_inherits_the_last_named_file(tmp=None):
    """`(`:1345-1465`)` means "same file as a moment ago" - 843 of the knowledge base's
    2,471 references are written that way."""
    p = KB / "graph" / "_t_cite.md"
    p.write_text("see `deployment_management.proto:8-12` and also `:1345-1465`\n")
    try:
        cs = X.extract(p)
    finally:
        p.unlink()
    assert len(cs) == 2
    assert cs[1].path == "deployment_management.proto"
    assert (cs[1].start, cs[1].end) == (1345, 1465)
    assert cs[1].inherited and not cs[0].inherited


def test_an_inherited_reference_can_never_raise_an_error():
    """`deployment-mgmt-grpc-api.md` names a 1,466-line proto, then a Main.java, then
    goes on quoting proto line numbers - so "the last file named" is not reliably the
    file a bare `:1345` continues from. Scoring those as errors would have produced 189
    accusations against cards that are very likely right. They get their own vocabulary
    and stay out of the headline instead."""
    bad = [c for c in scan() if c.inherited and c.status in X.E_CLASSES]
    assert not bad, f"{len(bad)} inherited references leaked into the error classes"
    assert all(c.status.startswith("I_") for c in scan() if c.inherited)


def test_the_verified_count_excludes_continuation_references():
    """A number that mixes checked evidence with plausible guesses is worse than a
    smaller honest one."""
    out = subprocess.run([sys.executable, str(HERE / "citations.py"), "report"],
                         capture_output=True, text=True).stdout
    m = re.search(r'(\d+) qualified citations', out)
    assert m, out
    qualified = sum(1 for c in scan() if not c.inherited)
    assert int(m.group(1)) == qualified
    assert "never counted as verified" in out


def test_tidy_drops_elided_segments():
    """Cards write `e5-ss-dynamic-scaling/.../dto/Request.java`."""
    assert X.tidy("a/.../b/C.java") == "a/b/C.java"


def test_split_repo_prefix_takes_the_last_matching_segment():
    alias = {"interface": "interface", "workflow-ops": "workflow-ops"}
    assert X.split_repo_prefix("ws/sub/workflow-ops/src/x.py", alias) == (
        "workflow-ops", "src/x.py")
    assert X.split_repo_prefix("src/x.py", alias) == (None, "src/x.py")


# --------------------------------------------------------------- attribution
def test_the_checkout_directory_name_is_an_alias():
    """event-store's directory is spelled `event-strore`, and three cards quote
    workspace paths that contain it. That is not a typo in the cards - it is what the
    directory is called - and treating it as unknown reported eight correct citations
    as broken."""
    _by_id, alias = X._registry_index()
    assert alias.get("event-strore") == "event-store"


def test_a_kb_directory_name_that_is_also_a_repo_directory_resolves_to_the_repo():
    """WAS WRONG ONCE. `schemas/` is a directory here and also inside monitoring-service.
    Matching on the name alone sent that card's citation into this repository's tree and
    reported a correct citation as broken."""
    c = X.Cite("x.md", "schemas/TaskManagerConfig.schema.json:28", 1,
               "schemas/TaskManagerConfig.schema.json", 28, None)
    X.attribute(c, {})
    assert c.repo != "__kb__", "a name match alone must not claim a KB-local file"
    here = X.Cite("x.md", "tools/citations.py:1", 1, "tools/citations.py", 1, None)
    X.attribute(here, {})
    assert here.repo == "__kb__", "a file that IS here must resolve here"


def test_the_line_number_disambiguates_two_files_with_the_same_basename():
    """WAS WRONG ONCE. Two repos have a `Constants.java`; deployment-mgmt's is 17 lines
    and the citation says 72. Taking the card's subject and ignoring the number reported
    event-processor's correct citation as off the end of a file it never meant."""
    hit = [c for c in scan()
           if c.card.endswith("deployment-mgmt-webhook-rpcs.md")
           and c.raw.startswith("Constants.java:91")]   # moved from :72 with the code, 2026-11-14
    assert hit, "the Constants.java:91 citation disappeared - retarget this test"
    assert hit[0].status == "OK" and hit[0].repo == "event-processor"


def test_the_repo_named_nearest_the_citation_wins_over_the_card_subject():
    """WAS WRONG ONCE. "Producers emit ... (webhook-service `AuditEventData.java:71-73`)"
    in a card about audit-service. Both repos have that class; the card says which, in
    the sentence. Ignoring the sentence produced a false accusation."""
    # Retargeted 2026-11-14 (the original card was archived): a card about cds cites
    # "(dynamic-scaling `release/v1.3.2` `algorithm/MinimalPodScheduler.java:259`)".
    hit = [c for c in scan()
           if c.card.endswith("subsystems/cds.md")
           and c.raw.endswith("MinimalPodScheduler.java:259")]
    assert hit, "the MinimalPodScheduler citation disappeared - retarget this test"
    assert hit[0].status == "OK" and hit[0].repo == "dynamic-scaling"


def test_ambiguity_is_reported_and_never_guessed():
    """A basename many repos share, in a card that names none of them, must stay
    unresolved. Verified and unverifiable may not look the same."""
    amb = [c for c in scan() if c.status == "W_AMBIGUOUS"]
    assert amb, "no ambiguous citations at all - the detector has probably stopped"
    for c in amb:
        assert c.anchor is None, f"{c.key} was anchored despite being ambiguous"


def test_a_citation_into_an_unindexed_repo_is_not_called_broken():
    """"We never looked" must not be reported as "we looked and it was wrong"."""
    un = [c for c in scan() if c.status in ("W_UNINDEXED_REPO", "W_NO_MATCH")]
    assert all(c.status not in X.E_CLASSES for c in un)
    names = X._unindexed_names()
    assert "webhook-dispatcher" in names, (
        "the catalog no longer declares webhook-dispatcher; three cards cite files in "
        "that Lambda and nothing else records that it exists")


# ------------------------------------------------------------------ the gate
def test_the_knowledge_base_cites_nothing_broken():
    """The regression this module exists for. A card may not point at a file that is not
    there, or at a line past the end of one."""
    errs = [c for c in scan() if c.status in X.E_CLASSES]
    assert not errs, "\n".join(f"{c.card}:{c.line_no} {c.raw} - {c.detail}"
                               for c in errs[:20])


def test_coverage_does_not_regress():
    """A change that makes the gate quieter by checking less is a regression, and it
    looks exactly like an improvement in the error count."""
    qual = [c for c in scan() if not c.inherited]
    ok = sum(1 for c in qual if c.status == "OK")
    assert ok / len(qual) >= 0.90, f"only {ok}/{len(qual)} citations resolve"


def test_every_verified_citation_is_anchored():
    anchors = json.loads((KB / "graph" / "citation-anchors.json").read_text())["anchors"]
    want = {c.key for c in scan()
            if c.status == "OK" and not c.inherited and c.repo != "__kb__"}
    missing = want - set(anchors)
    assert not missing, (f"{len(missing)} verified citations have no anchor - run "
                         f"`e5 cite bless`; the first is {sorted(missing)[0]}")


def test_anchors_carry_the_line_text_and_not_only_the_number():
    """The common rot is an insertion above: the line number still exists, still has
    content, and now points at something else. A count cannot see that."""
    anchors = json.loads((KB / "graph" / "citation-anchors.json").read_text())["anchors"]
    a = next(iter(anchors.values()))
    assert set(a) >= {"repo", "ref", "path", "line", "sha"}
    assert re.fullmatch(r'[0-9a-f]{12}', a["sha"])


# --------------------------------------------------------------------- drift
def test_drift_fires_when_an_anchored_line_changes(monkeypatch=None):
    """Hand-built anchors with a sha that cannot match: drift must name the card."""
    anchors = {"anchors": {
        "k#x.java:1": {"card": "knowledge/fake.md", "repo": "interface",
                       "ref": "0000000", "path": "build.gradle", "line": 1,
                       "sha": "deadbeefcafe"}}}
    real = X._registry_index
    by_id, alias = real()
    X._registry_index = lambda: (by_id, alias)
    try:
        moved = X.drift(anchors)
    finally:
        X._registry_index = real
    assert len(moved) == 1, moved
    assert moved[0]["card"] == "knowledge/fake.md"
    assert "now reads" in moved[0]["why"] or "no longer" in moved[0]["why"]


def test_drift_is_silent_while_the_registry_has_not_moved():
    """Drift answers "what did the sync invalidate". Before a sync the answer is
    nothing, and a gate that cries every run is a gate someone turns off.

    The title states a precondition the body used to skip: after a sync, drift is SUPPOSED
    to be non-empty - it is the list of cards to re-read - and this test went red on the
    correct state. It now binds whenever no anchored repo has moved, and otherwise reports
    the size of the worklist. It binds again once the drifted cards are re-read and
    `cite bless` re-anchors them at the new commits."""
    import pytest
    anchors = json.loads((KB / "graph" / "citation-anchors.json").read_text())
    by_id, _ = X._registry_index()
    moved = {a["repo"] for a in anchors.get("anchors", {}).values()
             if a["repo"] != "__kb__" and X._current_ref(a["repo"], by_id) not in (None, a["ref"])}
    if moved:
        d = X.drift(anchors)
        pytest.skip(f"registry moved for {len(moved)} anchored repos since the last bless: "
                    f"{len(d)} cited lines in {len({m['card'] for m in d})} cards await "
                    f"re-reading - `e5 cite drift` lists them")
    assert X.drift(anchors) == []


# ------------------------------------------------------------------- wiring
def test_the_gate_runs_in_e5_check_and_in_ci():
    e5 = (KB / "e5").read_text()
    assert '("citations", "citations.py")' in e5, "`e5 check` does not run the gate"
    assert 'cmd == "cite"' in e5
    ci = (KB / "ci" / "jenkins-kb-index.groovy").read_text()
    assert "citations.py" in ci, "CI does not run the citation gate"


def test_the_gate_is_blocking_in_e5_check():
    """Advisory gates describe the estate; this one describes the knowledge base, which
    is the thing this repository is responsible for. A broken citation here is our bug."""
    e5 = (KB / "e5").read_text()
    advisory = re.search(r'if r and name not in \(([^)]*)\)', e5)
    assert advisory, "could not find the advisory list in ./e5"
    assert "citations" not in advisory.group(1)


# ------------------------------------------------------------------- graph






if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn()
            print("PASS ", fn.__name__)
        except Exception:
            bad += 1
            print("FAIL ", fn.__name__)
            traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)


class _C:
    """The attributes bless_new_only reads from a Cite, and nothing else."""
    def __init__(self, card, raw, line_no, anchor):
        self.card, self.raw, self.line_no, self.anchor = card, raw, line_no, anchor
        self.status, self.repo, self.inherited = "OK", "r", False
        self.ref, self.resolved, self.start = "new-commit", "p.java", 10

    @property
    def key(self):
        return f"{self.card}#{self.raw}@{self.line_no}"


def test_bless_new_only_never_reanchors_a_citation_whose_card_merely_shifted():
    """A key carries the citation's LINE IN THE CARD, so a note added at the top gives every
    citation below it a new key. Plain `bless` then anchors them at today's content - on
    2026-11-13 that would have re-verified 135 drifted, unread lines and erased them from
    the drift worklist. `--new-only` must carry the original anchor forward instead."""
    old = {"anchors": {"c.md#p.java:10@5": {"card": "c.md", "sha": "OLD", "ref": "old-commit"}}}
    shifted = _C("c.md", "p.java:10", 12, anchor="NEW")           # same citation, 7 lines down
    data, st = X.bless_new_only([shifted], old)
    assert data["anchors"]["c.md#p.java:10@12"]["sha"] == "OLD", "re-anchored at today's content"
    assert st["carried"] == 1 and st["new"] == 0


def test_bless_new_only_carries_the_old_anchor_to_a_duplicate_inserted_above():
    """A dated note citing the same line ABOVE the original: line order cannot tell which
    occurrence is new. The first draft gave the old anchor to the note and anchored the
    original fresh, which silently removed it from drift. Both must inherit the old one."""
    old = {"anchors": {"c.md#p.java:10@20": {"card": "c.md", "sha": "OLD", "ref": "old-commit"}}}
    note, original = _C("c.md", "p.java:10", 3, "NEW"), _C("c.md", "p.java:10", 27, "NEW")
    data, _ = X.bless_new_only([note, original], old)
    assert {r["sha"] for r in data["anchors"].values()} == {"OLD"}


def test_bless_new_only_anchors_text_that_was_never_anchored():
    data, st = X.bless_new_only([_C("c.md", "q.java:4", 9, "NEW")], {"anchors": {}})
    assert data["anchors"]["c.md#q.java:4@9"]["sha"] == "NEW" and st["new"] == 1

#!/usr/bin/env python3
"""The gate that was missing. Without this test, fixing the 22 cards guarantees a repeat.

The original defect was not a reasoning error. Every card involved pinned its claim to a
commit, in the sentence, with grep evidence - exactly as the discipline requires. What was
absent was anything that checked the pinned commit was still the one the KB indexes. So the
registry was re-pointed, the facts re-indexed, and the prose silently kept describing an
abandoned tree while continuing to look rigorous.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mirror as M
from check_card_provenance import check, frontmatter

KB = Path(__file__).resolve().parent.parent


def test_card_provenance_matches_registry():
    """No card may cite a commit the registry does not index."""
    errs, _warns, checked, _skipped = check()
    assert checked > 0, "no cards carry provenance - the gate would pass vacuously"
    assert not errs, "cards disagree with the registry:\n" + "\n".join(
        f"  {code} {rel}: {msg}" for rel, code, msg in errs)


def test_registry_indexes_only_release_branches():
    """`main` is not 'latest' in this estate; the integration convention is release/vX.Y.Z.
    A repo with no release branch must be labelled, not quietly indexed as though normal."""
    _root, repos = M.registry()
    bad = [r["id"] for r in repos
           if not re.match(r'^release/v\d', r.get("branch", ""))
           and r.get("status") != "no-release-branch"]
    assert not bad, f"indexed off a non-release branch without a status label: {bad}"




def test_every_card_commit_resolves_in_its_mirror():
    """A commit that no longer exists on the remote cannot be re-verified, so a card citing
    one is unfalsifiable - the failure mode this whole pass exists to remove."""
    import subprocess
    _root, repos = M.registry()
    by_repo = {}
    for r in repos:
        by_repo[r["id"]] = r
        n = re.search(r'/([^/]+?)(?:\.git)?$', r.get("remote", ""))
        if n:
            by_repo[n.group(1)] = r

    unresolved = []
    for p in sorted((KB / "knowledge").rglob("*.md")):
        if p.name.startswith("_TEMPLATE"):
            continue
        fm = frontmatter(p)
        if not fm.get("commit"):
            continue
        reg = by_repo.get(fm.get("repo") or fm.get("subsystem") or "")
        if not reg:
            continue
        bare = M.CACHE / (reg["id"] + ".git")
        if not bare.is_dir():
            continue    # mirror not synced in this environment; check() still covers the match
        rc = subprocess.run(["git", "-C", str(bare), "cat-file", "-e", fm["commit"] + "^{commit}"],
                            capture_output=True).returncode
        if rc:
            unresolved.append(f"{p.relative_to(KB)} cites {fm['commit']}, absent from the mirror")
    assert not unresolved, "\n".join(unresolved)


if __name__ == "__main__":
    for fn in (test_card_provenance_matches_registry,
               test_no_credentials_persisted_in_mirror_configs,
               test_no_secrets_in_kb_artifacts,
               test_registry_indexes_only_release_branches,
               test_facts_were_indexed_from_the_mirror,
               test_every_card_commit_resolves_in_its_mirror):
        fn()
        print(f"PASS {fn.__name__}")


def test_no_credentials_persisted_in_mirror_configs():
    """`git clone --mirror <authed-url>` writes the token into .git/config.

    This actually happened during the mirror build: a PAT ended up in 22 mirror configs.
    The KB artifacts were clean, which is exactly why it went unnoticed - a mirror cache
    is not somewhere anyone thinks to grep for secrets. Credentials are attached per
    fetch and never stored.
    """
    bad = M.audit_origins()
    assert not bad, (
        f"{len(bad)} mirrors embed credentials in their origin URL: {bad}\n"
        f"  fix: python3 -c \"import sys;sys.path.insert(0,'tools');import mirror as M;"
        f"[M.scrub_origin(M.CACHE/(r+'.git'), '?') for r in {bad}]\" "
        f"or re-run tools/mirror.py sync")


def test_no_secrets_in_kb_artifacts():
    """Nothing the KB publishes may contain a token or an inline-auth URL."""
    pat = re.compile(r'gh[ps]_[A-Za-z0-9]{20,}|://[^/\s"\']+:[^@/\s"\']+@')
    hits = []
    for sub in ("registry.yaml", "graph", "knowledge", "disagreements", "constitution", "ci"):
        t = KB / sub
        files = [t] if t.is_file() else (list(t.rglob("*")) if t.is_dir() else [])
        for f in files:
            if not f.is_file():
                continue
            try:
                txt = f.read_text(errors="replace")
            except Exception:
                continue
            if pat.search(txt):
                hits.append(str(f.relative_to(KB)))
    assert not hits, f"secret-shaped strings in published KB artifacts: {hits}"


def test_restamping_a_moved_card_never_marks_its_prose_verified():
    """sync_card_provenance bumped `verified` to today on every card it re-stamped, so
    the W1 staleness warning went quiet for bodies nobody had read. `verified` is the date
    of the last HUMAN review; a metadata re-stamp is not one."""
    src = (Path(__file__).resolve().parent / "sync_card_provenance.py").read_text()
    body = src[src.index("def main"):]
    assert 'set_key(head, "verified"' not in body, "the re-stamp is bumping `verified` again"
    assert "prose_reviewed_at_commit" in body, "the re-stamp no longer marks unread prose"


def test_a_card_whose_prose_was_read_at_another_commit_is_reported():
    """`prose_reviewed_at_commit` was written by the re-stamp and read by no gate, so a
    moved card's unread body left no trace once E1 cleared. W2 is that trace."""
    import check_card_provenance as CP
    errs, warns, checked, _ = CP.check()
    by_card = {}
    for rel, code, msg in warns:
        by_card.setdefault(str(rel), set()).add(code)
    for p in sorted((KB / "knowledge").rglob("*.md")):
        fm = CP.frontmatter(p)
        prc, cmt = fm.get("prose_reviewed_at_commit"), fm.get("commit")
        if prc and cmt and prc != cmt:
            rel = str(p.relative_to(KB))
            assert "W2" in by_card.get(rel, set()), f"{rel}: unread prose is not reported"

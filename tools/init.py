#!/usr/bin/env python3
"""`e5 init` - is this machine ready to sync? Credentials, tools, catalog. Changes nothing.

  python3 tools/init.py [--no-prompt]

The order a teammate follows: `./e5 setup` once, fill in ~/.e5/credentials.md, `/e5-init`
(this), then `/e5-sync`, which clones every platform repo into platform/<system>/, compiles,
runs the extractors and pyright, and rebuilds the knowledge base. init itself clones nothing:
a check that sometimes downloads a gigabyte is not a check.

WHY VERIFY BOTH CREDENTIALS UP FRONT
------------------------------------
A bad JFrog token must fail here, in two seconds, with its own name on the error - not
thirty minutes later inside a Gradle build where it looks like a dependency problem. Git
and JFrog are checked separately and reported separately for exactly that reason.

CREDENTIALS ARE NEVER PERSISTED BY THIS TOOL
--------------------------------------------
`git clone <authed-url>` writes the token into .git/config. That happened once in this
workspace and had to be scrubbed from 22 mirrors, so every run re-checks every mirror origin
and the check is also a test.
"""
from __future__ import annotations
import argparse, os, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import credentials as CRED
import mirror as M
import catalog as C

KB = HERE.parent
OK, BAD, WARN = "  ok   ", "  FAIL ", "  warn "


def check_git() -> bool:
    src = CRED.available()
    if not src.get("github_source"):
        print(f"{BAD}github: no credential found "
              f"- fill in {CRED.CRED_FILE} (see templates/credentials.md)")
        return False
    print(f"{OK}github credential present  [{src['github_source']}]")
    try:
        branches = M.ls_release_branches("element5inc/e5-platform-ss-interface")
        print(f"{OK}github reachable           ({len(branches)} release branches on a probe repo)")
        return True
    except Exception as e:
        print(f"{BAD}github unreachable: {CRED.mask(str(e))[:110]}")
        return False


def check_jfrog() -> bool:
    av = CRED.jfrog_available()
    if not av["present"]:
        print(f"{BAD}jfrog: no credential found. Needed for BUILDS (gradle/docker) and "
              f"for reading the published contracts.")
        return False
    print(f"{OK}jfrog credential present   [{av['jfrog_source']}]")
    try:
        import artifacts as A
        n = len(A.search_gavc("e5.functionblock", "AppSDK"))
        print(f"{OK}jfrog reachable            (AppSDK: {n} versions visible)")
        return True
    except Exception as e:
        print(f"{BAD}jfrog unreachable: {CRED.mask(str(e))[:110]}")
        return False


def check_mirror_hygiene() -> bool:
    bad = M.audit_origins()
    if bad:
        print(f"{BAD}{len(bad)} mirrors embed credentials in their origin URL: {bad}")
        print("       fix: python3 tools/mirror.py sync   (re-points origin to the plain URL)")
        return False
    n = len(list(M.CACHE.glob("*.git"))) if M.CACHE.is_dir() else 0
    print(f"{OK}no credentials in any of {n} mirror origins")
    return True


def check_branches() -> bool:
    """Count honestly.

    This used to print "all 21 repos resolve to a release/vX.Y.Z branch" while silently
    excluding the one that does not, because it carried status: no-release-branch. The
    session hook meanwhile reported "1 repo not current", so the harness contradicted
    itself in two places on the same run - which is worse than either message alone.
    """
    _root, repos = M.registry()
    unexpected = [r["id"] for r in repos
                  if not r.get("branch", "").startswith("release/v")
                  and r.get("status") != "no-release-branch"]
    declared = [r["id"] for r in repos if r.get("status") == "no-release-branch"]
    ok_n = len(repos) - len(unexpected) - len(declared)

    if unexpected:
        print(f"{BAD}{len(unexpected)} repos are off a release branch unexpectedly: "
              f"{unexpected}")
        print("       `main` is a different line of development here, not 'latest'")
    print(f"{OK}{ok_n} of {len(repos)} repos on a release/vX.Y.Z branch")
    if declared:
        print(f"{WARN}{len(declared)} {'has' if len(declared) == 1 else 'have'} no release "
              f"branch at all and {'is' if len(declared) == 1 else 'are'} labelled as "
              f"such: {declared} - facts from it are not current platform behaviour")
    return not unexpected


def check_catalog() -> bool:
    errs = C.check()
    if errs:
        print(f"{BAD}catalog invalid ({len(errs)} errors) - run "
              f"`python3 tools/catalog.py check`")
        return False
    cat = C.load()
    comps = cat["Component"]
    # `repos` previously counted the UNKNOWN and EXTERNAL placeholders alongside real
    # ones, so it read 23 against 21 mirrors and looked like two repos were missing.
    real = {(c.get("source") or {}).get("repo") for c in comps.values()
            if not c.get("unindexed") and c.get("indexed_from") != "artifact"}
    real.discard(None)
    # artifact-only = no source repo at all (2026-11-14: the SDKs turned out to have repos)
    from_artifact = [n for n, c in comps.items() if c.get("indexed_from") == "artifact"
                     and (c.get("source") or {}).get("repo") in (None, "UNKNOWN")]
    unindexed = [n for n, c in comps.items() if c.get("unindexed")]
    print(f"{OK}catalog valid              ({len(comps)} components, "
          f"{len(cat.get('System', {}))} systems, {len(real)} source repos)")
    if from_artifact:
        print(f"       {len(from_artifact)} from published artifacts (no repo exists): "
              f"{', '.join(sorted(from_artifact))}")
    if unindexed:
        print(f"       {len(unindexed)} declared but NOT indexed: "
              f"{', '.join(sorted(unindexed))}")
    return True


def check_toolchain() -> dict:
    """Advisory. git is required; each other tool gates one sync step, named here."""
    import lsp_client
    need = {"git": "mirrors and checkouts (required)",
            "java": "compiling the JVM repos; reading published artifacts (javap)",
            "gradle": "compiling repos that ship no gradle wrapper",
            "pyright-langserver": "the Python call graph (workflow-ops, rf-fb-worker-core, "
                                  "task-sdk-python, devops-pipeline)",
            "semgrep": "re-checking the known issues"}
    out = {}
    for tool, why in need.items():
        have = (lsp_client.find_pyright() if tool == "pyright-langserver"
                else shutil.which(tool)) is not None
        out[tool] = have
        print(f"{OK if have else WARN}{tool:<19} {'present' if have else 'MISSING':<8} - {why}")
    if not out["pyright-langserver"]:
        print("       install: pipx install pyright   (or npm i -g pyright)")
    return out


# ===========================================================================
# CHANGING CREDENTIALS: edit the markdown file and re-run init. There is no prompt and no
# flag that takes a secret (owner decision 2026-11-14): `ps` shows arguments to every user
# on the box, and anything typed into a chat lands in its transcript.
# ===========================================================================


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-prompt", action="store_true",
                    help="never ask anything (for CI)")
    a = ap.parse_args()

    print("e5 init — checking what the harness needs\n")
    print("CREDENTIALS")
    git_ok = check_git()
    jfrog_ok = check_jfrog()
    print("\nHYGIENE")
    hyg_ok = check_mirror_hygiene()
    print("\nSOURCES")
    br_ok = check_branches()
    cat_ok = check_catalog()
    print("\nTOOLCHAIN (advisory)")
    check_toolchain()

    # Offer to change them, rather than making the user discover a flag. Only when there
    # is a terminal to prompt on - in CI this must stay silent and non-blocking.
    # Owner decision 2026-11-14: credentials live in ONE markdown file, read on every run and
    # never written; nothing is copied into a keyring or anywhere else. To change one, edit
    # the file and re-run init.
    def _offer_credentials():
        if a.no_prompt:
            return
        print(f"\n  Credentials are read from {CRED.CRED_FILE} on every run and never stored.")
        print("  To change one, edit that file and run `e5 init` again.")

    print("\n" + "-" * 66)
    blocking = [n for n, v in [("github", git_ok), ("mirror hygiene", hyg_ok),
                               ("catalog", cat_ok)] if not v]
    if blocking:
        print(f"NOT READY — blocked on: {', '.join(blocking)}")
        _offer_credentials()
        return 1
    if not jfrog_ok:
        print("READY to index, but NOT to build or read published contracts.")
        print("  JFrog is needed for: gradle/maven builds of private dependencies and library versions")
        _offer_credentials()
        return 0
    if not br_ok:
        print("READY, with a warning: some repos are off a release branch.")
        return 0
    _reg = M.registry()[1]
    have = sum(1 for r in _reg if (M.CACHE / f"{r['id']}.git").is_dir())
    print("READY.")
    _offer_credentials()
    if have < len(_reg):
        print(f"  {len(_reg) - have} of {len(_reg)} platform repos are not on this machine yet.")
        print("  next:  /e5-sync    clones every platform repo into platform/<system>/<repo>,")
        print("                     compiles, runs the extractors and pyright, rebuilds the")
        print("                     knowledge base. First run: about 1 GB and 15-30 minutes.")
    else:
        print("  next:  /e5-sync             bring the knowledge base up to the latest releases")
        print("         /e5-feature          start a change")
    return 0


if __name__ == "__main__":
    sys.exit(main())

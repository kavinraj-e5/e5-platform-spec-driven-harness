#!/usr/bin/env python3
"""Multi-repo workspace manager.

Materialises only the repos a change set actually touches, using a shared bare mirror
plus one git worktree per repo per change set. One fetch, many trees.

Deliberate choices, each from something that bit us in this estate:
  - plain `git` CLI over bindings: partial clone, sparse checkout and worktrees behave
    predictably on the CLI and not always through libgit2 wrappers
  - the remote name is NOT assumed to be `origin` - this workspace has `master` and `dev`
  - the base branch is the recorded `release/vX.Y.Z`, NOT `main`; `main` is a different
    line of development here and a checkout sitting on it is not "latest"
  - credentials are never read, stored or printed; auth is delegated to the existing git
    credential helper / SSH agent, and we only ever report presence
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kbpaths

KB = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("E5_CACHE", Path.home() / ".e5" / "cache"))
TREES = Path(os.environ.get("E5_TREES", Path.home() / ".e5" / "worktrees"))
STATE = KB / ".e5"

def run(*a, cwd=None, check=True, quiet=False):
    r = subprocess.run(a, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(a[:4])}... -> {r.returncode}\n{r.stderr.strip()[:400]}")
    if not quiet and r.stdout.strip():
        pass
    return r.stdout.strip()

def registry():
    txt = (KB / "registry.yaml").read_text()
    root = kbpaths.workspace_root(txt)
    repos, cur, inr = [], None, False
    for line in txt.splitlines():
        if line.startswith("repos:"): inr = True; continue
        if inr and re.match(r'^\w', line): inr = False
        if not inr: continue
        m = re.match(r'\s+-\s+id:\s*(\S+)', line)
        if m: cur = {"id": m.group(1)}; repos.append(cur); continue
        m = re.match(r'\s+(\w+):\s*(.*)$', line)
        if m and cur is not None: cur[m.group(1)] = m.group(2).strip()
    return root, {r["id"]: r for r in repos}

def repo_remote_name(path: str) -> str:
    """Never assume `origin`. Return whichever remote this checkout actually has."""
    out = run("git", "-C", path, "remote", check=False)
    names = [n for n in out.splitlines() if n.strip()]
    for pref in ("origin", "upstream", "master", "dev"):
        if pref in names: return pref
    return names[0] if names else "origin"

# --------------------------------------------------------------- credentials
def creds_status() -> dict:
    """Presence only. This never reads, prints or stores a secret value."""
    def ok(*a):
        try: return subprocess.run(a, capture_output=True, timeout=8).returncode == 0
        except Exception: return False
    st = {
        "gh_cli_authenticated": ok("gh", "auth", "status"),
        "ssh_agent_has_keys": ok("ssh-add", "-l"),
        "git_credential_helper": run("git", "config", "--get", "credential.helper",
                                     check=False) or None,
        "aws_profile_env": bool(os.environ.get("AWS_PROFILE")),
        "kubeconfig_env": bool(os.environ.get("KUBECONFIG")),
    }
    try:
        import keyring  # noqa: F401
        st["os_keyring_available"] = True
    except Exception:
        st["os_keyring_available"] = False
    st["note"] = ("presence only - no secret is read, stored or displayed. Tokens belong in "
                  "the OS keyring or the git credential helper, never in a harness file.")
    return st

# ------------------------------------------------------------------- mirrors
def mirror_path(rid: str) -> Path:
    return CACHE / f"{rid}.git"

def sync_mirror(rid: str, repo: dict, root: str, blobless: bool = False) -> Path:
    """One bare mirror per repo, shared by every change set. Seeded from the local
    checkout when present so we do not re-download what is already on disk."""
    m = mirror_path(rid)
    CACHE.mkdir(parents=True, exist_ok=True)
    remote = repo.get("remote", "")
    local = Path(root) / repo.get("path", "")
    if not m.exists():
        if remote and remote != "UNKNOWN":
            args = ["git", "clone", "--mirror"]
            if blobless: args += ["--filter=blob:none"]
            src = str(local) if local.is_dir() else remote
            run(*args, src, str(m))
            if local.is_dir() and remote:
                run("git", "-C", str(m), "remote", "set-url", "origin", remote, check=False)
        elif local.is_dir():
            run("git", "clone", "--mirror", str(local), str(m))
        else:
            raise RuntimeError(f"{rid}: no remote and no local checkout")
    else:
        run("git", "-C", str(m), "fetch", "--all", "--prune", check=False)
    return m

# ----------------------------------------------------------------- worktrees
def add_worktree(changeset: str, rid: str, base: str | None = None,
                 blobless: bool = False, allow_non_release: str | None = None) -> dict:
    """Create a worktree off the registry's release branch.

    Refuses rather than guesses. Two guesses used to live here and both are the same
    mistake that produced the KB's worst defect - branching from whatever tree was
    available and then speaking about it with confidence:

      1. a non-release base was a printed warning, so `main` silently became the base;
      2. an unresolvable base fell back to the mirror's HEAD, so a typo'd branch name
         produced a worktree off some arbitrary default.

    `allow_non_release` takes a REASON, not a flag, mirroring `e5 unlock --reason`.
    The reason is the point: it is recorded on the result so a reviewer can see why.
    """
    root, reg = registry()
    if rid not in reg: raise SystemExit(f"unknown repo id {rid!r}; see registry.yaml")
    repo = reg[rid]
    m = sync_mirror(rid, repo, root, blobless)
    base = base or repo.get("branch") or ""
    if not base:
        raise SystemExit(f"{rid}: registry records no branch; refusing to guess one")

    if not re.search(r'release/v\d', base):
        if repo.get("status") == "no-release-branch":
            pass          # labelled in the registry; the estate genuinely has none
        elif not allow_non_release:
            raise SystemExit(
                f"{rid}: base branch {base!r} is not a release/vX.Y.Z branch.\n"
                f"  In this estate `main` is a different line of development, not 'latest'.\n"
                f"  If this is deliberate, pass --allow-non-release '<why>'.")

    wt = TREES / changeset / rid
    wt.parent.mkdir(parents=True, exist_ok=True)
    if wt.exists():
        return {"repo": rid, "worktree": str(wt), "status": "exists", "base": base}

    # resolve the base ref inside the mirror - and REFUSE if it does not resolve
    if run("git", "-C", str(m), "rev-parse", "--verify", "--quiet", base, check=False) == "":
        raise SystemExit(
            f"{rid}: base ref {base!r} does not exist in the mirror {m}.\n"
            f"  Refusing to fall back to its HEAD - branching from an arbitrary tree and\n"
            f"  calling it {base!r} is how the knowledge base came to describe a year-old\n"
            f"  `main`. Run `python3 tools/mirror.py sync` or fix registry.yaml.")

    branch = f"e5/{changeset}"
    run("git", "-C", str(m), "worktree", "add", "-B", branch, str(wt), base)
    out = {"repo": rid, "worktree": str(wt), "branch": branch, "base": base, "status": "created"}
    if allow_non_release:
        out["non_release_reason"] = allow_non_release
    return out

def remove_worktree(changeset: str, rid: str) -> dict:
    m = mirror_path(rid)
    wt = TREES / changeset / rid
    if m.exists():
        run("git", "-C", str(m), "worktree", "remove", "--force", str(wt), check=False)
    if wt.exists(): shutil.rmtree(wt, ignore_errors=True)
    return {"repo": rid, "removed": str(wt)}

# ---------------------------------------------------------------- changesets
_OWNER_RANK = {"deployment-mgmt": 0, "platform-core": 1, "task-manager": 1, "event-store": 2,
               "workflow-manager": 3, "workflow-ops": 3, "monitoring-service": 3,
               "event-processor": 4, "webhook-service": 4, "audit-service": 4,
               "interface": 5, "workflow-api": 5}

def merge_order(repos): return sorted(repos, key=lambda r: (_OWNER_RANK.get(r, 9), r))

def cs_dir(changeset: str) -> Path:
    d = STATE / "changesets" / changeset
    d.mkdir(parents=True, exist_ok=True)
    return d

def cs_new(changeset: str, intent: str, repos: list[str]) -> dict:
    d = cs_dir(changeset)
    order = merge_order(repos)
    meta = {"changeset": changeset, "intent": intent, "repos": repos,
            "merge_order": order, "stage": "intake", "approved": False}
    (d / "changeset.json").write_text(json.dumps(meta, indent=1) + "\n")
    (d / "intake.md").write_text(f"# {changeset}\n\n{intent}\n")
    if not (d / "plan.md").exists():
        (d / "plan.md").write_text(
            "---\nstage: plan\napproved: false\n---\n\n# Plan\n\n"
            "Edits stay blocked until `approved: true`. Flip it only after a human yes.\n")
    return meta

def cs_status(changeset: str | None = None) -> dict:
    d = STATE / "changesets"
    if not d.is_dir(): return {"changesets": []}
    out = []
    for cs in sorted(d.iterdir()):
        if changeset and cs.name != changeset: continue
        plan = cs / "plan.md"
        approved = bool(plan.is_file() and
                        re.search(r'^approved:\s*true\s*$', plan.read_text(), re.M | re.I))
        meta = {}
        if (cs / "changeset.json").is_file():
            try: meta = json.loads((cs / "changeset.json").read_text())
            except Exception: pass
        trees = sorted(p.name for p in (TREES / cs.name).iterdir()) if (TREES / cs.name).is_dir() else []
        out.append({"changeset": cs.name, "approved": approved,
                    "artifacts": sorted(p.name for p in cs.iterdir()),
                    "merge_order": meta.get("merge_order"), "worktrees": trees})
    return {"changesets": out}

def main():
    ap = argparse.ArgumentParser(prog="workspace")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("creds")
    p = sub.add_parser("new");    p.add_argument("changeset"); p.add_argument("--intent", required=True); p.add_argument("--repo", action="append", default=[])
    p = sub.add_parser("add");    p.add_argument("changeset"); p.add_argument("repo"); p.add_argument("--base"); p.add_argument("--blobless", action="store_true")
    # a REASON, not a flag - mirroring `e5 unlock --reason`, where the reason is the point
    p.add_argument("--allow-non-release", metavar="REASON",
                   help="branch off a non-release base; the reason is recorded on the result")
    p = sub.add_parser("remove"); p.add_argument("changeset"); p.add_argument("repo")
    p = sub.add_parser("status"); p.add_argument("changeset", nargs="?")
    sub.add_parser("order").add_argument("repos", nargs="+")
    a = ap.parse_args()
    if a.cmd == "creds":  out = creds_status()
    elif a.cmd == "new":  out = cs_new(a.changeset, a.intent, a.repo)
    elif a.cmd == "add":  out = add_worktree(a.changeset, a.repo, a.base, a.blobless,
                                             a.allow_non_release)
    elif a.cmd == "remove": out = remove_worktree(a.changeset, a.repo)
    elif a.cmd == "status": out = cs_status(a.changeset)
    elif a.cmd == "order":  out = {"merge_order": merge_order(a.repos)}
    print(json.dumps(out, indent=1))

if __name__ == "__main__":
    sys.exit(main())

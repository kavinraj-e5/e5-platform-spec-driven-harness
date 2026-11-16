#!/usr/bin/env python3
"""Re-point registry.yaml at the latest release branch of each repo, read from the
bare mirror rather than from the user's working copy.

Reading working copies is what produced the original defect: the registry inherited
whatever branch each checkout happened to be sitting on, and one of them was a
year-old `main`. Mirrors have no checkout, so there is nothing to inherit.

Writes `branch`, `commit`, `mirror`, `deployed_in_qa`, `deployment_lag`, `status`
and a canonical `remote`. Never writes a credential: `remote` is the plain
element5inc URL, and authentication is attached at use time by credentials.py.

Line-based rather than yaml.dump because registry.yaml carries hand-written comments
that record *why* an entry was re-pointed, and those are worth more than tidy output.
"""
import re, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import mirror as M

KB = Path(__file__).resolve().parent.parent
REG = KB / "registry.yaml"

# keys this tool owns; any existing copy is replaced, order is stable
OWNED = ["remote", "branch", "commit", "mirror", "deployed_in_qa", "deployment_lag", "status"]


def sha(rid: str, ref: str) -> str | None:
    """Mirrors are keyed by REGISTRY ID, not by GitHub repo name - the two differ for
    most of the estate (id `task-manager` lives in repo `e5-platform-core`)."""
    d = M.CACHE / (rid + ".git")
    if not d.exists():
        return None
    r = subprocess.run(["git", "-C", str(d), "rev-parse", "--short", ref],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def plan():
    deployed = M.deployed_map()
    out = {}
    _root, repos = M.registry()
    for repo in repos:
        rid = repo["id"]
        row = M.resolve(repo, deployed)
        full = row.get("canonical")
        new = {"status": row["status"]}
        if full:
            new["remote"] = f"https://github.com/{full}.git"
            new["mirror"] = f"~/.e5/cache/{rid}.git"   # keyed by id, see sha()
        if row["status"] == "ok":
            new["branch"] = row["latest_release"]
            s = sha(rid, row["latest_release"])
            if s:
                new["commit"] = s
            else:
                new["status"] = "unresolvable"
                new.pop("branch", None)
        else:
            # keep whatever branch the entry already had, but say so loudly
            new["branch"] = repo.get("branch", "")
            s = sha(rid, new["branch"]) if new.get("branch") else None
            if s: new["commit"] = s
        if row.get("deployed_in_qa"):
            new["deployed_in_qa"] = row["deployed_in_qa"]
        if row.get("deployment_lag"):
            new["deployment_lag"] = row["deployment_lag"]
        out[rid] = (repo, new)
    return out


def rewrite(changes: dict, apply: bool) -> int:
    lines = REG.read_text().splitlines()
    out, i, n = [], 0, len(lines)
    cur = None
    touched = 0
    in_repos = False
    while i < n:
        ln = lines[i]
        if re.match(r'^[a-z_]+:', ln):
            in_repos = ln.startswith("repos:")
        m = re.match(r'^  - id: (\S+)', ln) if in_repos else None
        if m:
            # flush pending block first
            cur = m.group(1)
            out.append(ln)
            i += 1
            # collect the rest of this block
            # The block ends at the next entry OR at ANY column-0 line. It used to end
            # only at a lowercase top-level key, so a column-0 comment after the last
            # entry - the one documenting `binary_dependencies:` - was swept into that
            # entry and the owned keys were re-emitted BELOW it. That is how the comment
            # came to sit between variant-scaffolding's `build:` and `remote:`, and how it
            # moved into the next repo appended after it. YAML still parsed; the file no
            # longer said what its author wrote. Indented comments inside an entry - the
            # "why this was re-pointed" notes - are unaffected and still kept.
            block = []
            while i < n and not re.match(r'^  - id: ', lines[i]) and not re.match(r'^\S', lines[i]):
                block.append(lines[i]); i += 1
            new = changes.get(cur, (None, {}))[1]
            kept = []
            for b in block:
                k = re.match(r'^    ([a-z_]+):', b)
                if k and k.group(1) in OWNED:
                    continue            # replaced below
                if b.strip() == "":
                    continue            # re-added as the single separator
                kept.append(b)
            out.extend(kept)
            for k in OWNED:
                if k in new and new[k] != "":
                    out.append(f"    {k}: {new[k]}")
            out.append("")
            touched += 1
            continue
        out.append(ln)
        i += 1
    text = "\n".join(out).rstrip() + "\n"
    if apply:
        REG.write_text(text)
    return touched


def main():
    apply = "--apply" in sys.argv
    ch = plan()
    print(f"{'repo':<22} {'was':<28} {'now':<22} {'deployed':<10} status")
    print("-" * 96)
    nmoved = 0
    for rid, (old, new) in ch.items():
        was, now = old.get("branch", ""), new.get("branch", "")
        moved = was != now
        nmoved += moved
        print(f"{rid:<22} {was:<28} {now:<22} {new.get('deployed_in_qa','-'):<10} "
              f"{new['status']}{'  <-- MOVED' if moved else ''}")
    print(f"\n{nmoved} of {len(ch)} entries move branch.")
    t = rewrite(ch, apply)
    print(("wrote" if apply else "dry-run; would write") + f" {t} entries to registry.yaml")


if __name__ == "__main__":
    main()

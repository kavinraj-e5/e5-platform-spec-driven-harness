#!/usr/bin/env python3
"""One-time setup of the harness on a new machine: `./e5 setup`.

    ./e5 setup               everything below
    ./e5 setup --no-plugin   skip installing the Claude Code plugin

What it does, in order, and what it never does:
  1. checks the tools it needs: python >= 3.10 and git (required); java (JDK 17/21),
     gradle and semgrep are optional and only reported
  2. creates ONE virtualenv at .venv-mcp/ with the MCP server and extractor dependencies,
     including pyright for the Python call graph. `./e5` and the plugin both use it
  3. creates ~/.e5/credentials.md from templates/credentials.md if it does not exist,
     readable only by you (0600). It NEVER writes a credential and never reads one into
     its output. You fill the file in; init reads it on every run.
  4. records where this harness lives, for the plugin (tools/prepare_plugin.py)
  5. adds this folder as a Claude Code plugin marketplace and installs the e5-platform
     plugin (`claude plugin ...`), unless --no-plugin

It does not clone the platform repos. `/e5-sync` (or `./e5 sync all`) does that with YOUR
credentials: bare mirrors into ~/.e5/cache, checked out under platform/<system>/<repo>.
"""
from __future__ import annotations
import argparse, os, shutil, subprocess, sys, venv
from pathlib import Path

KB = Path(__file__).resolve().parent.parent
VENV = KB / ".venv-mcp"
CREDS = Path(os.environ.get("E5_GIT_CRED_FILE", Path.home() / ".e5" / "credentials.md")).expanduser()
OK, BAD, WARN = "  ok   ", "  FAIL ", "  warn "


def check_tools() -> bool:
    ok = True
    if sys.version_info < (3, 10):
        print(f"{BAD}python {sys.version.split()[0]} - need 3.10 or newer"); ok = False
    else:
        print(f"{OK}python {sys.version.split()[0]}")
    for tool, need, why in (("git", True, "mirrors and checkouts"),
                            ("java", False, "compiling the JVM repos, artifact introspection"),
                            ("gradle", False, "compiling repos with no gradle wrapper"),
                            ("semgrep", False, "known-issue re-checks"),
                            ("claude", False, "plugin install")):
        found = shutil.which(tool)
        if found:
            print(f"{OK}{tool:8} {found}")
        else:
            print(f"{BAD if need else WARN}{tool:8} not found - {why}")
            ok = ok and not need
    return ok


def make_venv() -> bool:
    py = VENV / "bin" / "python"
    if not py.exists():
        print(f"  creating {VENV.relative_to(KB)}/ ...")
        venv.EnvBuilder(with_pip=True).create(VENV)
    reqs = [KB / "tools" / "requirements-mcp.txt", KB / "tools" / "requirements-extract.txt"]
    cmd = [str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    for r in reqs:
        cmd += ["-r", str(r)]
    r = subprocess.run(cmd)
    print(f"{OK if r.returncode == 0 else BAD}dependencies installed into {VENV.name}/")
    if r.returncode == 0:
        # The pip package downloads the pyright release on first use. Do it now, not in the
        # middle of the first sync.
        ls = VENV / "bin" / "pyright"
        if ls.exists():
            v = subprocess.run([str(ls), "--version"], capture_output=True, text=True)
            print(f"{OK if v.returncode == 0 else WARN}pyright {v.stdout.strip() or 'not ready'}")
    return r.returncode == 0


def make_credentials_file() -> None:
    if CREDS.is_file():
        print(f"{OK}credentials file exists: {CREDS} (not read here)")
        return
    CREDS.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(KB / "templates" / "credentials.md", CREDS)
    CREDS.chmod(0o600)
    print(f"{WARN}created {CREDS} from the template - FILL IT IN before /e5-init")


def stamp_plugin() -> None:
    subprocess.run([sys.executable, str(KB / "tools" / "prepare_plugin.py")], check=True)


def install_plugin() -> bool:
    claude = shutil.which("claude")
    if not claude:
        print(f"{WARN}claude CLI not found - install the plugin later with:\n"
              f"         claude plugin marketplace add {KB}\n"
              f"         claude plugin install e5-platform@e5-platform-harness")
        return False
    have = subprocess.run([claude, "plugin", "marketplace", "list"], capture_output=True, text=True).stdout
    if "e5-platform-harness" in have:
        subprocess.run([claude, "plugin", "marketplace", "update", "e5-platform-harness"])
    else:
        subprocess.run([claude, "plugin", "marketplace", "add", str(KB)])
    r = subprocess.run([claude, "plugin", "install", "e5-platform@e5-platform-harness"])
    if r.returncode != 0:
        subprocess.run([claude, "plugin", "update", "e5-platform@e5-platform-harness"])
    print(f"{OK}plugin installed - restart Claude Code to load it")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-plugin", action="store_true")
    a = ap.parse_args()
    print("e5 setup\n\nTOOLS")
    if not check_tools():
        print("\nfix the FAILs above, then re-run ./e5 setup"); return 1
    print("\nPYTHON ENVIRONMENT")
    if not make_venv():
        return 1
    print("\nCREDENTIALS")
    make_credentials_file()
    print("\nPLUGIN")
    stamp_plugin()
    if not a.no_plugin:
        install_plugin()
    print("\nNEXT\n"
          f"  1. fill in {CREDS} (git username + token, JFrog user + password)\n"
          "  2. restart Claude Code\n"
          "  3. run /e5-init - it verifies the credentials and the tools\n"
          "  4. run /e5-sync - it clones every platform repo into platform/<system>/<repo>,\n"
          "     compiles, runs the extractors and pyright, and rebuilds the knowledge base\n"
          "     (first run: about 1 GB and 15-30 minutes)\n"
          "  5. /e5-feature, /e5-bugfix, /e5-update-knowledge-base")
    return 0


if __name__ == "__main__":
    sys.exit(main())

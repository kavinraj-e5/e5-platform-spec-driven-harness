#!/usr/bin/env python3
"""Credential resolution for the indexer. Read at use time, never persisted.

Rules, enforced by tests:
  1. a secret is NEVER written to registry.yaml, a card, graph/, or any log
  2. a secret is NEVER returned in a form that gets printed - callers get a URL
     builder or an askpass script, not the raw value
  3. the ONE source is the nominated markdown file (owner decision 2026-11-14), read at
     use time and never written. CI, which has no file, may pass JFrog via env vars.
     The keyring helpers below are kept only so an old keyring entry can be reported;
     they are never used to authenticate.
"""
from __future__ import annotations
import os, re, subprocess, tempfile, stat
from pathlib import Path

KB = Path(__file__).resolve().parent.parent
# Where the ONE credentials file is (owner decision 2026-11-14: read every run, never written):
#   1. $E5_GIT_CRED_FILE, if set
#   2. ~/.e5/credentials.md - the per-person default; `./e5 setup` creates it from
#      templates/credentials.md
#   3. ../docs/git_credentails.md beside the knowledge base - the original location on the
#      owner's machine, kept so nothing moves under them
def _cred_file() -> Path:
    env = os.environ.get("E5_GIT_CRED_FILE")
    if env:
        return Path(env).expanduser()
    home = Path.home() / ".e5" / "credentials.md"
    legacy = KB.parent / "docs" / "git_credentails.md"
    if not legacy.is_file():
        return home
    # `./e5 setup` drops the template at `home`. A file that still holds only the
    # template placeholders must not hide a filled-in legacy file next to it.
    if home.is_file() and not _filled(home):
        return legacy
    return home if home.is_file() else legacy


def _filled(f: Path) -> bool:
    """True if the file has at least one value that is not a `<...>` placeholder."""
    for line in f.read_text(errors="ignore").splitlines():
        k, sep, v = line.partition(":")
        v = v.strip()
        if sep and v and not (v.startswith("<") and v.endswith(">")) \
                and k.strip().lower() in ("username", "token", "user_name", "pwd", "password"):
            return True
    return False


CRED_FILE = _cred_file()

_SECRET_KEYS = ("token", "pat", "password", "pwd", "secret")

# Service names are shared with init.py --store-keyring. They were NOT: the writer used
# "e5-github"/username+token and the reader looked for "e5-platform-kb"/github_user+
# github_token, so storing a credential in the keyring silently did nothing and the
# harness kept reading the plaintext file - the exact thing --store-keyring exists to
# stop. Named here once, imported there, so they cannot drift again.
KEYRING_GITHUB = ("e5-github", "username", "token")
KEYRING_JFROG = ("e5-jfrog", "username", "token")
# read-only compatibility with the original, wrong, names
_LEGACY_GITHUB = ("e5-platform-kb", "github_user", "github_token")


def _keyring_pair(svc: str, ukey: str, tkey: str):
    try:
        import keyring
        u = keyring.get_password(svc, ukey)
        t = keyring.get_password(svc, tkey)
        return (u, t) if u and t else None
    except Exception:
        return None


def _from_keyring() -> tuple[str, str] | None:
    return _keyring_pair(*KEYRING_GITHUB) or _keyring_pair(*_LEGACY_GITHUB)

def _from_file(section: str = "git") -> tuple[str, str] | None:
    """Parse one stanza of the nominated credentials file.

    `section` exists because the platform's contracts live in published JFrog artifacts,
    not in any repository - AppSDK defines the function blocks workflows call and has no
    source anywhere. Reading them needs the jfrog stanza, so the parser gained a section
    argument rather than a second near-copy of itself.
    """
    if not CRED_FILE.is_file(): return None
    try:
        txt = CRED_FILE.read_text()
    except Exception:
        return None
    sect, user, tok = None, None, None
    for line in txt.splitlines():
        s = line.strip()
        if not s: continue
        if re.fullmatch(r"(git|jfrog|aws)\b.*", s, re.I) and ":" not in s:
            sect = s.lower().split()[0]; continue
        m = re.match(r"(\w+)\s*:\s*(.+)$", s)
        if not m or sect != section: continue
        k, v = m.group(1).lower(), m.group(2).strip()
        if v.startswith("<") and v.endswith(">"):
            continue            # still the template placeholder: not a credential
        if k in ("username", "user", "user_name"): user = v
        elif k in _SECRET_KEYS:                     tok = v
    return (user, tok) if user and tok else None

def available() -> dict:
    """Presence only - safe to print. Never includes a value."""
    src = f"file:{CRED_FILE.name}" if _from_file() else None
    helper = subprocess.run(["git", "config", "--get", "credential.helper"],
                            capture_output=True, text=True).stdout.strip() or None
    return {"github_source": src, "git_credential_helper": helper,
            "old_keyring_entry_ignored": _from_keyring() is not None,
            "note": "presence only; no value is read into any output or file"}

def _creds() -> tuple[str, str] | None:
    return _from_file("git")


def jfrog_creds() -> tuple[str, str] | None:
    """(user, secret) for element5.jfrog.io, or None.

    NEVER log the return value. Callers must pass it straight into a request and use
    mask() on anything they print.
    """
    env_u = os.environ.get("E5_JFROG_USER", "").strip()
    env_p = os.environ.get("E5_JFROG_TOKEN", "").strip()
    if env_u and env_p:
        return (env_u, env_p)
    return _from_file("jfrog")


def jfrog_available() -> dict:
    """Presence only - safe to print.

    Reports the source it would ACTUALLY use. The first version had no keyring branch at
    all, so a credential stored in the keyring was used correctly but reported as coming
    from the plaintext file - which would have told someone their secret was still on disk
    when it was not, or the reverse.
    """
    c = jfrog_creds()
    if os.environ.get("E5_JFROG_USER") and os.environ.get("E5_JFROG_TOKEN"):
        src = "env"
    elif c:
        src = f"file:{CRED_FILE.name}"
    else:
        src = None
    return {"jfrog_source": src, "present": c is not None,
            "note": "presence only; no value is read into any output or file"}

def authed_url(repo_full: str) -> str:
    """https URL with credentials inlined. NEVER log the return value.

    Callers must pass it straight to subprocess and mask it in any error they
    surface - see mask()."""
    c = _creds()
    base = f"github.com/{repo_full}.git"
    if not c: return f"https://{base}"
    u, t = c
    return f"https://{u}:{t}@{base}"

def mask(text: str) -> str:
    """Strip anything secret-shaped from text before it reaches a log or a user.
    Applied to every subprocess stderr this module's callers surface."""
    if not text: return text
    c = _creds()
    if c:
        text = text.replace(c[1], "<REDACTED>")
    text = re.sub(r'https://[^:/@\s]+:[^@\s]+@', 'https://<REDACTED>@', text)
    text = re.sub(r'\bgh[pousr]_[A-Za-z0-9]{16,}\b', '<REDACTED>', text)
    return text

if __name__ == "__main__":
    import json
    print(json.dumps(available(), indent=1))

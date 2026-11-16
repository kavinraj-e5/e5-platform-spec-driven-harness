#!/usr/bin/env python3
"""Tests for credential handling. Every one of these corresponds to a bug that was real.

None of them touch the live keyring with real values: the round-trip test writes a dummy,
asserts, and removes it, restoring whatever was there before.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import credentials as C

KB = Path(__file__).resolve().parent.parent








def test_no_secret_accepted_on_the_command_line():
    """`ps` shows arguments to every user on the box and shells log them to history.
    There must be no way to pass a token as a flag."""
    src = (KB / "tools" / "init.py").read_text()
    for bad in ("--token", "--password", "--secret", "--pat"):
        assert f'"{bad}"' not in src, (
            f"init.py accepts {bad} as an argument - a secret comes only from the "
            f"credentials file, never from argv")


def test_presence_reporting_never_includes_a_value():
    """available()/jfrog_available() are printed by init and by the session hook."""
    for d in (C.available(), C.jfrog_available()):
        blob = repr(d)
        real = C._creds() or ("", "")
        for v in real:
            if v and len(v) > 6:
                assert v not in blob, "a credential value leaked into a presence report"


def test_mask_neutralises_both_shapes():
    """mask() is applied to anything logged. It must handle a token in a URL and bare."""
    assert "ghp_" not in C.mask("https://u:ghp_AAAAAAAAAAAAAAAAAAAAAAAA@github.com/x")
    assert "ghp_AAAAAAAAAAAAAAAAAAAAAAAA" not in C.mask("ghp_AAAAAAAAAAAAAAAAAAAAAAAA")


def test_init_does_not_prompt_when_not_a_tty():
    """The slash command runs init non-interactively. A prompt there would hang the
    session, and collecting a secret in a chat would write it to the transcript."""
    src = (KB / "tools" / "init.py").read_text()
    # Since 2026-11-14 init never prompts at all: credentials come only from the md file.
    assert "input(" not in src and "getpass" not in src, "init must never prompt"
    assert "--no-prompt" in src, "init needs a way to be fully silent for CI"
    cmd = (KB / "plugin" / "commands" / "e5-init.md").read_text()
    assert "--no-prompt" in cmd, (
        "/e5-init must pass --no-prompt; otherwise it can open a prompt it cannot protect")
    assert "transcript" in cmd.lower(), (
        "/e5-init must warn against pasting a credential into the conversation")





# --------------------------------------------------------------- the setup UI








# The console was "read-only by construction" until catalog editing was added. The
# invariant NARROWED rather than disappeared: exactly one module writes, it writes only
# catalog/*.yaml, and it refuses the fields CI verifies. Deleting the test instead of
# re-aiming it would have thrown away the guarantee along with the constraint.
CONSOLE_WRITER = "edit.py"















if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn(); print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            bad += 1; print(f"FAIL  {fn.__name__}\n      {e}")
        except Exception:
            bad += 1; print(f"ERROR {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)


def test_the_markdown_file_is_the_only_source():
    """Owner decision 2026-11-14: read the md file on every run, never write it, store
    nothing elsewhere. A keyring entry must not take precedence or be used at all."""
    import inspect
    src = inspect.getsource(C._creds) + inspect.getsource(C.jfrog_creds)
    assert "_from_keyring" not in src and "_keyring_pair" not in src, \
        "a keyring is consulted for authentication again"
    import init as I
    for gone in ("store_keyring", "set_credentials", "clear_credentials"):
        assert not hasattr(I, gone), f"init.{gone} writes credentials somewhere"

"""The harness is shared with the team: it must carry no secret and no one's machine paths,
and a fresh machine must be able to set itself up (2026-11-15)."""
import os, re, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
KB = HERE.parent
sys.path.insert(0, str(HERE))

SECRET = re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
                    r"AKCp[A-Za-z0-9]{20,}|-----BEGIN (RSA |OPENSSH )?PRIVATE KEY-----")


def _tracked_and_new():
    r = subprocess.run(["git", "-C", str(KB), "ls-files", "--cached", "--others",
                        "--exclude-standard"], capture_output=True, text=True)
    return [KB / p for p in r.stdout.splitlines()]


def test_no_secret_in_any_file_that_would_be_shared():
    hits = []
    for p in _tracked_and_new():
        if p.is_file() and p.stat().st_size < 5_000_000:
            try:
                for m in SECRET.finditer(p.read_text(errors="ignore")):
                    body = re.sub(r"^(ghp_|github_pat_|AKIA|AKCp)", "", m.group(0))
                    if len(set(body)) > 1:          # ghp_AAAA... is a test fixture
                        hits.append(str(p.relative_to(KB))); break
            except Exception:
                pass
    assert not hits, f"a secret-looking token is in files that would be pushed: {hits}"


def test_registry_names_no_home_directory():
    assert not re.search(r"^workspace_root:\s*/home/", (KB / "registry.yaml").read_text(), re.M)


def test_workspace_root_defaults_to_the_users_home():
    import kbpaths
    assert kbpaths.workspace_root("repos: []\n").endswith("/.e5/workspace")


def test_template_placeholders_are_not_credentials(tmp_path, monkeypatch):
    f = tmp_path / "c.md"
    f.write_text((KB / "templates" / "credentials.md").read_text())
    import credentials as C
    monkeypatch.setattr(C, "CRED_FILE", f)
    assert C._from_file("git") is None and C._from_file("jfrog") is None


def test_setup_and_update_exist_and_sync_is_what_clones():
    e5 = (KB / "e5").read_text()
    assert 'cmd == "setup"' in e5 and 'cmd == "update"' in e5
    assert '("check out the repos under platform/<system>/", ["checkouts.py", "sync"])' in e5
    assert (KB / "templates" / "credentials.md").is_file()
    assert "pyright" in (KB / "tools" / "requirements-extract.txt").read_text(), \
        "setup must install pyright, or a teammate's sync has no Python call graph"


def test_an_unfilled_template_does_not_hide_a_filled_legacy_file(tmp_path, monkeypatch):
    import credentials as C
    home = tmp_path / ".e5"; home.mkdir()
    (home / "credentials.md").write_text((KB / "templates" / "credentials.md").read_text())
    legacy = tmp_path / "docs"; legacy.mkdir()
    (legacy / "git_credentails.md").write_text("git\nusername: u\ntoken: t\n")
    monkeypatch.delenv("E5_GIT_CRED_FILE", raising=False)
    monkeypatch.setattr(C.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(C, "KB", tmp_path / "kb")
    assert C._cred_file() == legacy / "git_credentails.md"
    (home / "credentials.md").write_text("git\nusername: me\ntoken: x\n")
    assert C._cred_file() == home / "credentials.md"

"""The user-facing surface must only point at things that exist.

Found by the owner on 2026-11-14 testing /e5-init: init.py read the catalog's old `Domain`
key and crashed, and /e5-sync still told the model to run tools that had been archived.
Nothing in the suite exercised either path. These tests do.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KB = HERE.parent
sys.path.insert(0, str(HERE))

PLUGIN_DOCS = sorted((KB / "plugin" / "commands").glob("*.md")) + \
    sorted((KB / "plugin" / "skills").glob("*/SKILL.md"))


def _e5_subcommands() -> set:
    return set(re.findall(r'cmd == "([a-z-]+)"', (KB / "e5").read_text()))


def test_exactly_the_five_user_commands():
    names = {p.stem for p in (KB / "plugin" / "commands").glob("*.md")}
    assert names == {"e5-init", "e5-sync", "e5-feature", "e5-bugfix",
                     "e5-update-knowledge-base"}, names


def test_every_tool_a_command_or_skill_names_exists():
    missing = []
    for p in PLUGIN_DOCS:
        for tool in re.findall(r'tools/([\w/]+\.py)', p.read_text()):
            if not (KB / "tools" / tool).is_file():
                missing.append(f"{p.relative_to(KB)} -> tools/{tool}")
    assert not missing, "plugin docs name tools that do not exist:\n  " + "\n  ".join(missing)


def test_every_e5_subcommand_a_command_or_skill_names_exists():
    subs = _e5_subcommands()
    missing = []
    for p in PLUGIN_DOCS:
        for sub in re.findall(r'\./e5 ([a-z-]+)', p.read_text()):
            if sub not in subs:
                missing.append(f"{p.relative_to(KB)} -> ./e5 {sub}")
    assert not missing, "plugin docs name e5 subcommands that do not exist:\n  " + "\n  ".join(missing)


def test_every_mcp_tool_a_skill_names_is_served():
    import mcp_server as S
    served = set(S.DESCRIPTIONS)
    missing = []
    for p in PLUGIN_DOCS:
        for t in set(re.findall(r'\b((?:kb|platform)_[a-z_]+)\b(?![./])', p.read_text())):
            if t not in served:
                missing.append(f"{p.relative_to(KB)} -> {t}")
    assert not missing, "skills call MCP tools the server does not serve:\n  " + "\n  ".join(missing)


def test_init_catalog_check_runs():
    """The exact crash: init's catalog step read `cat['Domain']` after the rename."""
    import init as I
    assert I.check_catalog() in (True, False)


def test_sync_status_runs():
    r = subprocess.run([sys.executable, str(HERE / "sync.py"), "status"],
                       capture_output=True, text=True, timeout=600, cwd=str(KB))
    assert r.returncode == 0, r.stderr[-2000:]


def test_intake_asks_one_question_at_a_time():
    """Owner, 2026-11-14: /e5-feature asked eight questions in one message. Intake is
    exactly two questions, one per message (doc? then description), then straight to the
    UX + HLD proposal, which ends with one question and waits for a yes."""
    # Owner, later the same day: questions are asked with the AskUserQuestion option
    # dialog, not in plain text.
    for skill, first in (("e5-feature", "Do you have a Confluence doc"),
                         ("e5-bugfix", "Do you have a Jira ticket")):
        body = (KB / "plugin" / "skills" / skill / "SKILL.md").read_text()
        assert "AskUserQuestion" in body and "One question per call" in body, skill
        assert first in body and "Describe" in body, skill
        assert "never ask several at once" in body, skill
        assert "Stop. Get a yes" not in body, skill
    feat = (KB / "plugin" / "skills" / "e5-feature" / "SKILL.md").read_text()
    stage0 = feat.split("## Stage 0")[1].split("## Stage 1")[0]
    assert stage0.count("`AskUserQuestion`") == 2, "Stage 0 must ask exactly two questions"
    assert "Do you agree with this UX\nand HLD" in feat or "Do you agree with this UX and HLD" in feat
    assert "only** on an explicit yes" in feat

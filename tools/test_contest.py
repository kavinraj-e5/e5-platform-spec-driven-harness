"""Tests for the correction loop.

Each test defends against a specific way this feature could rot. The first one is the
most important: it is the executable form of "name your consumer", and it exists because
the earlier prototype shipped a record_feedback tool writing to a file nothing ever read.
"""
import sys, re, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_core as core
import contest as C

KB = core.KB


def _fresh(monkeypatch=None):
    core._cache.pop("contests", None)


# --------------------------------------------------------------- the write-only sink






# ------------------------------------------------------------------- attribution
def test_attribution_is_required():
    """Attribution is the incentive to be careful; an anonymous correction has neither
    accountability nor anyone to ask a follow-up question of."""
    class A: pass
    a = A()
    a.kind, a.subject, a.says = "fact", "x", "y"
    a.harness_said = a.evidence = a.id = None
    a.target = ["repo:interface"]
    a.by = ""                      # explicitly anonymous
    import io, contextlib, unittest.mock as m
    buf = io.StringIO()
    with m.patch.object(C, "whoami", return_value=""), contextlib.redirect_stdout(buf):
        rc = C.cmd_new(a)
    assert rc != 0, "an unattributed correction was accepted"
    assert "unattributed" in buf.getvalue().lower()


def test_contest_without_targets_is_refused():
    """A contest with no targets can never surface, so recording it is a lie."""
    class A: pass
    a = A()
    a.kind, a.subject, a.says = "fact", "zzz unmatchable zzz", "qqq unmatchable qqq"
    a.harness_said = a.evidence = a.id = None
    a.target, a.by = None, "t@e5.ai"
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.cmd_new(a)
    assert rc != 0 and "never surface" in buf.getvalue()


# ------------------------------------------------- the regenerated-directory trap
def test_contests_dir_is_never_regenerated():
    """tools/confluence/reconcile.py unlinks its whole drafts dir on every run; any human
    review state parked there is destroyed. contests/ must not acquire that behaviour."""
    for p in Path(__file__).resolve().parent.rglob("*.py"):
        if p.name == "test_contest.py":
            continue
        src = p.read_text()
        for m in re.finditer(r'^.*\b(unlink|rmtree)\b.*$', src, re.M):
            line = m.group(0)
            assert "contest" not in line.lower(), (
                f"{p.name} appears to delete contest files:\n    {line.strip()}\n"
                f"contests/ is hand-managed review state and must never be regenerated."
            )


# ------------------------------------------------------------- the severity floor


# ---------------------------------------------------------------- risk signals




# ---------------------------------------------------------------- hygiene




if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for f in fns:
        try:
            f(); print(f"  PASS {f.__name__}")
        except AssertionError as e:
            bad += 1; print(f"  FAIL {f.__name__}\n       {e}")
        except Exception:
            bad += 1; print(f"  ERROR {f.__name__}"); traceback.print_exc()
    print(f"\n{len(fns)-bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)

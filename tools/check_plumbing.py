#!/usr/bin/env python3
"""Does anything actually produce what the tools read?

    python3 tools/check_plumbing.py            exit 1 on a dead integration point

THE BUG CLASS
-------------
`kb_core.queue_served_by` - the function whose own docstring calls QUEUE_SERVED_BY "the
edge that justifies the whole harness" - resolved 1 of 6 queues for as long as it existed,
and the reason was not a hard join. It read two things that nothing produced:

  * `graph/vq-bindings.json`, which no tool wrote. Both it and its successor
    `queue-observations.json` are now deleted: an observed value is not a fact.
  * a `ClusterQueueBinding` fact kind, which no extractor emits

Both branches were syntactically fine, covered by no test, and silently never taken. The
tool degraded to "one half of this dependency is invisible", reported that as a property
of the PLATFORM, and was believed.

`graph_store.py stats` failed the same way from the other direction: it counted a
hand-kept list of labels and reported 6 for a 14-label graph. And `build_graph.py` set
`"artifact": None` as a literal, so the publisher half of every coordinate was absent.

Three instances of one shape: a consumer and a producer that never met, where the consumer
had no way to say so. This checks the shape mechanically.

TWO CHECKS
----------
1. Every `graph/<file>` a tool reads either EXISTS or is written by some tool in this
   repository. A path that is neither is a branch that can never be taken.
2. Every fact `kind` a tool compares against is produced by some extractor - present in a
   graph fact file, or written as a literal by a producer.

WHY THIS ONE BLOCKS
-------------------
Unlike staleness, drift or ownership, a dead reference is a defect in THIS repository and
nothing else. It does not depend on which release the platform shipped or on a human
filling a field in. It is wrong on a clean checkout, and it stays wrong quietly, which is
the only reason it lasted.

EXPECTED ABSENCES
-----------------
`EXPECTED` below names artifacts a tool legitimately reads before anything writes them -
a first run, or a capture that needs credentials this repo does not hold. Each needs a
reason, and the reason is the point: an exception list with no reasons becomes the place
dead code goes to hide.
"""
from __future__ import annotations
import collections, json, re, sys
from pathlib import Path

KB = Path(__file__).resolve().parent.parent
GRAPH = KB / "graph"

# artifact -> why it may be absent on a clean checkout
EXPECTED = {
    "deployed.json": "captured by a kubectl one-liner, which needs a kubeconfig this "
                     "repo does not hold; check_staleness.py prints the command",
    "estate-snapshot.json.gz": "written by `estate.py accept`; absent until the first "
                               "snapshot is taken",
    "citation-anchors.json": "written by `citations.py bless`",
}

# Three spellings, all in use: `GRAPH / "x"`, `KB / "graph" / "x"`, and the literal
# `"graph/x"`. The first version matched only the first two and missed a path written the
# third way, which is the same kind of silent miss this tool exists to catch.
_SRC = re.compile(r'(?:GRAPH|graph|_GRAPH)\s*/\s*"([\w.\-]+)"'
                  r'|"graph"\s*/\s*"([\w.\-]+)"'
                  r'|"graph/([\w.\-]+)"')
_ASSIGN = re.compile(r'^\s*([A-Z_][A-Z_0-9]*)\s*=\s*.*?'
                     r'(?:(?:GRAPH|graph)\s*/\s*"([\w.\-]+)"'
                     r'|"graph"\s*/\s*"([\w.\-]+)")', re.M)
_WRITE = (r'{v}\.write_text\(', r'{v}\.open\(\s*"w', r'gzip\.open\(\s*{v}\b\s*,\s*"w',
          r'json\.dump\([^)]*{v}\b', r'with\s+{v}\.open\(\s*"w')


def sources() -> dict:
    out = {}
    for d in ("tools", "console"):
        for p in sorted((KB / d).glob("*.py")):
            # Never scan this file. Its own regexes and the comment explaining them
            # contain `GRAPH / "x"`, so the first run reported `graph/x` as read by
            # nothing - a checker reading its own patterns as data.
            if p.name.startswith("test_") or p.name == Path(__file__).name:
                continue
            out[f"{d}/{p.name}"] = p.read_text()
    return out


def _written(src: dict) -> set:
    """graph artifacts some tool writes, via the constant it is bound to."""
    out = set()
    for name, text in src.items():
        for var, a1, a2 in _ASSIGN.findall(text):
            artifact = a1 or a2
            if artifact and any(re.search(rx.format(v=re.escape(var)), text)
                                for rx in _WRITE):
                out.add(artifact)
        # a few writers build the path inline
        for m in re.finditer(r'\(\s*(?:GRAPH|graph)\s*/\s*"([\w.\-]+)"\s*\)'
                             r'\.write_text|\(\s*(?:GRAPH|graph)\s*/\s*"([\w.\-]+)"\s*\)'
                             r'\.open\(\s*"w', text):
            out.add(m.group(1) or m.group(2))
    return out


def produced_kinds(src: dict) -> set:
    out = set()
    for f in sorted(GRAPH.glob("*.jsonl")):
        for line in f.open():
            try:
                out.add(json.loads(line).get("kind"))
            except Exception:
                pass
    for text in src.values():
        out |= set(re.findall(r'"kind":\s*"(\w+)"', text))
        out |= set(re.findall(r'fact\(\s*"(\w+)"', text))
        out |= set(re.findall(r'NodeEmit\(\s*"(\w+)"', text))
    return out


def compared_kinds(src: dict) -> dict:
    out: dict = collections.defaultdict(set)
    for name, text in src.items():
        for m in re.finditer(r'\["kind"\]\s*==\s*"(\w+)"', text):
            out[m.group(1)].add(name)
        for m in re.finditer(r'\["kind"\]\s+(?:not\s+)?in\s+[\(\{]([^)}]*)[\)\}]', text):
            for k in re.findall(r'"(\w+)"', m.group(1)):
                out[k].add(name)
        # Only a SUBSCRIPT comparison counts. A bare `kind == "owns"` also matches
        # capability.py's own Claim attribute and console/kb.py's display categories,
        # which are not fact kinds - six false positives, and a gate that blocks the
        # build on six false positives is a gate that gets switched off today.
    return out


def main() -> int:
    src = sources()
    have = {p.name for p in GRAPH.iterdir()} if GRAPH.is_dir() else set()
    writes = _written(src)
    errs, notes = [], []

    read: dict = collections.defaultdict(set)
    for name, text in src.items():
        for m in _SRC.finditer(text):
            read[m.group(1) or m.group(2) or m.group(3)].add(name)
    for artifact, readers in sorted(read.items()):
        if artifact in have or artifact in writes:
            continue
        if artifact in EXPECTED:
            notes.append(f"{artifact} absent - {EXPECTED[artifact]}")
            continue
        errs.append(f"graph/{artifact} is read by {', '.join(sorted(readers))} and "
                    f"nothing in this repository writes it or has ever written it")

    prod = produced_kinds(src)
    for kind, who in sorted(compared_kinds(src).items()):
        if kind not in prod:
            errs.append(f"fact kind {kind!r} is compared in {', '.join(sorted(who))} and "
                        f"no extractor emits it - that branch can never be taken")

    for n in notes:
        print(f"note   {n}")
    for e in errs:
        print(f"ERROR  {e}")
    print(f"\n{len(read)} graph artifact(s) read, {len(writes)} written, "
          f"{len(compared_kinds(src))} fact kind(s) compared against "
          f"{len(prod)} produced - {len(errs)} errors, {len(notes)} expected absences")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())

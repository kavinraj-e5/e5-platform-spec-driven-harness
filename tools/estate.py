#!/usr/bin/env python3
"""Estate diff: what moved in the platform, and which cards that implicates.

    python3 tools/estate.py report     the subject index, and what the cards name
    python3 tools/estate.py diff       NEW / GONE / CHANGED since the snapshot
    python3 tools/estate.py accept     write the snapshot - the counterpart of `cite bless`
    python3 tools/estate.py subjects   dump the index (one line per subject)

WHY A COUNT IS NOT A DIFF
-------------------------
`graph/sync-state.json` already records what a sync did: `{"deployment-mgmt": {"commit":
"6536f6b", "facts": 534}}`. That is a COUNT. A task route whose `handled_by` flips from
workflow-manager to workflow-ops does not change it. Neither does a library version bump,
a dropped column, or an rpc that changed its request type. 534 facts before, 534 after,
and six cards are now wrong.

`cite drift` closed the other half of this: a cited LINE whose content changed. But a card
can go stale with every cited line untouched - the fact it describes moved somewhere the
card does not cite. Nothing saw that, so this does.

SUBJECT, NOT FACT
-----------------
Diffing facts directly reports a flood every sync, because a fact's identity carries its
line number: `DbBinding:platform-core:.../AkstService.java:51:AKST`. Insert a method above
it and that is one GONE and one NEW for a file nobody touched.

So the unit is the SUBJECT - `(kind, component, key)` with every `<path>:<line>` inside the
key collapsed to the file's basename. Location-free by construction. A thing that moved is
invisible here ON PURPOSE: that is `cite drift`'s question, and reporting one event as two
findings in two tools is how a worklist becomes noise.

Three verdicts, and no fourth:

  NEW      a subject the estate did not have
  GONE     a subject the estate no longer has   <- the dangerous one
  CHANGED  same subject, different shape        <- version bumps, handler flips, op changes

WHICH CARDS
-----------
A change is only actionable if you know what to re-read. Three routes, strongest first:

  names     a card contains the subject's identifier verbatim. A GONE subject named in a
            card is the knowledge base asserting something the code no longer contains.
  cites     a card cites a file this subject lives in (from graph/citation-facts.jsonl).
  describes the card's repo is the subject's repo. Weak, and for a NEW subject it is the
            only route there can be - nothing cites a fact that did not exist.

The route is printed next to every card, because "this card names the deleted queue" and
"this card is about that repo" are not the same claim and should never read as one.

ADVISORY, LIKE DRIFT
--------------------
An estate change is not a defect in this repository. `diff` exits 0 with findings; it is
a worklist, not a gate. What would be a defect - a card citing a line that is not there -
already has a blocking gate in citations.py.
"""
from __future__ import annotations
import argparse, gzip, hashlib, json, re, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import citations as CIT

KB = HERE.parent
GRAPH = KB / "graph"
# Gzipped, and not out of tidiness: the uncompressed snapshot is 2.8 MB, slightly LARGER
# than the facts.jsonl it summarises, because a subject key like
# `CodeCalls:deployment-mgmt:com.e5.a.B->com.e5.c.D` is most of the fact. A summary that
# costs more to store than the thing it summarises does not get committed, and a snapshot
# nobody commits cannot diff across a sync. 2.8 MB -> ~300 KB.
SNAP = GRAPH / "estate-snapshot.json.gz"

# The EXTRACTED layers. flow-facts and citation-facts are authored or derived inside this
# repository - diffing them against a snapshot would report this repo's own edits as
# platform movement, which is the opposite of the question.
FACT_FILES = ("facts.jsonl", "async-facts.jsonl", "build-facts.jsonl", "consumer-build-facts.jsonl", "rpc-facts.jsonl", "gql-task-facts.jsonl", "code-facts.jsonl",
              "artifact-facts.jsonl", "cluster-link-facts.jsonl", "layer-facts.jsonl")

# Excluded from a subject's SHAPE. `commit` and `branch` change on every sync by
# definition; including them would mark all 6,900 subjects CHANGED and the tool would be
# deleted on its first real run. `line`/`path`/`id` are location, which is the whole point.
# `_src` is this module's own bookkeeping - which fact file a fact came from. Leaving it
# out of VOLATILE made the first rehearsal report all 267 of interface's ConfigKeys as
# CHANGED, because facts read from disk carried it and facts re-extracted in memory did
# not. A diff tool whose first real run says "everything changed" is a diff tool nobody
# runs twice.
VOLATILE = {"line", "path", "commit", "branch", "id", "system", "indexed_at", "_src"}
NOT_SHAPE = VOLATILE | {"kind", "component", "repo", "contract_key", "name", "props"}

# Facts from the async and build layers carry no `contract_key`. These are the fields that
# NAME such a subject; everything else about it is shape. `version` is deliberately NOT
# here for DependsOnArtifact - a version bump must read as the same subject CHANGED, not
# as one artifact vanishing and another appearing.
IDENT = {
    "TopicProduce": ("topic",), "TopicConsume": ("topic",),
    "QueuePoll": ("queue",), "QueueRef": ("queue",),
    "TaskRoute": ("task_name",), "TaskRouting": ("task_name",),
    "DependsOnArtifact": ("group", "artifact"), "Publishes": ("group", "artifact"),
}

# Kinds whose identifiers a human would actually write in a sentence. Used only by
# `report` to answer "what does the estate have that no card names". ProtoMessage and
# SdkType are left out not because they do not matter but because 977 of them are
# generated wire types and "undocumented" is not a meaningful thing to say about them.
SPOKEN = {"KafkaTopicRef", "EventType", "TopicProduce", "TopicConsume",
          # `VirtualQueue` was here and no longer exists as a kind: it was a queue
          # ENTITY built from a source literal, and 7 of 10 such literals named a queue
          # the platform does not run. `QueueLiteralInCode` replaces it and means
          # something different - a hardcoded name that bypasses config.
          "QueueLiteralInCode",
          "VirtualQueueConsumer", "VirtualQueuePoll", "QueuePoll", "QueueRef",
          "TaskRoute", "TaskRouting", "Table", "GrpcService", "WorkerRole", "DbBinding"}

# A path:line buried inside a key, e.g. RoutingRule's. Collapsed to the basename so the
# subject survives an edit above it.
_PATHLINE = re.compile(r'(?:[\w.@-]+/)*([\w.@-]+\.[A-Za-z]\w{0,8}):\d+')

# Tokens a card might name an identifier with. Kept wide on the card side and narrow on
# the estate side: a false LINK is cheap (a human glances at a card), a missed link is not.
_TOKEN = re.compile(r'[A-Za-z_][\w.$-]{3,}')

# Identifiers too generic to search for. Every one of these is a real extracted name that
# also occurs as an ordinary English word in the prose.
# Only some extractors mark a fact `props.test`. EventType does not, which put
# `e5.platform.event.workflow.notstarted` - a string in event-store's EventBuilderTest -
# on the list of identifiers no card documents, next to the real NOT_STARTED that
# interface and monitoring-service both emit. The path is the fallback signal.
_TEST_PATH = re.compile(r'(^|/)(tests?|__tests__)/|(^|/)test_|_test\.|Tests?\.java$|'
                        r'\.spec\.|src/test/')

_UNSEARCHABLE = {"topics", "topic", "queue", "status", "state", "config", "value", "name",
                 "key", "type", "task", "event", "events", "service", "message", "data"}


def _short(v) -> str:
    """A shape value: kept verbatim when a human could read it in a diff, hashed when not.

    Storing every value in full would make the snapshot as large as the fact files it
    summarises; storing only hashes would make the output `shape changed` and nothing
    else, which tells a reader to go and diff it by hand. Short values are the ones that
    carry the news - `1.3.4` -> `1.3.5`, `workflow-manager` -> `workflow-ops`.
    """
    s = v if isinstance(v, str) else json.dumps(v, sort_keys=True)
    if len(s) <= 60:
        return s
    return "sha1:" + hashlib.sha1(s.encode()).hexdigest()[:10]


def subject_of(f: dict) -> tuple[str, str, str]:
    kind = f.get("kind") or "?"
    owner = f.get("component") or f.get("repo") or "?"
    ids = IDENT.get(kind)
    if ids:
        key = ":".join(str(f.get(i) or "") for i in ids)
    else:
        key = f.get("contract_key") or f.get("name") or f.get("id") or ""
        key = _PATHLINE.sub(r'\1', key)
    return kind, owner, key


def shape_of(f: dict) -> dict:
    ids = set(IDENT.get(f.get("kind") or "", ()))
    d = {k: _short(v) for k, v in f.items() if k not in NOT_SHAPE and k not in ids}
    d.update({"props." + k: _short(v) for k, v in (f.get("props") or {}).items()})
    return d


# Kinds whose key does not contain the name a human would write. A KafkaTopicRef's key
# is the CONFIG KEY that holds the topic (`audit-service:topics`); the topic itself is in
# the props. Searching for `topics` found nothing, which read as "no card names any Kafka
# topic reference" and was an artefact of the key, not a fact about the cards.
_IDENT_PROP = {"KafkaTopicRef": ("literal", "env_var", "config_key")}


def _searchable(tok: str) -> str | None:
    tok = (tok or "").strip()
    if len(tok) < 5 or tok.lower() in _UNSEARCHABLE:
        return None
    # Not every extracted "name" is a name. An unresolved virtual queue is recorded as the
    # EXPRESSION that produces it - `TaskRoutingEndpoints.getDestinationVqName(taskTransport)`,
    # `#{scalingEstimatorConsumerConfig.topic}` - which is the honest thing for the
    # extractor to do and a nonsense thing to search a card for.
    if re.search(r'[^\w.$-]', tok):
        return None
    # must look like an identifier rather than a word: a separator or internal caps
    if not (re.search(r'[_.\-$]', tok) or re.search(r'[a-z][A-Z]', tok)):
        return None
    return tok


def _ident(kind: str, key: str, props: dict | None = None) -> str | None:
    """The human-facing token for a subject, or None if there is nothing searchable.

    The last colon-separated segment, because every key family puts the name last:
    `VirtualQueue:e5_platform_workflow_ops_vq`, `ConfigKey:audit-service:AUDIT_...`.
    """
    for pk in _IDENT_PROP.get(kind, ()):
        got = _searchable(str((props or {}).get(pk) or ""))
        if got:
            return got
    return _searchable(key.rsplit(":", 1)[-1])


def load_facts() -> list[dict]:
    out = []
    for name in FACT_FILES:
        p = GRAPH / name
        if not p.is_file():
            continue
        for line in p.open():
            line = line.strip()
            if not line:
                continue
            f = json.loads(line)
            f["_src"] = name
            out.append(f)
    return out


def index(facts: list[dict] | None = None) -> dict:
    """subject -> {kind, owner, key, ident, shapes, paths, n}."""
    idx: dict = {}
    for f in facts if facts is not None else load_facts():
        # Authored facts are written in THIS repository. Diffing them would report our
        # own edits as platform movement. The filter lives here rather than in
        # load_facts() so that it also holds for a caller passing facts in directly -
        # which is how the rehearsal harness and the tests use this.
        if str(f.get("provenance", "")).startswith("authored"):
            continue
        kind, owner, key = subject_of(f)
        sid = f"{kind}|{owner}|{key}"
        e = idx.setdefault(sid, {"kind": kind, "owner": owner, "key": key,
                                 "ident": _ident(kind, key, f.get("props")),
                                 "repo": f.get("repo"),
                                 "shapes": [], "paths": [], "n": 0, "test": 0})
        e["n"] += 1
        if ((f.get("props") or {}).get("test") is True
                or _TEST_PATH.search(f.get("path") or "")):
            e["test"] += 1
        sh = shape_of(f)
        if sh not in e["shapes"]:
            e["shapes"].append(sh)
        p = f.get("path")
        if p and p not in e["paths"]:
            e["paths"].append(p)
    for e in idx.values():
        # test-only: every fact behind this subject came from a test tree. Still a real
        # subject and still diffed - a deleted test fixture is estate movement - but it is
        # not something anyone failed to document.
        e["test_only"] = e["test"] == e["n"]
        e["shapes"].sort(key=lambda d: json.dumps(d, sort_keys=True))
        e["paths"].sort()
    return idx


# ------------------------------------------------------------------ card implication
def card_tokens() -> dict:
    """token -> set of cards containing it. One pass over every card, not one per subject."""
    out: dict = {}
    for p in CIT.cards():
        rel = str(p.relative_to(KB))
        for t in set(_TOKEN.findall(p.read_text(errors="replace"))):
            out.setdefault(t, set()).add(rel)
    return out


def card_citations() -> dict:
    """(repo, path) -> set of cards that cite a line in that file."""
    out: dict = {}
    p = GRAPH / "citation-facts.jsonl"
    if not p.is_file():
        return out
    for line in p.open():
        d = json.loads(line)
        out.setdefault((d["repo"], d["path"]), set()).add(d["card"])
    return out


def card_repos() -> dict:
    """repo id -> set of cards whose front-matter declares it."""
    by_id, alias = CIT._registry_index()
    owners = CIT._owner_to_repo()
    out: dict = {}
    for p in CIT.cards():
        fm = CIT.frontmatter(p.read_text(errors="replace"))
        for r in CIT.card_repos(fm, alias, owners):
            out.setdefault(r, set()).add(str(p.relative_to(KB)))
    return out


class Links:
    def __init__(self):
        self.tok = card_tokens()
        self.cite = card_citations()
        self.repo = card_repos()
        self._blobs = {str(p.relative_to(KB)): p.read_text(errors="replace")
                       for p in CIT.cards()}
        self._sub: dict = {}

    def named(self, ident: str | None) -> set:
        """Cards naming this identifier - exact token first, substring as a fallback.

        A dotted name is often written in a card with something appended or a wildcard
        after it (`e5.platform.event.workflow.*`), which tokenises differently and made
        15 EventTypes read as undocumented when the card plainly discusses them. The
        fallback costs a scan per MISS only, and it errs toward claiming a card does
        mention the thing - the safe direction for a report whose failure mode is nagging
        about documentation that already exists.
        """
        if not ident:
            return set()
        hit = self.tok.get(ident)
        if hit:
            return hit
        if ident not in self._sub:
            self._sub[ident] = {c for c, b in self._blobs.items() if ident in b}
        return self._sub[ident]

    def of(self, e: dict) -> list[tuple[str, str]]:
        """[(route, card)], strongest route per card only."""
        best: dict = {}
        for c in self.named(e.get("ident")):
            best[c] = "names"
        for p in e.get("paths") or []:
            for c in self.cite.get((e.get("repo"), p), ()):
                best.setdefault(c, "cites")
        for c in self.repo.get(e.get("repo") or "\0", ()):
            best.setdefault(c, "describes")
        order = {"names": 0, "cites": 1, "describes": 2}
        return sorted(((r, c) for c, r in best.items()), key=lambda x: (order[x[0]], x[1]))


# ------------------------------------------------------------------------- snapshot
def snapshot(idx: dict) -> dict:
    by_id, _alias = CIT._registry_index()
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "refs": {r: (by_id[r].get("commit") or by_id[r].get("branch"))
                 for r in sorted(by_id)},
        "subjects": {sid: {k: v for k, v in
                           (("ident", e["ident"]), ("n", e["n"]),
                            ("shapes", e["shapes"]), ("repo", e["repo"]),
                            ("paths", e["paths"][:2])) if v}
                     for sid, e in sorted(idx.items())},
    }


def load_snapshot() -> dict | None:
    if SNAP.is_file():
        with gzip.open(SNAP, "rt") as fh:
            return json.load(fh)
    plain = SNAP.with_suffix("")          # a snapshot written before it was compressed
    if plain.is_file():
        return json.loads(plain.read_text())
    return None


def _shape_delta(old: list, new: list) -> list[str]:
    """What actually differs, field by field, so the worklist says WHAT changed."""
    o = {json.dumps(d, sort_keys=True): d for d in old}
    n = {json.dumps(d, sort_keys=True): d for d in new}
    gone, came = [o[k] for k in o if k not in n], [n[k] for k in n if k not in o]
    out = []
    # the common case is exactly one shape each way: name the fields, not the dicts
    if len(gone) == 1 and len(came) == 1:
        a, b = gone[0], came[0]
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                out.append(f"{k}: {a.get(k, '(absent)')} -> {b.get(k, '(absent)')}")
        return out
    for d in gone:
        out.append("- " + json.dumps(d, sort_keys=True)[:120])
    for d in came:
        out.append("+ " + json.dumps(d, sort_keys=True)[:120])
    return out


def diff(idx: dict, snap: dict) -> list[dict]:
    old = snap.get("subjects") or {}
    out = []
    for sid in sorted(set(old) | set(idx)):
        kind, owner, key = sid.split("|", 2)
        if sid not in old:
            out.append({"verdict": "NEW", "subject": sid, "kind": kind, "owner": owner,
                        "key": key, "detail": []})
        elif sid not in idx:
            e = old[sid]
            out.append({"verdict": "GONE", "subject": sid, "kind": kind, "owner": owner,
                        "key": key, "ident": e.get("ident"), "repo": e.get("repo"),
                        "paths": e.get("paths") or [], "detail": []})
        elif old[sid].get("shapes") != idx[sid]["shapes"]:
            out.append({"verdict": "CHANGED", "subject": sid, "kind": kind, "owner": owner,
                        "key": key,
                        "detail": _shape_delta(old[sid].get("shapes") or [],
                                               idx[sid]["shapes"])})
    return out


# ---------------------------------------------------------------------------- views
def _print_changes(changes: list[dict], idx: dict, links: Links, limit: int) -> None:
    by = {"GONE": [], "CHANGED": [], "NEW": []}
    for c in changes:
        by[c["verdict"]].append(c)
    for verdict in ("GONE", "CHANGED", "NEW"):
        rows = by[verdict]
        if not rows:
            continue
        print(f"\n{verdict}  ({len(rows)})")
        for c in rows[:limit]:
            print(f"  {c['kind']:18} {c['owner']:20} {c['key'][:70]}")
            for d in c["detail"][:4]:
                print(f"      {d[:110]}")
            e = idx.get(c["subject"]) or {
                "ident": c.get("ident"), "repo": c.get("repo"),
                "paths": c.get("paths") or []}
            cards = links.of(e)
            strong = [x for x in cards if x[0] != "describes"]
            for route, card in (strong or cards)[:4]:
                print(f"      [{route}] {card}")
            if not cards:
                print("      [no card] nothing in the knowledge base is linked to this")
        if len(rows) > limit:
            print(f"  ... {len(rows) - limit} more (--limit {len(rows)} to see them)")


def cmd_diff(limit: int) -> int:
    idx = index()
    snap = load_snapshot()
    if snap is None:
        print(f"no snapshot at {SNAP.relative_to(KB)}.")
        print(f"  {len(idx)} subjects in the estate right now.")
        print("  `python3 tools/estate.py accept` records them; the next sync diffs "
              "against that.")
        return 0
    changes = diff(idx, snap)
    print(f"snapshot {snap.get('generated')}  ->  now")
    print(f"{len(snap.get('subjects') or {})} subjects then, {len(idx)} now")
    moved = [r for r, c in (snap.get("refs") or {}).items()
             if c != (CIT._registry_index()[0].get(r) or {}).get("commit")]
    if moved:
        print(f"{len(moved)} repo(s) moved since: {', '.join(sorted(moved)[:8])}")
    if not changes:
        print("\nnone changed")
        return 0
    _print_changes(changes, idx, Links(), limit)
    print(f"\n{len(changes)} subject(s) changed. This is a worklist, not a failure: "
          f"re-read the cards above, then `estate accept`.")
    return 0


def cmd_report() -> int:
    idx = index()
    links = Links()
    kinds: dict = {}
    for e in idx.values():
        k = kinds.setdefault(e["kind"], {"n": 0, "named": 0, "spoken": 0})
        k["n"] += 1
        if e["kind"] in SPOKEN and not e["test_only"]:
            k["spoken"] += 1
            if links.named(e["ident"]):
                k["named"] += 1
    print(f"{len(idx)} subjects across {len(kinds)} kinds, "
          f"from {sum(1 for f in FACT_FILES if (GRAPH / f).is_file())} fact layers\n")
    print(f"  {'kind':22} {'subjects':>9}  {'named by a card':>16}")
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]["n"]):
        named = f"{v['named']}/{v['spoken']}" if v["spoken"] else "-"
        print(f"  {k:22} {v['n']:9}  {named:>16}")
    # the half that is answerable with no snapshot: estate the knowledge base never names
    silent = sorted((e["kind"], e["owner"], e["ident"]) for e in idx.values()
                    if e["kind"] in SPOKEN and e["ident"] and not e["test_only"]
                    and not links.named(e["ident"]))
    print(f"\n{len(silent)} identifier(s) in a spoken kind that no card names:")
    for kind, owner, ident in silent[:70]:
        print(f"  {kind:22} {owner:20} {ident}")
    if len(silent) > 70:
        print(f"  ... {len(silent) - 70} more")
    snap = load_snapshot()
    print(f"\nsnapshot: {snap.get('generated') if snap else '(none - run `accept`)'}")
    return 0


def cmd_accept() -> int:
    idx = index()
    snap = snapshot(idx)
    SNAP.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(SNAP, "wt") as fh:
        json.dump(snap, fh, sort_keys=True)
    print(f"{len(idx)} subjects -> {SNAP.relative_to(KB)} "
          f"({SNAP.stat().st_size // 1024} KB)")
    return 0


def cmd_subjects(kind: str | None) -> int:
    idx = index()
    for sid, e in sorted(idx.items()):
        if kind and e["kind"] != kind:
            continue
        print(f"{e['kind']:20} {e['owner']:20} {e['n']:3}  {e['key'][:80]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="report",
                    choices=["report", "diff", "check", "accept", "subjects"])
    ap.add_argument("--kind")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "accept":
        return cmd_accept()
    if a.cmd == "subjects":
        return cmd_subjects(a.kind)
    if a.cmd in ("diff", "check"):
        return cmd_diff(a.limit)
    return cmd_report()


if __name__ == "__main__":
    sys.exit(main())

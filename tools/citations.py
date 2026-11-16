#!/usr/bin/env python3
"""The evidence gate: every `file.java:113` in the knowledge base, checked.

    python3 tools/citations.py report   coverage, by card and by failure class
    python3 tools/citations.py check    validate; exit 1 on an E-class finding
    python3 tools/citations.py bless    record anchors -> graph/citation-anchors.json
    python3 tools/citations.py drift    which cards the latest sync invalidated
    python3 tools/citations.py facts    -> graph/citation-facts.jsonl

WHAT WAS UNCHECKED
------------------
The knowledge base makes 1,636 citations of the form `<path>:<line>`. Before this
module, not one of them was read by any line of Python. `check_card_provenance.py`
verifies that a card cites a commit the registry indexes - a card-level claim - and
stops there. Whether `TaskController.java:113` is a real line in a real file at that
commit was nobody's job.

That is the defect this harness exists to reject, one level down. A card pinned to the
right commit, asserting the right thing, pointing at a line that moved two releases ago
is indistinguishable from a correct card to every reader and every tool. It reads as
proof and is decoration.

TWO QUESTIONS, NOT ONE
----------------------
`check` asks: does this citation resolve AT THE COMMIT THE CARD DECLARES? A failure
here means the card was wrong when written, or has been edited since without being
re-verified. It is an error.

`drift` asks a different and more useful question: the registry has moved on - has the
cited LINE CHANGED between the commit the card was verified at and the commit the
registry indexes now? That is not an error; the card has not become false, it has become
unproven. The output is a re-verification worklist, which is the thing a human actually
needs after a sync and the thing nothing here produced before.

The anchor is a hash of the cited line's text, recorded by `bless` in
graph/citation-anchors.json. Text and not just the number, because the common failure is
an insertion above: the line number still exists, still has content, and now points at
something else entirely. A line-count check cannot see that; a content hash can.

ATTRIBUTION, AND WHY SOME CITATIONS CANNOT BE CHECKED
-----------------------------------------------------
A citation says `polling_service.py:109`. To check it you need the repo, which the text
does not say. The ladder, most specific first:

  1. the path starts with a KB directory (`catalog/`, `tools/`, ...) -> this repo,
     verified against the working tree
  2. the path's leading segment names a repo (`workflow-ops/src/...`, or the remote
     basename `e5-platform-ss-workflow-ops/...`) -> that repo, segment stripped
  3. the card's `repo:` frontmatter
  4. the card's `owner:` frontmatter, resolved through the catalog to a repo

A citation that exhausts the ladder is UNATTRIBUTED. It is reported as a warning with a
count, never silently dropped and never counted as passing - an unverifiable citation
and a verified one must not look the same, which is the whole argument of this file.
"""
from __future__ import annotations
import argparse, collections, hashlib, json, os, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mirror as M

KB = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("E5_CACHE", Path.home() / ".e5" / "cache"))
ANCHORS = KB / "graph" / "citation-anchors.json"
FACTS = KB / "graph" / "citation-facts.jsonl"

# Where cards live. joins/ and contracts/ are under knowledge/, so two roots cover all.
CARD_ROOTS = ("knowledge",)

# Extensions a citation may carry. Deliberately a closed list: an open one turns every
# `v1.2.3` and every `10:30` in prose into a citation and the coverage number becomes a
# lie. Add an extension when a real card needs it.
EXTS = ("java", "py", "ts", "tsx", "js", "go", "yaml", "yml", "gradle", "proto",
        "sql", "toml", "groovy", "json", "tf", "sh", "kt", "xml", "graphqls", "cfg")

_CITE = re.compile(r'(?<![\w/.-])([\w][\w./\-]*\.(?:' + "|".join(EXTS) + r'))'
                   r'\s*:\s*(\d+)(?:\s*-\s*(\d+))?(?![\w.])')

# A line reference with no file: `(`:1345-1465`)`. Anchored to backticks on both sides,
# because an unanchored `:\d+` matches a port number, a timestamp and a proto field tag.
_BARE = re.compile(r'`:(\d+)(?:\s*-\s*(\d+))?`')

# A leading path segment equal to one of these means the citation points INTO this
# knowledge base, not into a service repo.
KB_DIRS = {"catalog", "tools", "knowledge", "graph", "ci", "docs", "console",
           "constitution", "schemas", "runbooks", "plugin", "disagreements",
           "contests", "releases", "deploy"}


# --------------------------------------------------------------------------- loading

def _registry_index():
    """id -> repo row, plus every alias a card might use for it."""
    _root, repos = M.registry()
    by_id, alias = {}, {}
    for r in repos:
        by_id[r["id"]] = r
        alias[r["id"]] = r["id"]
        rem = (r.get("remote") or "").rsplit("/", 1)[-1]
        if rem.endswith(".git"):
            rem = rem[:-4]
        if rem:
            alias[rem] = r["id"]
        # The checkout DIRECTORY is a third name, and it is not always either of the
        # other two: event-store's is `event-strore`. Cards quote workspace paths
        # verbatim, so the typo in the directory name is in the cards too - correctly,
        # because that is what the directory is called.
        local = (r.get("path") or "").rstrip("/").rsplit("/", 1)[-1]
        if local:
            alias.setdefault(local, r["id"])
    return by_id, alias


def _owner_to_repo():
    """Catalog component name -> registry id, for cards that say `owner:` and not
    `repo:`. The api cards do exactly this and they are the densest citers here."""
    try:
        import catalog as C
        cat = C.load()
    except Exception:
        return {}
    out = {}
    for name, c in (cat.get("Component") or {}).items():
        src = c.get("source")
        rid = ((src or {}).get("repo") or "").strip() if isinstance(src, dict) else ""
        if rid and rid != "UNKNOWN":
            out[name] = rid
    return out


def _unindexed_names() -> set:
    """Components the catalog declares but does not index.

    A card may legitimately cite `LambdaHandler.java` in a repo nobody mirrors. Without
    this the gate calls that a broken citation, which is worse than silence: it says we
    looked and it was wrong, when the truth is we never looked. The catalog already had
    the vocabulary for this (`unindexed: true`); nothing read it."""
    try:
        import catalog as C
        cat = C.load()
    except Exception:
        return set()
    out = set()
    for name, c in (cat.get("Component") or {}).items():
        if not c.get("unindexed"):
            continue
        out.add(name)
        src = c.get("source") if isinstance(c.get("source"), dict) else {}
        rem = (src.get("remote") or "").rsplit("/", 1)[-1]
        if rem.endswith(".git"):
            out.add(rem[:-4])
    return out


def frontmatter(txt: str) -> dict:
    m = re.match(r'^---\n(.*?)\n---', txt, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        mm = re.match(r'(\w+):\s*(.*)$', line)
        if mm:
            fm[mm.group(1)] = mm.group(2).strip()
    return fm


# `status: in-flight` marks a design document for work that is on no release branch -
# the smart-safe-deployment documents were written against an uncommitted working copy.
# Its file:line references point at code no release contains, so checking them against
# the registry commit is a category error: every one would either fail or, worse,
# "verify" against an unrelated line that happens to exist. Such a document is a record of
# intent, not a claim about the platform, and is excluded from citation checking. Nothing
# else is: a card that DESCRIBES in-flight work while citing released code is still checked.
IN_FLIGHT = "in-flight"


def is_in_flight(card: Path) -> bool:
    return frontmatter(card.read_text(errors="replace")).get("status") == IN_FLIGHT


def cards() -> list[Path]:
    out = []
    for root in CARD_ROOTS:
        out += sorted(p for p in (KB / root).rglob("*.md") if not is_in_flight(p))
    return out


# ------------------------------------------------------------------------ git access

class Trees:
    """Cached `ls-tree` per (repo, ref), plus blob reads. One subprocess per tree."""

    def __init__(self):
        self._t: dict = {}
        self._blob: dict = {}

    def tree(self, rid: str, ref: str):
        key = (rid, ref)
        if key in self._t:
            return self._t[key]
        bare = CACHE / (rid + ".git")
        res = None
        if bare.is_dir() and ref:
            pr = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r",
                                 "--name-only", ref],
                                capture_output=True, text=True)
            if pr.returncode == 0:
                paths = pr.stdout.splitlines()
                idx = collections.defaultdict(list)
                for p in paths:
                    idx[p.rsplit("/", 1)[-1]].append(p)
                res = (set(paths), idx)
        self._t[key] = res
        return res

    def lines(self, rid: str, ref: str, path: str) -> list[str] | None:
        key = (rid, ref, path)
        if key in self._blob:
            return self._blob[key]
        bare = CACHE / (rid + ".git")
        out = None
        pr = subprocess.run(["git", "-C", str(bare), "show", f"{ref}:{path}"],
                            capture_output=True)
        if pr.returncode == 0:
            out = pr.stdout.decode("utf-8", "replace").splitlines()
        self._blob[key] = out
        return out

    def kb_lines(self, path: str) -> list[str] | None:
        p = KB / path
        if not p.is_file():
            return None
        return p.read_text(errors="replace").splitlines()


# -------------------------------------------------------------------------- the scan

class Cite:
    __slots__ = ("card", "raw", "line_no", "path", "start", "end", "repo", "ref",
                 "resolved", "status", "detail", "anchor", "near", "inherited")

    def __init__(self, card, raw, line_no, path, start, end):
        self.card, self.raw, self.line_no = card, raw, line_no
        self.path, self.start, self.end = path, start, end
        self.repo = self.ref = self.resolved = self.near = None
        self.inherited = False
        self.status = "PENDING"
        self.detail = ""
        self.anchor = None

    @property
    def key(self) -> str:
        return f"{self.card}#{self.raw}@{self.line_no}"


def extract(card: Path, alias: dict | None = None) -> list[Cite]:
    """Every `<path>:<line>` in the card, with the repo named nearest before it.

    Cards write the repo in prose, not in a field: "Producers emit the literals from
    `AuditEventData.OUTCOME_SUCCESS` (webhook-service `AuditEventData.java:71-73`)".
    Two repos have an AuditEventData.java and the card says which. Ignoring the sentence
    and falling back to the card's subject picked the other one and reported a correct
    citation as an off-the-end line number - a false accusation produced by not reading
    what the card actually said.
    """
    txt = card.read_text(errors="replace")
    rel = str(card.relative_to(KB))
    out, last = [], None
    for n, line in enumerate(txt.splitlines(), 1):
        for m in sorted(list(_CITE.finditer(line)) + list(_BARE.finditer(line)),
                        key=lambda x: x.start()):
            if m.re is _BARE:
                # `:1345-1465` on its own means "same file as a moment ago". That is how
                # a reader takes it and it is 855 more pieces of evidence - more than
                # half as many again as the qualified ones. Inheriting the last named
                # file is the only reading; the alternative is not checking them.
                if last is None:
                    continue
                path, a, b = last, int(m.group(1)), m.group(2)
                inherited = True
            else:
                path, a, b = m.group(1), int(m.group(2)), m.group(3)
                last, inherited = tidy(path), False
            c = Cite(rel, m.group(0).strip(), n, tidy(path), a,
                     int(b) if b else None)
            c.inherited = inherited
            if alias:
                c.near = _nearest_repo(line[:m.start()], alias)
            out.append(c)
    return out


def _nearest_repo(before: str, alias: dict) -> str | None:
    """The last repo name mentioned before the citation, on the same line."""
    hit = None
    for m in re.finditer(r'[\w][\w.-]*', before):
        tok = m.group(0)
        if tok in alias:
            hit = alias[tok]
    return hit


def card_repos(fm: dict, alias: dict, owners: dict) -> list[str]:
    """Every repo this card could be talking about, from its frontmatter.

    Cards name their repo four different ways and none of them is universal: service
    cards say `repo:`, api cards say `subsystem:` or `owner:`, table cards say `owner:`,
    and disagreement cards say nothing at all - they carry a `sources:` block of
    workspace-relative paths with the repo directory buried three levels in. So this
    gathers candidates rather than deciding, and a citation that matches in exactly one
    candidate is attributed to it. A card that cites two repos is normal here; a
    citation that is ambiguous ACROSS them is reported, not guessed.
    """
    out = []
    for key, val in fm.items():
        for tok in re.split(r'[\s,\[\]]+', str(val)):
            tok = tok.strip().strip("`'\"")
            if not tok:
                continue
            if tok in alias:
                out.append(alias[tok])
            elif tok in owners:
                out.append(owners[tok])
            elif "/" in tok:
                rid, _rest = split_repo_prefix(tok, alias)
                if rid:
                    out.append(rid)
    seen, uniq = set(), []
    for r in out:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def tidy(path: str) -> str:
    """Cards elide the boring middle: `e5-ss-dynamic-scaling/.../dto/Request.java`.
    Drop the ellipsis and let what remains be matched as a suffix."""
    segs = [s for s in path.split("/") if s not in ("...", ".")]
    return "/".join(segs)


def split_repo_prefix(path: str, alias: dict):
    """`.../e5-platform-ss-credential-monitor/src/main/X.java` -> (credential-monitor,
    src/main/X.java). The LAST matching segment wins: workspace paths nest a repo
    directory inside directories that may themselves look like repo names."""
    segs = path.split("/")
    hit = None
    for i, s in enumerate(segs[:-1]):
        if s in alias:
            hit = i
    if hit is None:
        return None, path
    return alias[segs[hit]], "/".join(segs[hit + 1:])


# A path segment shaped like one of the estate's repositories but absent from the
# registry: the citation is honest, we simply do not index that repo. Reported as its
# own class so "we never looked" never reads as "we looked and it was wrong".
_REPOISH = re.compile(r'^(?:e5|rf)-[\w-]+$')


def attribute(c: Cite, alias: dict) -> None:
    """Does the citation itself say which repo? Sets c.repo, or leaves it None."""
    head = c.path.split("/", 1)[0]
    # ...and the file must actually BE here. `schemas/` is a directory in this knowledge
    # base and also a directory inside monitoring-service; a name match alone sent that
    # card's citation to the wrong tree and reported a correct citation as broken.
    if "/" in c.path and head in KB_DIRS and (KB / c.path).is_file():
        c.repo = "__kb__"
        return
    rid, rest = split_repo_prefix(c.path, alias)
    if rid:
        c.repo, c.path = rid, rest
        return
    for seg in c.path.split("/")[:-1]:
        if _REPOISH.match(seg):
            c.repo = "__unindexed__"
            c.detail = f"{seg} is not a repo this knowledge base indexes"
            return


def _set_ref(c: Cite, fm: dict, by_id: dict) -> None:
    row = by_id.get(c.repo) or {}
    # The card's own commit wins over the registry's, but only when the card is ABOUT
    # that repo. A disagreement card pinned to credential-monitor's commit must not
    # have that commit applied to the monitoring-service file it compares against.
    own = (fm.get("repo") or fm.get("subsystem") or fm.get("owner") or "")
    same = own and (own == c.repo or own.endswith(c.repo))
    c.ref = ((fm.get("commit") or fm.get("branch")) if same else None) \
        or row.get("commit") or row.get("branch")


def locate(c: Cite, trees: Trees) -> list[str] | None:
    """Find c.path inside c.repo at c.ref. Returns the file's lines, or None.

    Three match shapes, narrowest first: the exact repo-relative path, a path suffix
    (`utility/ConfigsLoader.java` for `src/main/java/com/e5/utility/ConfigsLoader.java`),
    and a bare basename. A shape matching more than one file is NO match - this
    function only ever reports certainty; the caller decides what ambiguity means.
    """
    t = trees.tree(c.repo, c.ref)
    if t is None:
        return None
    paths, idx = t
    if c.path in paths:
        c.resolved = c.path
    else:
        base = c.path.rsplit("/", 1)[-1]
        cands = ([p for p in paths if p.endswith("/" + c.path)] if "/" in c.path
                 else idx.get(base, []))
        if len(cands) != 1:
            return None
        c.resolved = cands[0]
    return trees.lines(c.repo, c.ref, c.resolved)


def check_lines(c: Cite, ls: list[str]) -> None:
    last = c.end or c.start
    if c.start < 1 or last > len(ls):
        c.status = "E_LINE"
        c.detail = f"{c.resolved} has {len(ls)} lines, citation reaches line {last}"
        return
    c.status = "OK"
    c.anchor = hashlib.sha1(ls[c.start - 1].strip().encode()).hexdigest()[:12]


def primary_repo(fm: dict, alias: dict, owners: dict, cands: list[str]) -> str | None:
    """The one repo a card is ABOUT, as opposed to the ones it merely mentions.

    This is the tiebreak that makes `build.gradle:25` checkable. Three repos have a
    build.gradle; the card is platform-core's, so the citation is platform-core's. The
    alternative is 229 ambiguous citations that every reader can resolve at a glance,
    which trains people to ignore the warning column."""
    for key in ("repo", "subsystem", "owner", "component"):
        v = (fm.get(key) or "").strip()
        if v in alias:
            return alias[v]
        if v in owners:
            return owners[v]
    return cands[0] if cands else None


def resolve(c: Cite, fm: dict, by_id: dict, cands: list[str], all_ids: list[str],
            trees: Trees, primary: str | None = None,
            card_unindexed: str | None = None) -> None:
    """Three widening pools, the same acceptance rule in each.

      1. the repo the citation names outright
      2. the repos the card names in its frontmatter
      3. every indexed repo

    Widening is only safe because the rule never loosens: exactly one repo must hold
    exactly one matching file. `Main.java` is in nine repos and stays ambiguous, which
    is the correct answer and not a failure to try. Going straight to pool 3 would be
    wrong - two repos can both own a `polling_service.py`, and the card's own
    frontmatter is the tiebreak that makes the wider search safe at all.
    """
    if c.repo == "__kb__":
        c.ref = "worktree"
        ls = trees.kb_lines(c.path)
        if ls is None:
            c.status, c.detail = "E_NO_FILE", "not in this knowledge base"
        else:
            c.resolved = c.path
            check_lines(c, ls)
        return
    if c.repo == "__unindexed__":
        c.status = "W_UNINDEXED_REPO"
        return

    pools = [p for p in ([c.repo] if c.repo else [],
                         [c.near] if c.near and c.near != c.repo else [],
                         [r for r in cands if r not in (c.repo, c.near)],
                         all_ids) if p]
    named = c.repo
    for depth, pool in enumerate(pools):
        hits = []
        for rid in pool:
            probe = Cite(c.card, c.raw, c.line_no, c.path, c.start, c.end)
            probe.repo = rid
            _set_ref(probe, fm, by_id)
            found = locate(probe, trees)
            if found is not None:
                hits.append((probe, found))
        if not hits:
            continue
        # The LINE NUMBER is part of the citation, so it is also evidence about which
        # file was meant. Two repos have a Constants.java; one is 17 lines long and the
        # citation says 72. Ignoring that and taking the card's subject reported a
        # correct citation as an off-the-end line - the number was there to be read.
        fits = [h for h in hits if (c.end or c.start) <= len(h[1])]
        pick = None
        if len(fits) == 1:
            pick = fits[0]
        elif primary and len([h for h in (fits or hits) if h[0].repo == primary]) == 1:
            pick = next(h for h in (fits or hits) if h[0].repo == primary)
        elif len(hits) == 1 and (named or depth == len(pools) - 1):
            pick = hits[0]           # one candidate, no better option: report its verdict
        if pick is None:
            if not fits and depth < len(pools) - 1:
                continue             # nothing here fits; a wider pool may hold the file
            c.status = "W_AMBIGUOUS"
            c.detail = (f"{len(hits)} repos hold a file matching {c.path!r}: "
                        + ", ".join(sorted(p.repo for p, _ in hits)[:6]))
            return
        probe, ls = pick
        c.repo, c.ref, c.resolved = probe.repo, probe.ref, probe.resolved
        check_lines(c, ls)
        if c.status != "OK":
            return
        if named and c.repo != named:
            c.detail = f"not in {named}; found in {c.repo}"
        elif len(hits) > 1:
            c.detail = f"{len(hits)} repos match; resolved to {c.repo}"
        elif depth == len(pools) - 1 and not named and not c.near:
            c.detail = "repo inferred estate-wide; the card never names it"
        return
    home = named or c.near or primary
    if home:
        c.repo = home
        _set_ref(c, fm, by_id)
    _no_match(c, home, trees, card_unindexed)


def _no_match(c: Cite, home: str | None, trees: Trees,
              card_unindexed: str | None) -> None:
    """Nothing matched. Decide whether that accuses the card or only abstains.

    The discriminator is whether the citation carries a PATH. `utility/ConfigsLoader.java`
    names a directory; the repo the card is about has a `ConfigsLoader.java` and it is not
    in `utility/`, so the card states a path that does not exist and that is an error. A
    bare `LambdaHandler.java` that is in no indexed repo says nothing about the card - it
    is very likely in a repo nobody here mirrors, and calling that a broken citation is
    worse than silence, because it reports "we looked and it was wrong" when the truth is
    "we never looked".
    """
    base = c.path.rsplit("/", 1)[-1]
    if "/" in c.path and home:
        t = trees.tree(home, c.ref)
        if t and t[1].get(base):
            c.status = "E_WRONG_PATH"
            c.detail = (f"{home} has {base} at " +
                        ", ".join(t[1][base][:3]) + f", not at {c.path!r}")
            return
    c.status = "W_NO_MATCH"
    c.detail = (f"{base} is in no indexed repo"
                + (f"; this card names {card_unindexed}, which the catalog declares "
                   f"unindexed" if card_unindexed else ""))


# Continuation references are scored in their own vocabulary and never folded into the
# headline. `deployment-mgmt-grpc-api.md` names a 1,466-line proto, then a Main.java, then
# goes on quoting proto line numbers - so "the last file named" is NOT reliably the file a
# bare `:1345` continues from. Resolving them anyway and calling the in-range ones verified
# would inflate the coverage figure with guesses, which is the exact failure this module
# exists to prevent. They are counted, reported, and kept out of the anchors.
_INHERITED = {"OK": "I_FITS", "E_LINE": "I_OUT_OF_RANGE"}


def _demote(c: Cite) -> None:
    c.status = _INHERITED.get(c.status, "I_UNRESOLVED")


def scan() -> list[Cite]:
    by_id, alias = _registry_index()
    owners = _owner_to_repo()
    unindexed = _unindexed_names()
    all_ids = sorted(by_id)
    trees = Trees()
    out = []
    for card in cards():
        cs = extract(card, alias)
        if not cs:
            continue
        fm = frontmatter(card.read_text(errors="replace"))
        cands = card_repos(fm, alias, owners)
        primary = primary_repo(fm, alias, owners, cands)
        named_unindexed = next(
            (n for n in unindexed
             for v in fm.values()
             if n in re.split(r'[\s,\[\]]+', str(v))), None)
        for c in cs:
            attribute(c, alias)
            resolve(c, fm, by_id, cands, all_ids, trees, primary, named_unindexed)
            if c.inherited:
                _demote(c)
            out.append(c)
    return out


# ----------------------------------------------------------------------------- drift

def _current_ref(rid: str, by_id: dict) -> str | None:
    row = by_id.get(rid) or {}
    return row.get("commit") or row.get("branch")


def drift(anchors: dict) -> list[dict]:
    """Cited lines whose CONTENT changed between the anchored commit and the one the
    registry indexes now. Not errors - a worklist of cards to re-read."""
    by_id, _alias = _registry_index()
    trees = Trees()
    moved, seen = [], set()
    for key, a in sorted(anchors.get("anchors", {}).items()):
        # A card may cite the same line twice in different sentences. The worklist is
        # "re-read this line", and saying it twice does not make it twice as true.
        spot = (a["card"], a["repo"], a["path"], a["line"])
        if spot in seen:
            continue
        seen.add(spot)
        rid = a["repo"]
        if rid == "__kb__":
            continue
        now = _current_ref(rid, by_id)
        if not now or now == a["ref"]:
            continue
        ls = trees.lines(rid, now, a["path"])
        if ls is None:
            t = trees.tree(rid, now)
            if t is None:
                continue
            moved.append({**a, "key": key, "now": now, "why": "file no longer exists"})
            continue
        if a["line"] > len(ls):
            moved.append({**a, "key": key, "now": now,
                          "why": f"file is now {len(ls)} lines, citation "
                                 f"reaches {a['line']}"})
            continue
        h = hashlib.sha1(ls[a["line"] - 1].strip().encode()).hexdigest()[:12]
        if h != a["sha"]:
            moved.append({**a, "key": key, "now": now,
                          "why": "that line now reads: "
                                 + (ls[a["line"] - 1].strip()[:70] or "(blank)")})
    return moved


def load_anchors() -> dict:
    if ANCHORS.is_file():
        return json.loads(ANCHORS.read_text())
    return {"anchors": {}}


def bless(cs: list[Cite]) -> dict:
    rec = {}
    for c in cs:
        if c.status != "OK" or c.repo == "__kb__" or c.inherited:
            continue
        rec[c.key] = {"card": c.card, "repo": c.repo, "ref": c.ref,
                      "path": c.resolved, "line": c.start, "sha": c.anchor}
    return {"generated_by": "tools/citations.py bless", "anchors": rec}


def _split_key(key: str) -> tuple[str, str, int]:
    """`card#raw@line_no` -> (card, raw, line_no)."""
    head, _, ln = key.rpartition("@")
    card, _, raw = head.partition("#")
    return card, raw, int(ln) if ln.isdigit() else 0


def bless_new_only(cs: list[Cite], existing: dict) -> tuple[dict, dict]:
    """Anchor citations that have never been anchored; carry every other anchor forward.

    A key is `card#raw@line_no`, and `line_no` is the citation's line IN THE CARD. So any
    edit that shifts a card's lines - a dated note at the top, or sync_card_provenance
    adding a frontmatter key - gives every citation below it a new key. Plain `bless` then
    anchors them at TODAY'S content, which re-verifies by side door exactly the drifted,
    unread lines `drift` exists to report: on 2026-11-13, 135 of 172 "unanchored"
    citations were old ones whose cards had merely shifted.

    So match on (card, raw) - the citation's text - in line order. A citation whose text
    already has an anchor in that card is RE-KEYED with its original anchor, so drift still
    compares against what was verified. Only text never anchored before is anchored now.
    """
    old = collections.defaultdict(list)
    for k, rec in (existing.get("anchors") or {}).items():
        card, raw, ln = _split_key(k)
        old[(card, raw)].append((ln, rec))
    for v in old.values():
        v.sort(key=lambda t: t[0])
    rec, stats, last = {}, collections.Counter(), {}
    for c in sorted(cs, key=lambda c: (c.card, c.raw, c.line_no)):
        if c.status != "OK" or c.repo == "__kb__" or c.inherited:
            continue
        pool = old.get((c.card, c.raw))
        if pool is not None and (pool or last.get((c.card, c.raw))):
            # More occurrences of this text than old anchors - e.g. a dated note above the
            # original that cites the same line. Line order cannot say which is new, so
            # carry the old anchor to EVERY occurrence. The worst case is a spurious drift
            # report; the alternative was a silent re-verification at today's content.
            if pool:
                _, carried = pool.pop(0)
                last[(c.card, c.raw)] = carried
            else:
                carried = last[(c.card, c.raw)]
                stats["carried_duplicate"] += 1
            rec[c.key] = dict(carried, card=c.card)
            stats["carried"] += 1
        else:
            rec[c.key] = {"card": c.card, "repo": c.repo, "ref": c.ref,
                          "path": c.resolved, "line": c.start, "sha": c.anchor}
            stats["new"] += 1
    stats["dropped"] = sum(len(v) for v in old.values())
    return {"generated_by": "tools/citations.py bless --new-only", "anchors": rec}, stats


# --------------------------------------------------------------------------- outputs

E_CLASSES = ("E_NO_FILE", "E_LINE", "E_WRONG_PATH")


def _counts(cs):
    return collections.Counter(c.status for c in cs)


def report(cs: list[Cite]) -> int:
    qual = [c for c in cs if not c.inherited]
    inh = [c for c in cs if c.inherited]
    n = _counts(qual)
    ok = n["OK"]
    print(f"{len(qual)} qualified citations in {len({c.card for c in qual})} cards")
    print(f"  {ok} verified  ({ok * 100 // max(len(qual), 1)}%)")
    for k in sorted(n):
        if k == "OK":
            continue
        print(f"  {n[k]:5d}  {k}")
    if inh:
        ni = _counts(inh)
        print(f"\n{len(inh)} continuation references (`:1345` with no file) - scored "
              f"separately, never counted as verified:")
        for k in sorted(ni):
            print(f"  {ni[k]:5d}  {k}")
    worst = collections.Counter(c.card for c in cs if c.status in E_CLASSES)
    if worst:
        print("\ncards with unresolvable citations:")
        for card, k in worst.most_common(15):
            print(f"  {k:3d}  {card}")
    un = collections.Counter(c.card for c in cs if c.status == "W_UNATTRIBUTED")
    if un:
        print(f"\nunattributed, by card (top 10 of {len(un)}):")
        for card, k in un.most_common(10):
            print(f"  {k:3d}  {card}")
    return 0


def check(cs: list[Cite]) -> int:
    qual = [c for c in cs if not c.inherited]
    errs = [c for c in qual if c.status in E_CLASSES]
    warns = [c for c in qual if c.status.startswith("W_")]
    for c in errs:
        print(f"ERROR  {c.card}:{c.line_no}: {c.raw} - {c.detail}")
    for k, v in sorted(collections.Counter(c.status for c in warns).items()):
        print(f"warn   {v} {k}")
    ok = sum(1 for c in qual if c.status == "OK")
    print(f"{len(qual)} qualified citations - {ok} verified, {len(errs)} errors, "
          f"{len(warns)} unverified")
    inh = collections.Counter(c.status for c in cs if c.inherited)
    if inh:
        print(f"{sum(inh.values())} continuation references - "
              + ", ".join(f"{v} {k}" for k, v in sorted(inh.items()))
              + " (never counted as verified)")
    return 1 if errs else 0


def facts(cs: list[Cite]) -> list[dict]:
    out = []
    for c in cs:
        if c.status != "OK":
            continue
        out.append({"kind": "Citation", "card": c.card, "repo": c.repo,
                    "ref": c.ref, "path": c.resolved, "line": c.start,
                    "end_line": c.end, "anchor": c.anchor,
                    "provenance": "verified-citation", "confidence": "CODE"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["report", "check", "bless", "drift", "facts"])
    ap.add_argument("--new-only", action="store_true",
                    help="bless: anchor only never-anchored citations; carry the rest forward")
    a = ap.parse_args()

    if a.cmd == "drift":
        anchors = load_anchors()
        if not anchors.get("anchors"):
            print("no anchors recorded - run `citations.py bless` first")
            return 0
        moved = drift(anchors)
        if not moved:
            print(f"{len(anchors['anchors'])} anchors, none drifted "
                  f"since the registry last moved")
            return 0
        bycard = collections.defaultdict(list)
        for m in moved:
            bycard[m["card"]].append(m)
        print(f"{len(moved)} cited lines changed in {len(bycard)} cards - "
              f"re-verify these:\n")
        for card in sorted(bycard):
            print(f"  {card}")
            for m in sorted(bycard[card], key=lambda x: (x["path"], x["line"])):
                print(f"      {m['path']}:{m['line']}  {m['ref']} -> {m['now']}")
                print(f"        {m['why']}")
        return 0

    cs = scan()
    if a.cmd == "report":
        return report(cs)
    if a.cmd == "check":
        return check(cs)
    if a.cmd == "bless" and a.new_only:
        data, st = bless_new_only(cs, load_anchors())
        ANCHORS.parent.mkdir(parents=True, exist_ok=True)
        ANCHORS.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        print(f"{st['new']} new anchors, {st['carried']} carried forward unchanged, "
              f"{st['dropped']} dropped (citation no longer in its card) -> "
              f"{ANCHORS.relative_to(KB)}")
        return 0
    if a.cmd == "bless":
        data = bless(cs)
        ANCHORS.parent.mkdir(parents=True, exist_ok=True)
        ANCHORS.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        print(f"{len(data['anchors'])} anchors -> {ANCHORS.relative_to(KB)}")
        return 0
    fs = facts(cs)
    FACTS.parent.mkdir(parents=True, exist_ok=True)
    FACTS.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in fs))
    print(f"{len(fs)} verified citations -> {FACTS.relative_to(KB)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

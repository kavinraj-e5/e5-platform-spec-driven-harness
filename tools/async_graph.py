#!/usr/bin/env python3
"""The asynchronous communication graph: who publishes what, and who consumes it.

    python3 tools/async_graph.py extract    # -> graph/async-facts.jsonl
    python3 tools/async_graph.py report     # the chains, as text
    python3 tools/async_graph.py check      # exit 1 on a dangling topic

WHY THIS EXISTS
---------------
Every other extractor in this knowledge base reads a SYNCHRONOUS surface - a proto, a
GraphQL schema, an HTTP route - and asynchronous communication has none. The result was
measurable: 23 Component->Component `CALLS` edges and ZERO async edges, while the
platform's philosophy document says the subsystems talk over Kafka. 32 Kafka topics
existed as Contract nodes, so you could ask "who declares this topic" and never "who
sends to it and who reads it".

THE JOIN IS THE CONFIG KEY, NOT THE TOPIC NAME
-----------------------------------------------
Stated already in knowledge/_reference/joins/kafka-topics.yaml and confirmed here: topic
names are rarely literals. Both ends read a config key whose value is injected per
environment. So two things can join a producer to a consumer, and this tool uses both:

    the CONFIG KEY      `kafka.topics.audit-events`   - a contract two repos must agree on
    the DEFAULT LITERAL `e5.audit.events`             - from `@Value("${key:default}")`

A mismatch is silent and total: a producer on `topics.audit` and a consumer on
`topic.audit` never exchange a message and nothing fails to start. `check` is that test.

RESOLUTION IS LADDERED, AND FAILURE IS RECORDED
------------------------------------------------
A topic expression is resolved down five rungs, because that is how deep the platform
actually buries the value:

  1. a string literal in the code
  2. the default in `@Value("${key:default}")`
  3. a bound field, or a getter on a @ConfigurationProperties class
  4. the config key, looked up in the repo's own application.yml - which almost always
     yields NOT a topic but an ENV VAR: `task-manager: ${TASK_MANAGER_INBOUND_TOPIC}`
  5. that env var, looked up in the OBSERVED CLUSTER ENV of the component's deployment

Rung 5 is why this graph can be built at all. The producer and the consumer of one
channel use DIFFERENT config keys - `kafka.topic.producer.task-manager` versus
`kafka.topic.consumer.task-manager` - so joining on the key finds nothing. They meet only
at the resolved value, and the value exists nowhere in any repository: it is injected as
a literal env var at deploy time. That is the platform's config-injection design, and it
is the reason async wiring was invisible to a source-only index.

CAVEAT, carried on every edge resolved that way: the cluster snapshot is **QA**, read at
one moment. `resolved_from: cluster/qa` means "this is the wiring QA was running", not
"this is the wiring". A dev or prod difference is invisible here and would need their
kubeconfigs.

Anything that resolves off the bottom of the ladder is emitted as
`resolution: unresolved` WITH the expression text - never dropped and never guessed, the
same convention `VirtualQueuePoll` already uses. An extractor that silently drops what it
cannot parse is indistinguishable from a service that does not publish.

THREE FAMILIES OF EDGE, BECAUSE THE PLATFORM HAS THREE ASYNC MECHANISMS
------------------------------------------------------------------------
  Kafka topics     publish/subscribe between subsystems          -> PUBLISHES_TO / CONSUMES_FROM
  Virtual queues   the AKST task plane, polled not pushed        -> SERVES_QUEUE / POLLS_QUEUE
  Task names       interface -> the subsystem that handles it    -> PRODUCES_TASK / HANDLES_TASK

The third already existed as curated data in knowledge/_reference/joins/task-names.yaml
and was never projected as edges - graph_store even COUNTED them in its stats while
graph_model emitted none.

READ-ONLY, from bare mirrors, at the indexed branch. Never a working copy.
"""
from __future__ import annotations
import argparse, collections, json, os, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "release"))
import minyaml

KB = HERE.parent
OUT = KB / "graph" / "async-facts.jsonl"
JOINS = KB / "knowledge" / "flows"

# --------------------------------------------------------------------------- patterns
_PLACEHOLDER = re.compile(r'\$\{([A-Za-z0-9_.\-]+)(?::([^}"]*))?\}')
_VALUE_ANN   = re.compile(r'@Value\s*\(\s*(?:value\s*=\s*)?"([^"]*)"\s*\)')
_DECL_AFTER  = re.compile(r'\s*(?:private|public|protected)?\s*(?:final\s+)?String\s+(\w+)')
_FIELD_LIT   = re.compile(r'(?:private|public|protected)\s+(?:final\s+)?String\s+(\w+)\s*=\s*"([^"]+)"\s*;')
# The annotation may be written fully qualified. event-processor uses
# `@org.springframework.kafka.annotation.KafkaListener(` and matching only the
# short form lost the event pipeline's entire consumer side.
_LISTENER    = re.compile(r'@(?:[\w.]+\.)?KafkaListener\s*\(', re.S)
_TOPICS_ATTR = re.compile(r'topics\s*=\s*(\{[^}]*\}|"[^"]*"|\w+)')
# Kafka Streams. Anchored on a StreamsBuilder receiver on purpose: a bare `.stream(` is
# java.util.stream and matches hundreds of times per repo.
_KSTREAM_IN  = re.compile(r'\bstreamsBuilder\s*\.\s*stream\s*\(\s*([A-Za-z_][\w.]*(?:\(\))?)')
_KSTREAM_OUT = re.compile(r'\.\s*to\s*\(\s*([A-Za-z_][\w.]*(?:\(\))?)\s*[,)]')
_SEND        = re.compile(r'\b(\w*[Kk]afka\w*|\w*[Tt]emplate|\w*[Oo]perations)\s*\.\s*send\s*\(\s*([^,)\n]+)')
_PRODREC     = re.compile(r'new\s+ProducerRecord\s*<[^>]*>\s*\(\s*([^,)\n]+)', re.S)
_GETTER      = re.compile(r'^(\w+)\s*\.\s*get([A-Z]\w*)\s*\(\s*\)$')
# A producer usually assigns the topic to a local first:
#   String topic = topicsConfig.getEffectiveAuditEventsTopic();
#   ... new ProducerRecord<>(topic, ...)
# Without this the send site resolves to the bare name `topic` and reads as
# unresolved even when the right-hand side is perfectly resolvable.
_LOCAL_ASSIGN = re.compile(r'\bString\s+(\w+)\s*=\s*([^;\n]+);')
# Constructor field assignment. webhook-service's audit producer does
#   this.auditTopic = auditProperties.getTopic();
# and then uses `auditTopic` at two ProducerRecord sites. Without this the
# whole audit publication is invisible, which hid a real misconfiguration.
_FIELD_ASSIGN = re.compile(r'this\.(\w+)\s*=\s*([^;\n]+);')
# The value group must allow EMPTY: `topic:` with nothing after it is a PARENT,
# and requiring a value meant parents never pushed onto the stack, so
# `kafka.topic.producer.task-manager` flattened to nothing at all.
_YAML_KEY    = re.compile(r'^\s*([A-Za-z0-9_.\-]+)\s*:\s*(.*?)\s*$')

SKIP_DIRS = ("/test/", "/tests/", "src/test", "/it/")


def _git(gitdir: str, *a: str) -> str:
    p = subprocess.run(["git", "--git-dir", gitdir, *a],
                       capture_output=True, text=True, timeout=180)
    return p.stdout if p.returncode == 0 else ""


def _registry() -> list[dict]:
    return minyaml.load((KB / "registry.yaml").read_text())["repos"]


def _components_for(repo: str, cat) -> list[str]:
    return sorted(n for n, c in cat["Component"].items()
                  if (c.get("source") or {}).get("repo") == repo)


# --------------------------------------------------------------------------- resolution
def _flatten_yaml(text: str) -> dict:
    """Dotted-key view of a Spring application.yml.

    Deliberately indentation-only and tiny: these files are plain nested mappings of
    scalars, and pulling pyyaml into the extractor path would break the bare-CI-container
    rule the rest of the indexer is held to.
    """
    out, stack = {}, []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        m = _YAML_KEY.match(raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = [k for _, k in stack] + [key]
        if val:
            out[".".join(path)] = val.strip('"\'')
        else:
            stack.append((indent, key))
    return out


def _repo_config(files: dict[str, str]) -> dict:
    """Every config key this repo declares -> its value, which is usually `${ENV_VAR}`."""
    cfg = {}
    for path, body in files.items():
        base = os.path.basename(path)
        if not (base.startswith("application") and path.endswith((".yml", ".yaml"))):
            continue
        if any(sk in path for sk in SKIP_DIRS):
            continue
        # the main profile wins over -local / -test, which exist to be overridden
        primary = base in ("application.yml", "application.yaml")
        for k, v in _flatten_yaml(body).items():
            if primary or k not in cfg:
                cfg[k] = v
    return cfg


class Bindings:
    """field/property name -> (config_key, literal). The symbol table for one repo."""

    def __init__(self, config: dict | None = None, env: dict | None = None):
        self.by_field: dict[str, tuple] = {}
        self.by_prop: dict[str, tuple] = {}   # lowercased field name, for getFoo()
        self.config = config or {}            # rung 4: application.yml
        self.env = env or {}                  # rung 5: observed cluster env

    def add(self, name: str, key, lit):
        if name in self.by_field and self.by_field[name] != (None, None):
            return
        self.by_field[name] = (key, lit)
        self.by_prop[name.lower()] = (key, lit)

    def _deepen(self, key, lit, how) -> tuple:
        """Rungs 4 and 5: config key -> application.yml -> env var -> observed value.

        Only ever ADDS a literal; it never overrides one that the code already stated.
        A default in `@Value("${k:d}")` is what the service runs with when nothing is
        injected, but what it runs with IN QA is the env value - so when both exist the
        observed one wins and `how` records which."""
        if not key:
            return (key, lit, how)
        raw = self.config.get(key)
        if raw:
            m = _PLACEHOLDER.search(raw)
            if m:
                envvar, envdefault = m.group(1), m.group(2)
                observed = self.env.get(envvar)
                if observed:
                    return (key, observed, f"{how} -> yml -> env {envvar} -> cluster/qa")
                if envdefault:
                    return (key, lit or envdefault, f"{how} -> yml -> env default")
                return (key, lit, f"{how} -> yml -> env {envvar} (unset here)")
            return (key, lit or raw, f"{how} -> yml literal")
        return (key, lit, how)

    def resolve(self, expr: str) -> tuple:
        """-> (config_key, literal, how). `how` is never invented; 'unresolved' is a value."""
        e = expr.strip().rstrip(";").strip()
        if not e:
            return (None, None, "unresolved")
        if e.startswith('"') and e.endswith('"') and len(e) > 1:
            inner = e[1:-1]
            m = _PLACEHOLDER.search(inner)
            if m:
                return self._deepen(m.group(1), m.group(2) or None, "placeholder")
            return (None, inner, "literal")
        m = _PLACEHOLDER.search(e)
        if m:
            return self._deepen(m.group(1), m.group(2) or None, "placeholder")
        if e in self.by_field:
            k, l = self.by_field[e]
            return self._deepen(k, l, "field")
        g = _GETTER.match(e)
        if g:
            recv, prop = g.group(1), g.group(2)
            prop = prop[0].lower() + prop[1:]
            # RECEIVER FIRST. `auditProperties.getTopic()` -> the `audit.*` prefix in
            # application.yml. Matching the bare property name against a repo-global map
            # of field names resolved this to a field called `topic` in an UNRELATED
            # class and produced `e5.audit.events` - the code default - with total
            # confidence, hiding that QA injects `e5_dev_audit_events`. The receiver
            # names the class; it has to be the stronger signal.
            pre = re.sub(r'(Properties|Config|Configuration)$', '', recv)
            pre = re.sub(r'(?<!^)(?=[A-Z])', '-', pre).lower()
            kebab = re.sub(r'(?<!^)(?=[A-Z])', '-', prop).lower()
            for cand in (f"{pre}.{prop}", f"{pre}.{kebab}"):
                if cand in self.config:
                    return self._deepen(cand, None, "properties-prefix")
            if prop.lower() in self.by_prop:
                k, l = self.by_prop[prop.lower()]
                # weaker: a field of this name exists SOMEWHERE in the repo, not
                # necessarily in the class the receiver names.
                return self._deepen(k, l, "field-name match (unverified class)")
        return (None, None, "unresolved")


def _collect_bindings(files: dict[str, str], config=None, env=None) -> Bindings:
    b = Bindings(config, env)
    for path, body in files.items():
        if not path.endswith(".java"):
            continue
        for m in _VALUE_ANN.finditer(body):
            ph = _PLACEHOLDER.search(m.group(1))
            if not ph:
                continue
            tail = body[m.end():m.end() + 200]
            d = _DECL_AFTER.match(tail)
            if d:
                b.add(d.group(1), ph.group(1), ph.group(2) or None)
            else:
                # constructor parameter: @Value("${k:d}") String auditEventsTopic
                d2 = re.match(r'\s*String\s+(\w+)', tail)
                if d2:
                    b.add(d2.group(1), ph.group(1), ph.group(2) or None)
        for m in _FIELD_LIT.finditer(body):
            b.add(m.group(1), None, m.group(2))
    # Second pass for locals: the right-hand side must itself be resolvable, which it
    # only is once every field binding above is known.
    for path, body in files.items():
        if not path.endswith(".java"):
            continue
        for m in list(_LOCAL_ASSIGN.finditer(body)) + list(_FIELD_ASSIGN.finditer(body)):
            name, rhs = m.group(1), m.group(2).strip()
            if name in b.by_field:
                continue
            k, l, how = b.resolve(rhs)
            if how != "unresolved":
                b.add(name, k, l)
    return b


def _line_of(body: str, pos: int) -> int:
    return body[:pos].count("\n") + 1


# --------------------------------------------------------------------------- extraction
def _kafka_sites(*_a, **_k):
    """Removed. It emitted `TopicProduce` / `TopicConsume` keyed on a topic NAME.

    Measured on the output it actually produced: 25 TopicProduce and 19 TopicConsume
    facts, of which the resolved ones carried a qa-cluster value
    (`resolution: "authored-join -> cluster/qa"`) and the unresolved ones carried
    `topic: null` with a bare Java expression such as `dlqTopic`. Neither is a fact
    about code - the first is one environment's config and the second is nothing.

    The definite part of the same information survives as `TopicConfigKey`: which
    component reads or writes which config KEY, in a producer or a consumer class, cited
    per row in `_reference/joins/kafka-topics.yaml`. Who is on the other end of a topic
    is written down by a human in
    `knowledge/flows/task-and-event-flows.md`.
    """
    return []

def _authored_topic_facts(cat, envs: dict, repo_cfgs: dict) -> list[dict]:
    """knowledge/_reference/joins/kafka-topics.yaml, resolved against the cluster.

    This file is hand-curated and carries the one thing the code does not state plainly:
    the DIRECTION of each config key. It was written precisely because topic names are
    never literals, and it has sat unused by any projection since it was authored.

    Resolving it here is what connects the task plane: `interface` producing to
    `kafka.topic.producer.task-manager` and `workflow-manager` consuming
    `workflowManagerTopics.taskManagerOutboundEndPoint` are the same channel, and nothing
    in either repo says so - they meet only at the value injected into both pods.
    """
    f = JOINS / "kafka-topics.yaml"
    if not f.is_file():
        return []
    out = []
    for r in (minyaml.load(f.read_text()).get("rows") or []):
        comp, key, role = r.get("component"), r.get("config_key"), r.get("role")
        if not (comp and key and role):
            continue
        # The resolved `topic` value used to be computed here, by walking a ladder from
        # the config key to application.yml to an env var to the qa cluster. It is gone:
        # the topic NAME is injected at deploy time, so no walk can make it a fact about
        # code, and the one it produced was environment-scoped and dated the moment the
        # next deploy ran.
        #
        # What is kept is exactly what `kafka-topics.yaml` itself says is the contract:
        # "the value is config, the KEY is a contract two repos must agree on". The role
        # is definite too - it comes from which class holds the site, a producer or a
        # consumer, cited per row.
        out.append({
            "kind": "TopicConfigKey",
            "repo": (cat["Component"].get(comp, {}).get("source") or {}).get("repo"),
            "component": comp, "path": r.get("site"), "line": r.get("line"),
            "config_key": key, "role": role,
            "mechanism": "kafka-topics.yaml", "provenance": "authored-join",
            "confidence": "CURATED",
        })
    return out


def _task_routing_facts(binds_by_repo: dict) -> list[dict]:
    """The subsystem routing chain, as the code actually implements it.

        taskName --(switch #1)--> taskType + fbName
        taskName --(switch #2)--> virtual queue      <-- THIS picks the subsystem
        subsystem polls its OWN queue by name over gRPC
        subsystem dispatches internally on fbName

    Modelled because the first version of this graph got it wrong: it emitted a node
    labelled `TaskType` and keyed it on the task NAME, producing 15 "task types" where
    the platform has five. That mislabel made the obvious question - "which subsystem
    handles E5_WORKFLOW_OPS" - unanswerable while looking answered.

    The routing key is the VIRTUAL QUEUE, and `interface` selects it from the taskName in
    `TaskManagerProtocolBuilder`, NOT from the taskType, which a separate switch in
    `TaskManagerHelperImpl` sets alongside. Both are recorded; only one routes.
    """
    out = []
    facts_f = KB / "graph" / "facts.jsonl"
    if not facts_f.is_file():
        return out
    by_name: dict = {}
    for line in facts_f.open():
        f = json.loads(line)
        p_ = f.get("props") or {}
        if f["kind"] != "RoutingRule":
            continue
        if not any(k in p_ for k in ("sets_task_type", "sets_fb_name", "sets_vq")):
            continue
        for nm in (p_.get("task_names") or []):
            r = by_name.setdefault(nm, {"task_name": nm, "component": f["component"]})
            for src, dst in (("sets_task_type", "task_type"),
                             ("sets_fb_name", "fb_name"), ("sets_vq", "vq_field")):
                if p_.get(src):
                    r[dst] = p_[src]
                    r[f"{dst}_at"] = f"{f.get('path')}:{f.get('line')}"
    for nm, r in sorted(by_name.items()):
        # `virtual_queue` and `vq_resolution` used to be set here by chasing the switch's
        # VQ field through yml to an env var to the qa cluster, and the resolution string
        # said so out loud: "field -> yml -> env INTERFACE_TASK_WORKFLOWMANAGER_OUTBOUND
        # _QUEUE -> cluster/qa". A four-hop guess stored beside two certainties.
        #
        # `vq_field` is kept because the FIELD NAME is in the switch and is definite -
        # `workflowManagerVQ` is what the developer wrote. Its VALUE is not ours to know.
        # The destination is established instead by the task name appearing on both
        # sides: interface routes PAUSE_OR_RESUME_APP at TaskManagerProtocolBuilder:84
        # and workflow-manager dispatches PAUSE_RESUME_APP at
        # WorkflowManagerTaskHandler:90. Two code sites, no queue name.
        out.append({
            "kind": "TaskRouting", "component": r.get("component"),
            "task_name": nm, "task_type": r.get("task_type"),
            "fb_name": r.get("fb_name"), "vq_field": r.get("vq_field"),
            "path": r.get("vq_field_at") or r.get("task_type_at"),
            "provenance": "code", "confidence": "EXTRACTED",
        })
    return out


def _queue_and_task_facts() -> list[dict]:
    """The task plane, by TASK NAME. No queue names: they are deploy-time values.

    This used to project `QueuePoll` / `QueueRef` facts keyed on a queue name read out
    of source. Every one of those keys was a value the devops pipeline overrides, and 7
    of the 10 named a queue the platform does not run. The extractor that produced them
    (`extractors.extract_virtual_queues`) is deleted, so there is nothing left to read.

    What remains is the half that is actually provable: the authored task-name join,
    where `produced_by` and `handled_by` are each backed by a switch in a different
    repository that names the same task.
    """
    out = []
    tn = JOINS / "task-names.yaml"
    if tn.is_file():
        for r in (minyaml.load(tn.read_text()).get("rows") or []):
            out.append({
                "kind": "TaskRoute", "task_name": r.get("task_name"),
                "task_type": r.get("task_type"), "fb_name": r.get("fb_name"),
                "component": r.get("produced_by"), "handled_by": r.get("handled_by"),
                "path": r.get("site"), "line": r.get("line"),
                "provenance": "authored-join", "confidence": "CURATED",
            })
    return out


def extract() -> list[dict]:
    import catalog as C
    cat = C.load()
    # No cluster environment: deploy-time values are never knowledge (removed 2026-11-14).
    envs: dict = {}
    repo_cfgs: dict = {}
    binds_by_repo: dict = {}
    facts: list[dict] = []
    for r in _registry():
        gitdir = os.path.expanduser(r["mirror"])
        if not os.path.isdir(gitdir):
            continue
        branch, repo = r["branch"], r["id"]
        listing = _git(gitdir, "ls-tree", "-r", "--name-only", branch).splitlines()
        java = [f for f in listing if f.endswith(".java")]
        if not java:
            continue
        files, cfgfiles = {}, {}
        for f in java:
            body = _git(gitdir, "show", f"{branch}:{f}")
            if body and ("afka" in body or "Topic" in body):
                files[f] = body
        for f in listing:
            if os.path.basename(f).startswith("application") and f.endswith((".yml", ".yaml")):
                cfgfiles[f] = _git(gitdir, "show", f"{branch}:{f}")
        repo_cfgs[repo] = _repo_config(cfgfiles)
        if not files:
            continue
        comps = _components_for(repo, cat)
        merged = {}
        for c in comps:
            merged.update(envs.get(c) or {})
        binds_by_repo[repo] = _collect_bindings(files, repo_cfgs[repo], merged)
        facts += _kafka_sites(repo, cat, files, repo_cfgs[repo], envs,
                              branch, r.get("commit"))
    # Kafka topic config keys are not extracted any more (owner, 2026-11-14): topic wiring
    # is deploy-time configuration. Only the task plane, joined on the task name, remains.
    facts += _task_routing_facts(binds_by_repo)
    facts += _queue_and_task_facts()
    facts.sort(key=lambda f: (f["kind"], f.get("component") or "",
                              str(f.get("path")), f.get("line") or 0))
    return facts


# --------------------------------------------------------------------------- views
def _chains(facts: list[dict]):
    """Producer and consumer of one CONFIG KEY. The key is the join; there is no value.

    Everything that used to live between here and `main` - `_topic_key`, `_topic_names`,
    `_norm`, the five-rung resolution ladder, the env-stamp table - resolved a topic
    EXPRESSION down to a name, with rung 5 reading the observed qa cluster. That was the
    only way to pair a producer with a consumer when the pairing was done on values, and
    pairing on values is the thing this estate cannot support: the devops pipeline
    injects the name at deploy time and every repo runs with whatever it is handed.

    The key join needs none of it. `kafka-topics.yaml` records, per cited code site,
    which component reads or writes which config key as a producer or a consumer. If a
    producer writes `topics.audit` and a consumer reads `topic.audit`, no message ever
    arrives and nothing fails to start - and comparing the two KEYS catches it, which is
    what `check` now does.
    """
    by_key: dict = {}
    for f in facts:
        if f.get("kind") != "TopicConfigKey":
            continue
        d = by_key.setdefault(f["config_key"], {"producers": set(), "consumers": set(),
                                                "sites": []})
        (d["producers"] if f.get("role") == "producer" else d["consumers"]).add(
            f.get("component"))
        d["sites"].append(f"{f.get('component')} {f.get('path')}:{f.get('line')}")
    return by_key


def report(facts: list[dict]) -> None:
    by_key = _chains(facts)
    routes = [f for f in facts if f.get("kind") == "TaskRoute"]
    routing = [f for f in facts if f.get("kind") == "TaskRouting"]
    print(f"{len(routes)} task routes, {len(routing)} routing-switch rows\n")
    print("\nTASK PLANE - joined on the TASK NAME, which two repos compare in code\n")
    for f in sorted(routes, key=lambda x: x.get("task_name") or ""):
        print(f"  {f.get('task_name'):34} {f.get('component')} -> {f.get('handled_by')}"
              f"   ({f.get('task_type')}/{f.get('fb_name')})")


def check(facts: list[dict]) -> int:
    """A config key declared by only one side. Silent and total when it happens."""
    by_key = _chains(facts)
    lonely = {k: d for k, d in by_key.items()
              if not (any(d["producers"]) and any(d["consumers"]))}
    print(f"{len(by_key)} kafka config keys; {len(lonely)} declared by only one side")
    for k, d in sorted(lonely.items()):
        side = "producer" if any(d["producers"]) else "consumer"
        print(f"  WARNING {k}: only a {side} declares this key")
        for st in d["sites"][:3]:
            print(f"            {st}")
    # Advisory, not blocking: a key with one side may simply mean the other end is a
    # component this knowledge base does not index (webhook-dispatcher is an AWS Lambda),
    # and failing a build for that is how a gate gets switched off.
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["extract", "report", "check"])
    a = ap.parse_args()
    facts = extract()
    if a.cmd == "extract":
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("".join(json.dumps(f, sort_keys=True) + "\n" for f in facts))
        print(f"{len(facts)} facts -> {OUT.relative_to(KB)}")
        return 0
    if a.cmd == "report":
        report(facts)
        return 0
    return check(facts)


if __name__ == "__main__":
    sys.exit(main())

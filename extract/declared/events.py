"""Event types: the CloudEvents `type` an envelope carries on the wire.

WHAT THIS DELIBERATELY NO LONGER EXTRACTS
-----------------------------------------
The regex version matched two families and treated them as one kind:

    e5.platform.event.*          a CloudEvents TYPE, written into the envelope
    e5.audit.* / e5.webhook.*    a KAFKA TOPIC NAME

Only the first is a fact about the code. Every single occurrence of the second — all 50
of them across four repos — is one of: a `${ENV_VAR:default}` Spring placeholder
default, a Java field the Spring binder overwrites from that same key, a Javadoc
comment, or a test assertion. The qa cluster runs `e5_qa_audit_events`,
`e5_qa_audit_events_dlq`, `e5_qa_webhook_requests` and `e5_qa_webhook_requests_dlq`
(observed 2026-10-06), so **not one of the source literals names a topic the platform
runs**. That is the same defect `extract_kafka` and `extract_virtual_queues` were
deleted for, surviving under a different `kind`. See
`docs/2026-10-13-extraction-redesign.md`, and
`knowledge/flows/task-and-event-flows.md` §2, which already records those
flows by payload contract and already says the defaults are stale.

`e5.platform.event.*` survives the same test and passes it: it is written in code and
read from no config key anywhere in the estate, and `eventstore-client-sdk` pins
`e5.platform.event.customevent` in a JSON Schema `enum`. It is a payload contract.
"""
from __future__ import annotations

import re

from extract.core import Repo, fact, is_test
from extract.parse.java import JavaFile
from extract.parse.pyfile import PyFile
from extract.parse.yamlfile import YamlFile

# A CloudEvents type, not a topic. The trailing `.` is kept off the name: one producer
# builds the type by concatenation and the literal it holds is a PREFIX, which is a
# different thing from a complete type and is reported as one.
EVENT_TYPE = re.compile(r'^e5\.platform\.event\.[A-Za-z0-9_.]*$')

_JAVA = (".java",)
_DATA = (".yaml", ".yml", ".json")


def _literals(repo: Repo, path: str):
    """(value, line, role, context) for every string CONSTANT in a file.

    Three front ends, one shape. A Javadoc comment is not a string constant in any of
    them, which is what drops `* - e5.webhook.requests - Webhook delivery requests` and
    the eleven commented-out `TopicPartition("e5.webhook.requests", 0)` lines in
    `WebhookRequestProducerTest.java` that the regex read as code.
    """
    src = repo.read(path)
    if "e5.platform.event" not in src:
        return
    if path.endswith(_JAVA):
        for s in JavaFile(src).strings():
            yield s.value, s.line, s.role, s.context
    elif path.endswith(".py"):
        f = PyFile(src)
        if not f.ok:
            return
        for s in f.strings():
            if s.role != "DOCSTRING":
                yield s.value, s.line, s.role, s.context
    else:
        f = YamlFile(src)
        if not f.ok:
            return
        for n in f.scalars():
            yield n.value, n.line, "DATA", n.path


def events(repo: Repo) -> list[dict]:
    sites: dict = {}
    for path in sorted(repo.files(*_JAVA, ".py", *_DATA, include_tests=True)):
        in_test = is_test(path)
        for value, line, role, context in _literals(repo, path):
            if not EVENT_TYPE.match(value):
                continue
            name = value.rstrip(".")
            sites.setdefault(name, []).append(
                (in_test, value.endswith("."), path, line, role, context))
    out = []
    for name, found in sorted(sites.items()):
        # One fact per type per repo, cited at the BEST site: production beats a
        # fixture, a complete type beats a prefix, then path order so the choice is
        # deterministic. The old version took whichever file `os.walk` reached first,
        # which let a JUnit fixture claim the name and silently suppress the real site.
        found.sort(key=lambda c: c[:4])
        in_test, is_prefix, path, line, role, context = found[0]
        props = {"role": role}
        if context:
            props["context"] = context
        if is_prefix:
            # `setEventtype("e5.platform.event.workflow." + status)` holds a PREFIX, not
            # a type. Saying so is the difference between a contract and a fragment.
            props["is_prefix"] = True
        if in_test:
            props["test_only"] = True
        # Losing the second production site would lose the fact that two places emit
        # this type - which is exactly the kind of thing a reader is looking for.
        others = [f"{p}:{ln}" for t, _, p, ln, _, _ in found[1:] if not t]
        if others:
            props["also_at"] = others
        out.append(fact("EventType", name, repo.id, path, line, "code", **props))
    return out

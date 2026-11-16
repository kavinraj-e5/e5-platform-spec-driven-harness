#!/usr/bin/env python3
"""BLOCKING gate: no fact may be identified by a value the deployment injects.

    python3 tools/check_no_injected_values.py

WHY THIS GATE EXISTS
--------------------
Every virtual-queue and Kafka-topic binding in this knowledge base was once extracted
from repository values, and all of it was wrong in the same way: the devops pipeline
injects the name at deploy time, so each service is written to run with whatever value
it is handed and the value in the repo is whatever someone last typed. Measured before
the extractors were deleted:

  * `workflow-manager/deployment-configs/qa-config.yaml` ships
    `"taskVirtualQueueName": "value"` - the literal word `value`. Its `.env` says `wmq`.
    `dynamic-scaling`'s Spring default for the very env var the cluster sets says
    `workflow_manager_vq`. The cluster says `wfmanager_service_vq`. One queue, four
    names, three fictional.
  * 7 of 10 queue literals in source named a queue the platform does not run, and 31%
    of the facts came from JUnit fixtures: `any_vq`, `other_VQ`, `tenant_app_VQ`.

Deleting the extractors fixed the facts. This gate is what stops them coming back - by
an extractor someone adds in good faith, by a fixture, or by a well-meant "resolution
ladder" that ends at an observed cluster.

WHAT IS AND IS NOT FLAGGED
--------------------------
Flagged: underscore-delimited channel names, which is how this platform's devops
pipeline spells them - `*_vq`, `*_VQ`, `e5_qa_task_manager_cmdq`,
`Ftps-Robotframework_VQ`.

NOT flagged, on purpose:
  * dot-delimited CloudEvents types - `e5.platform.event.workflow.started` is written
    in source, read from no config key anywhere in the estate, and pinned in
    `eventstore-client-sdk`'s JSON Schema. It is a payload contract, not a channel.
    NB this exception used to be "dot-delimited code literals", which was too wide:
    `e5.audit.events` and `e5.webhook.requests` are dot-delimited and are KAFKA TOPIC
    NAMES. Every occurrence of them in source is a `${ENV_VAR:default}` default, a
    field the Spring binder overwrites, a Javadoc comment or a test, and qa runs
    `e5_qa_audit_events` / `e5_qa_webhook_requests` instead. They are listed as
    fiction below, and `extract/declared/events.py` no longer produces them.
  * type and class names - credential-monitor really does declare a protobuf message
    called `VirtualQueue` in task.proto:10. The word is not the problem; a deployed
    channel NAME is.
  * config KEYS - `task.virtual.queue.name`, `TASK_VIRTUAL_QUEUE`. A key lives in the
    source and stays put. Keys are what the authored joins use.

The flows that used to be derived from these values are written down instead, with
two-sided code evidence, in `knowledge/flows/task-and-event-flows.md`.
"""
import json
import re
import sys
from pathlib import Path

KB = Path(__file__).resolve().parent.parent
GRAPH = KB / "graph"

# TWO CHECKS, and the first matters more.
#
# 1. FORBIDDEN FIELDS. A fact must not have a field whose job is to hold a channel
#    value, whatever the value happens to be. This is the precise rule: the defect was
#    never a particular string, it was storing "which topic/queue" as data.
#    `literal` is NOT in this set. It is too generic a field name: `CallsComponent.literal`
#    holds a gRPC service enum or stub class (`DEPLOYMENT_MANAGEMENT`,
#    `DeploymentManagementServiceBlockingStub`), which is a code literal and definite.
#    Forbidding it flagged 23 correct facts. The value-shape backstop covers the case
#    where a channel name lands in a generically named field.
_FORBIDDEN_FIELDS = {"topic", "queue", "virtual_queue", "vq_literal", "vq_name",
                     "queue_name", "topic_name", "observed_value", "literal_in_source"}

# 2. VALUE SHAPE, as a backstop for a field named something new. Only the two signals
#    that are unambiguous in this estate. An earlier version also flagged
#    `^e5_<x>_<y>$`, which caught the PyPI package `e5_task_sdk_python` - a published
#    artifact coordinate, and definite. Being clever about value shapes is how a gate
#    starts making false accusations.
_CHANNEL = re.compile(r'''
      ^[\w-]*_(?:vq|VQ)$                   # anything_vq / Ftps-Robotframework_VQ
    | _(?:qa|dev|prod|stage)_             # an environment segment: e5_qa_task_manager_cmdq
''', re.X)

# Coordinates and paths legitimately contain underscores and environment-ish words.
# `name` is deliberately ABSENT. The gate's rule is that no fact may be IDENTIFIED by
# an injected value, and `name` is the identity - exempting it exempted the only field
# the rule is really about. Measured when it was removed: 0 of 3,858 facts trip, so it
# costs nothing. `id` and `contract_key` stay exempt because they EMBED the name and
# would turn one finding into three.
_NOT_A_CHANNEL_FIELD = {"repo", "artifact", "group", "path", "id", "contract_key",
                        "component", "branch", "commit", "system", "site"}

# Kinds whose `name` / `owner` are a function and its class or module, parsed from source
# by a grammar (tools/call_graph.py). A function called `delete_tasks_from_vq` or
# `resolve_stage_error_definition` is code, not a deployed channel; the value-shape backstop
# flagged exactly those two on the first call-graph run (2026-11-15). Only these two fields,
# only for these kinds: a channel VALUE in any other field of such a fact still fires.
_CODE_IDENTITY = {"Method": {"name", "owner"}}

# Names that were measured to be fictional: JUnit fixtures and stale Spring defaults.
_KNOWN_FICTION = {"any_vq", "other_vq", "tenant_app_vq", "sample-robotframework_vq",
                  "vtapp-robotframework_vq", "workflow_manager_vq", "wmq",
                  # Kafka topic defaults. Measured 2026-10-17: every occurrence in
                  # source is a Spring placeholder default or a test, and qa runs
                  # e5_qa_audit_events, e5_qa_audit_events_dlq, e5_qa_webhook_requests
                  # and e5_qa_webhook_requests_dlq (observed 2026-10-06).
                  "e5.audit.events", "e5.audit.events.dlq",
                  "e5.webhook.requests", "e5.webhook.requests.dlq",
                  "e5.workflow.updates"}

# Fields that name a CONFIG KEY rather than a value. A key is allowed to contain the
# word queue; that is the whole point of joining on keys.
_KEY_FIELDS = {"config_key", "env_var", "declares_config_key", "vq_field",
               "keys_that_must_agree", "a_key", "b_key", "resolved_config_key"}


def scan():
    bad = []
    for f in sorted(GRAPH.glob("*.jsonl")):
        if f.name == "cost.jsonl":          # token telemetry, not facts
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if not line.strip():
                continue
            fact = json.loads(line)
            fields = dict(fact)
            fields.pop("props", None)
            fields.update(fact.get("props") or {})
            for k, v in fields.items():
                if k in _KEY_FIELDS:
                    continue
                if k in _FORBIDDEN_FIELDS and v not in (None, ""):
                    bad.append((f.name, n, fact.get("kind"), k, v,
                                "field exists to hold a channel value"))
                    continue
                if not isinstance(v, str) or k in _NOT_A_CHANNEL_FIELD \
                        or k in _CODE_IDENTITY.get(fact.get("kind"), ()):
                    continue
                if v.lower() in _KNOWN_FICTION:
                    bad.append((f.name, n, fact.get("kind"), k, v,
                                "measured to be fictional (JUnit fixture or stale default)"))
                elif _CHANNEL.search(v):
                    bad.append((f.name, n, fact.get("kind"), k, v,
                                "looks like a deployed channel name"))
    return bad


def main() -> int:
    bad = scan()
    files = len([p for p in GRAPH.glob("*.jsonl") if p.name != "cost.jsonl"])
    if not bad:
        print(f"{files} fact layer(s) scanned - no fact is identified by an injected "
              f"value")
        return 0
    print(f"ERROR: {len(bad)} fact(s) carry a deploy-time channel name as data.\n")
    print("A topic or queue NAME is injected by the devops pipeline, so it cannot be a")
    print("fact about code. Record the config KEY instead, or write the flow down in")
    print("knowledge/flows/task-and-event-flows.md.\n")
    for layer, n, kind, field, val, why in bad[:40]:
        print(f"  {layer}:{n}  {kind}.{field} = {val!r}\n      {why}")
    if len(bad) > 40:
        print(f"  ... and {len(bad) - 40} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""DECLARED facts: what the code says, independent of any environment.

A declared fact's identity is a code symbol - a class, a method, an rpc, a table, a
config KEY, a task name compared in a switch. It is the same on every deploy.

Nothing in this package may key a fact on a value the deployment injects. That is not a
style rule; `tools/check_no_injected_values.py` fails the build on it. See
`docs/2026-10-13-extraction-redesign.md` for the 140 facts that rule deleted.
"""
from extract.declared.runtime import handlers, worker_roles, db_bindings   # noqa: F401
from extract.declared.deps import sdk_imports                             # noqa: F401
from extract.declared.rf import rf_keywords                                # noqa: F401
from extract.declared.routing import routing                              # noqa: F401
from extract.declared.events import events                                # noqa: F401
from extract.declared.config import config                                # noqa: F401
from extract.declared.proto import proto                                  # noqa: F401
from extract.declared.graphql import graphql                              # noqa: F401
from extract.declared.migrations import migrations                        # noqa: F401

# Ported from tools/extractors.py and verified against it by `extract.cli diff`.
# Each entry is (name, fn) where fn takes a core.Repo and returns facts.
PORTED = {
    "handlers": handlers,
    "worker_roles": worker_roles,
    "db_bindings": db_bindings,
    "sdk_imports": sdk_imports,
    "rf_keywords": rf_keywords,
    "routing": routing,
    "events": events,
    "config": config,
    "proto": proto,
    "graphql": graphql,
    "migrations": migrations,
}

"""Configuration KEYS. Never values — a pod spec carries live DB credentials.

WHAT CHANGED, AND WHY IT IS NOT JUST A PARSER SWAP
--------------------------------------------------
The regex version asked whether the NEXT LINE matched `value:` to decide whether a
`- name:` entry was an environment variable, because in these files the key `name` also
names the service, the image and the container. That is the structure, guessed. The real
question is whether the key sits at `...env[i].name`, which the composer answers
outright. It also carried a second guess — `[A-Z][A-Z0-9_]{2,}` — which quietly made
the NAMING CONVENTION part of the definition of an environment variable.

It also emitted **one fact per key per environment file**, so `audit-service` declared
the same 232 keys five times over and the knowledge base carried 1,836 occurrences of
397 keys. That was not harmless: the v3 plan's own worked example found `platform_impact`
citing `ConfigKey credential-monitor:taskManager @ dev-config.yaml:15` **four times** as
the evidence for a Component — noise presented as proof. Measured before collapsing:
every one of the 397 keys appears in every environment file of its repo, and every one
of the 1,836 occurrences carried `has_value_binding: True`. The duplication said nothing.

So one fact per key, with `declared_in` listing the environment files that declare it —
and a `ConfigKeyGap` finding when that list is short. The gap count is 0 today. The
check is still the point: a key added to dev and forgotten in prod is a real outage, and
nothing here could see it before.
"""
from __future__ import annotations

import re

from extract.core import Repo, fact
from extract.parse.yamlfile import YamlFile

# `...env[7]` - the parent of an environment variable's `name`. Anchored on the
# structure, so it holds for both the flat `env:` list these repos use and a
# Kubernetes `spec.template.spec.containers[0].env[0]`.
_ENV_PARENT = re.compile(r'(^|\.)env\[\d+\]$')

_DEPLOY = "deployment-config"
_BUILD = "build-config.yaml"


def _config_files(repo: Repo) -> list[str]:
    return sorted(p for p in repo.files(".yaml", ".yml", include_tests=True)
                  if _DEPLOY in p or p.endswith(_BUILD))


def config(repo: Repo) -> list[dict]:
    found: dict = {}
    unparseable, env_files = [], set()
    for path in _config_files(repo):
        f = YamlFile(repo.read(path))
        if not f.ok:
            # A config file that does not parse is a finding. The line-regex version
            # read it anyway, one line at a time, and reported whatever it recognised -
            # so a broken file and a healthy one produced the same kind of answer.
            unparseable.append((path, f.error))
            continue
        base = path.rsplit("/", 1)[-1]
        if _DEPLOY in path:
            env_files.add(base)
        for n in f.scalars():
            if n.key != "name" or not _ENV_PARENT.search(n.path.rsplit(".", 1)[0]):
                continue
            parent = n.path.rsplit(".", 1)[0]
            binding = ("value" if f.has(f"{parent}.value")
                       else "valueFrom" if f.has(f"{parent}.valueFrom") else "none")
            rec = found.setdefault(n.value, {"sites": [], "bindings": set(),
                                             "files": set()})
            rec["sites"].append((path, n.key_line))
            rec["bindings"].add(binding)
            rec["files"].add(base)

    out = []
    for key, rec in sorted(found.items()):
        path, line = sorted(rec["sites"])[0]
        bindings = sorted(rec["bindings"])
        props = {"key": key, "declared_in": sorted(rec["files"]),
                 # `valueFrom` means the value is a secret reference rather than a
                 # literal - the one thing about a config key worth knowing that is not
                 # the key itself. The old `has_value_binding` was True on all 1,836.
                 "binding": bindings[0] if len(bindings) == 1 else "mixed:" + ",".join(bindings)}
        out.append(fact("ConfigKey", f"{repo.id}:{key}", repo.id, path, line, "config",
                        **props))
        missing = sorted(env_files - rec["files"])
        if missing and env_files:
            out.append(fact("ConfigKeyGap", f"{repo.id}:{key}", repo.id, path, line,
                            "config", key=key, missing_from=missing,
                            issue=f"{key} is declared in {len(rec['files'])} of "
                                  f"{len(env_files)} environment configs",
                            severity="medium"))
    for path, err in unparseable:
        out.append(fact("ConfigParseError", path, repo.id, path, 1, "config",
                        issue="config file does not parse as YAML", detail=err,
                        severity="high"))
    return out

#!/usr/bin/env python3
"""Where the repository mirrors and checkouts live.

WHY THIS IS NOT JUST A LINE IN registry.yaml
--------------------------------------------
`workspace_root:` is an ABSOLUTE PATH committed to the repository. It names one
developer's home directory, which is fine on that laptop and wrong everywhere else - in
CI, in a container, on a second machine.

CI already worked around it by rewriting the file in place:

    sed -i 's|^workspace_root:.*|workspace_root: ${env.WORKSPACE}|' registry.yaml

That is in ci/jenkins-kb-index.groovy with a TODO admitting it should not be necessary.
A build that edits its own configuration to run is a build that cannot be trusted to
have run against what the configuration says.

PRECEDENCE: explicit flag > environment > registry file > ~/.e5/workspace.
The flag is for a human at a terminal, the environment is for CI and containers, and the
file is the default for the machine the KB was first set up on.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ENV_VAR = "E5_KB_WORKSPACE_ROOT"


def workspace_root(registry_text: str, override: str | None = None) -> str:
    """Resolve the workspace root. Raises if no source supplies one."""
    if override:
        return override.rstrip("/")
    env = os.environ.get(ENV_VAR)
    if env:
        return env.rstrip("/")
    m = re.search(r"^workspace_root:\s*(\S+)", registry_text, re.M)
    if not m:
        # Portable default (2026-11-15): the harness is shared, so the registry no longer
        # names one person's home directory. Worktrees for change sets go here.
        return str(Path.home() / ".e5" / "workspace")
    return m.group(1).rstrip("/")

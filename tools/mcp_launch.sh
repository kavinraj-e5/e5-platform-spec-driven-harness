#!/usr/bin/env bash
# Launcher for the MCP server, used by the Claude Code plugin.
#
# WHY A WRAPPER AND NOT `python3` DIRECTLY
# ----------------------------------------
# plugin.json declared `python3`, and the system python has no `mcp` package - only the
# project venv does. The server then printed a helpful message and exited 0, so Claude Code
# saw the process end cleanly and simply showed no tools. A missing transport that looks
# like success is the worst of the three outcomes; this script picks an interpreter that
# can actually serve, and fails loudly if none can.
set -euo pipefail
KB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for PY in "$KB/.venv-mcp/bin/python" "$KB/.venv/bin/python" "$(command -v python3 || true)"; do
  [ -x "$PY" ] || continue
  if "$PY" -c 'import mcp' >/dev/null 2>&1; then
    exec "$PY" "$KB/tools/mcp_server.py" "$@"
  fi
done

echo "e5-platform-kb: no interpreter with the \`mcp\` package." >&2
echo "  python3 -m venv $KB/.venv-mcp" >&2
echo "  $KB/.venv-mcp/bin/pip install -r $KB/tools/requirements-mcp.txt" >&2
exit 1

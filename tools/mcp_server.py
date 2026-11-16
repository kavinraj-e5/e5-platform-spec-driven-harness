#!/usr/bin/env python3
"""MCP server exposing the E5 platform knowledge base.

All logic lives in kb_nav / kb_core (zero dependencies). This file is only the transport
wrapper, so the knowledge base stays fully usable when `mcp` is not installed - the
previous prototype buried its logic in the server and shipped it unreachable.

Transports:
  stdio            - local, spawned by the agent harness. No port, no collisions.
  streamable-http  - the hosted team service. (SSE is superseded; do not add it.)

  python3 tools/mcp_server.py                 # stdio
  python3 tools/mcp_server.py --http --port 7100
"""
from __future__ import annotations
import argparse, functools, hmac, os, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_core as core

DESCRIPTIONS = {
 "kb_overview":
   "The L0 overview of the E5 platform: philosophy, the map of areas (workflow, workflow "
   "library, app, app library, ui-synthesizer, subsystems, communication, reporting, "
   "delivery), dependencies, the subsystem picture, which areas a kind of change touches, "
   "and how to traverse. Already injected at session start; call it again after a long "
   "conversation. Start every task here.",
 "kb_read":
   "Read one document under knowledge/ by PATH, e.g. 'subsystems/README.md', "
   "'subsystems/workflow-ops.md', 'flows/task-and-event-flows.md'. A folder returns its "
   "README and its contents. Not found returns what the folder does contain. No search: "
   "follow the links from the overview and the READMEs.",
 "kb_component":
   "One component by exact name or alias (DSS, DMS, CDS, CCS...): its catalog entry "
   "(system, kind, repo, role), its documents, the facts the extractors found at the "
   "indexed release (entry points, RPCs, outbound calls, tables, handlers...), its task "
   "routes and its build dependencies. Returns exactly what was extracted.",
 "kb_dependencies":
   "Build-file dependencies of a component or repo, in both directions, with versions.",
 "kb_flow":
   "The path of one kind of task across subsystems, by flow name, task name or fbName "
   "(exact). With no argument, lists the flows and task names.",
 "kb_contract":
   "Every extracted declaration of an exact identifier: an RPC, proto service or message, "
   "GraphQL field, table or event type, with file:line and commit, plus its document in "
   "contracts/ or tables/. 'declared in' is not 'served by': repos copy protos.",
 "kb_freshness":
   "Which release each repo was indexed at, and which repos are not current. Check before "
   "describing behaviour as current.",
 "kb_trace":
   "Level 2: what one handler does inside its service, method by method, from the extracted "
   "call graph (Java: declared-type resolution; Python: pyright). Give an exact name - "
   "`Class#method`, `Class.method`, `module:func` or a full id - and optionally repo= (a repo "
   "or component). Each step has repo@branch:path:line and the RPCs it calls or serves and "
   "the Kafka/HTTP/Jenkins calls it makes at its own lines. direction='in' walks callers "
   "instead. A name in several classes returns the candidates, not a guess. Use after "
   "kb_component or kb_flow has named the handler.",
}

def _server(name: str):
    """mcp 2.x renamed FastMCP to MCPServer and changed how the port is set.
    Support both rather than pinning to the old API."""
    try:
        from mcp.server.mcpserver import MCPServer          # mcp >= 2
        return MCPServer(name), 2
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP              # mcp 1.x
        return FastMCP(name), 1
    except ImportError as e:
        # Exit NON-ZERO. Exiting 0 made a dead transport look like a clean shutdown, so
        # Claude Code showed no tools and no error.
        raise SystemExit(
            "ERROR: the `mcp` package is required for the MCP transport.\n"
            "  pip install -r tools/requirements-mcp.txt\n"
            "The knowledge base itself needs no dependencies - use tools/query.py or\n"
            "import tools/kb_core.py directly if you only need the data."
        ) from e

def build():
    app, _api = _server("e5-platform-kb")
    import kb_nav as nav

    # Every tool re-checks the facts on disk before answering, so a long-lived server
    # never answers from facts it read before the last sync.
    _tool = app.tool

    def tool(*a, **kw):
        def deco(fn):
            @functools.wraps(fn)
            def guarded(*args, **kwargs):
                core.refresh_if_stale()
                return fn(*args, **kwargs)
            return _tool(*a, **kw)(guarded)
        return deco

    @tool(description=DESCRIPTIONS["kb_overview"])
    def kb_overview() -> dict:
        return nav.overview()

    @tool(description=DESCRIPTIONS["kb_read"])
    def kb_read(path: str) -> dict:
        return nav.read(path)

    @tool(description=DESCRIPTIONS["kb_component"])
    def kb_component(name: str) -> dict:
        return nav.component(name)

    @tool(description=DESCRIPTIONS["kb_dependencies"])
    def kb_dependencies(name: str) -> dict:
        return nav.dependencies(name)

    @tool(description=DESCRIPTIONS["kb_flow"])
    def kb_flow(name: str | None = None) -> dict:
        return nav.flow(name)

    @tool(description=DESCRIPTIONS["kb_contract"])
    def kb_contract(identifier: str) -> dict:
        return nav.contract(identifier)

    @tool(description=DESCRIPTIONS["kb_freshness"])
    def kb_freshness() -> dict:
        return nav.freshness()

    @tool(description=DESCRIPTIONS["kb_trace"])
    def kb_trace(name: str, repo: str | None = None, depth: int = 3,
                 direction: str = "out") -> dict:
        return nav.trace(name, repo, depth, direction)

    return app


# --------------------------------------------------------------------------- auth
TOKEN_ENV = "E5_MCP_TOKEN"


class BearerAuth:
    """Plain ASGI bearer-token gate in front of the MCP transport.

    stdio needs none of this: the transport IS the trust boundary, because the process
    is spawned by the agent on the user's own machine. HTTP has no such boundary. The
    server previously bound a port and answered every caller, which is survivable on
    localhost and not survivable the moment it moves to a shared host - which is
    exactly what the hosted deployment does.

    Written against the raw ASGI interface rather than a Starlette middleware class so
    it does not move when the MCP SDK changes its web framework, which it already did
    once between 1.x and 2.x.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("path", "").rstrip("/").endswith("/healthz"):
            return await self.app(scope, receive, send)
        header = ""
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                header = v.decode("latin-1")
                break
        got = header[7:].strip() if header[:7].lower() == "bearer " else ""
        # constant-time: a token check that returns early leaks the token by timing
        if not (got and hmac.compare_digest(got, self.token)):
            body = b'{"error":"unauthorized"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"www-authenticate", b"Bearer"),
                                    (b"content-length", str(len(body)).encode())]})
            return await send({"type": "http.response.body", "body": body})
        await self.app(scope, receive, send)


def serve_http(app, host: str, port: int) -> int:
    """Serve Streamable HTTP, authenticated. Refuses rather than serving open."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        print(f"refusing to serve HTTP with no ${TOKEN_ENV} set.\n"
              f"  An unauthenticated MCP endpoint hands the whole knowledge base, and\n"
              f"  the shape of the platform, to anyone who can reach the port.\n\n"
              f"  export {TOKEN_ENV}=\"$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')\"",
              file=sys.stderr)
        return 2
    if len(token) < 16:
        print(f"${TOKEN_ENV} is {len(token)} characters; use at least 16.", file=sys.stderr)
        return 2
    if not hasattr(app, "streamable_http_app"):
        print("this MCP SDK exposes no ASGI app, so the auth gate cannot be installed.\n"
              "  Serving HTTP unauthenticated is not offered. Use stdio, or upgrade mcp.",
              file=sys.stderr)
        return 2
    import uvicorn
    inner = app.streamable_http_app(host=host)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  listening on {host}:{port} - reachable beyond this machine. "
              f"Bearer token required.", file=sys.stderr)
    uvicorn.run(BearerAuth(inner, token), host=host, port=port, log_level="info")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=7100)
    # Default stays loopback. A container must opt in explicitly with --host 0.0.0.0,
    # so "reachable from the network" is never something that happens by accident.
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--selftest", action="store_true",
                    help="exercise every tool through kb_nav without needing `mcp`")
    a = ap.parse_args()

    if a.selftest:
        import kb_nav as nav
        checks = [("kb_overview", nav.overview),
                  ("kb_read", lambda: nav.read("subsystems/README.md")),
                  ("kb_component", lambda: nav.component("DSS")),
                  ("kb_dependencies", lambda: nav.dependencies("platform-core")),
                  ("kb_flow", lambda: nav.flow("DEPLOYMENT_ACTION")),
                  ("kb_contract", lambda: nav.contract("UpdateFbWorkerPodDetails")),
                  ("kb_freshness", nav.freshness),
                  ("kb_trace", lambda: nav.trace("WorkflowManagerTaskHandler#pollForTask",
                                                 depth=1))]
        total = 0
        for name, fn in checks:
            r = fn(); t = core.est_tokens(r); total += t
            print(f"  {name:16} {'ok ' if r.get('ok', True) else 'ERR'}  est_tokens={t:6}")
        print(f"\n  {len(checks)} tools, {total} tokens if every one were called once.")
        return 0

    app = build()
    if a.http:
        return serve_http(app, a.host, a.port)
    app.run(transport="stdio")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

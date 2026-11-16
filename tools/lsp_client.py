"""A minimal Language Server Protocol client over stdio, for pyright-langserver.

Only what tools/call_graph.py needs: initialize, open files, prepareCallHierarchy and
outgoingCalls. Server-to-client requests (workspace/configuration, registerCapability,
progress) are answered with empty results so the server never waits on us.

Why pyright and not the Python `ast`: measured in the four-approach trial on 2026-11-14
(memory `ast-vs-lsp-trial-result`), a syntactic Python extractor has to guess a callee by its
name whenever the receiver's type is not written down, and Python rarely writes it down.
pyright infers it. For Java the declared-type resolver in tools/java_ast.py matched bytecode
and jdtls exactly, so no Java language server is used.
"""
from __future__ import annotations
import json, os, shutil, subprocess, threading, queue
from pathlib import Path


def find_pyright() -> str | None:
    """$E5_PYRIGHT_LANGSERVER, then the harness venv (`./e5 setup` installs it there), then
    PATH, then ~/.local/bin (pipx / pip --user)."""
    venv = Path(__file__).resolve().parent.parent / ".venv-mcp" / "bin" / "pyright-langserver"
    return (os.environ.get("E5_PYRIGHT_LANGSERVER")
            or (str(venv) if venv.exists() else None)
            or shutil.which("pyright-langserver")
            or next((str(p) for p in (Path.home() / ".local/bin/pyright-langserver",)
                     if p.exists()), None))


class LSP:
    def __init__(self, cmd: list[str], root: Path, timeout: float = 60):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL)
        self.timeout, self.next_id = timeout, 0
        self.replies: dict[int, queue.Queue] = {}
        self.lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()
        uri = root.resolve().as_uri()
        self.request("initialize", {
            "processId": os.getpid(), "rootUri": uri,
            "workspaceFolders": [{"uri": uri, "name": root.name}],
            "capabilities": {"textDocument": {"callHierarchy": {}}, "workspace": {
                "configuration": True, "workspaceFolders": True}},
            "initializationOptions": {}})
        self.notify("initialized", {})
        # pyright does not start analysing until it has a configuration. Without this
        # notification it answers `initialize` and then waits, silently, forever.
        self.notify("workspace/didChangeConfiguration", {"settings": {"python": {"analysis": {
            "diagnosticMode": "openFilesOnly", "typeCheckingMode": "off"}}}})

    # ------------------------------------------------------------------ wire
    def _send(self, msg: dict) -> None:
        body = json.dumps(msg).encode()
        with self.lock:
            self.p.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            self.p.stdin.flush()

    def _reader(self) -> None:
        out = self.p.stdout
        while True:
            n = 0
            while True:
                line = out.readline()
                if not line:
                    return
                if line in (b"\r\n", b"\n"):
                    break
                if line.lower().startswith(b"content-length:"):
                    n = int(line.split(b":")[1])
            msg = json.loads(out.read(n))
            if "id" in msg and "method" in msg:          # a request from the server
                res = None
                if msg["method"] == "workspace/configuration":
                    res = [None for _ in msg["params"].get("items", [])]   # defaults
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": res})
            elif "id" in msg and msg["id"] in self.replies:
                self.replies[msg["id"]].put(msg)

    def request(self, method: str, params: dict):
        self.next_id += 1
        i, q = self.next_id, queue.Queue()
        self.replies[i] = q
        self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        try:
            msg = q.get(timeout=self.timeout)
        finally:
            self.replies.pop(i, None)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error'].get('message')}")
        return msg.get("result")

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ------------------------------------------------------------------ helpers
    def open(self, f: Path, text: str) -> None:
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": f.resolve().as_uri(), "languageId": "python", "version": 1, "text": text}})

    def outgoing(self, f: Path, line0: int, col0: int) -> list[dict]:
        items = self.request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": f.resolve().as_uri()},
            "position": {"line": line0, "character": col0}}) or []
        out = []
        for it in items[:1]:
            out += self.request("callHierarchy/outgoingCalls", {"item": it}) or []
        return out

    def close(self) -> None:
        try:
            self.request("shutdown", {})
            self.notify("exit", {})
        except Exception:
            pass
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()

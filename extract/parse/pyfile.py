"""Python, through the standard library's `ast`. No dependency, and exact.

tree-sitter is not used here on purpose. For valid Python, `ast` is the language's own
parser: it cannot disagree with the interpreter, and it costs nothing to install. The
front ends are chosen per language rather than uniformly, because uniformity would mean
taking a dependency for a case the standard library already answers better.

    f = PyFile(src)
    f.functions()        name, line, decorators, and WHETHER IT IS TOP LEVEL
    f.imports()
    f.env_bindings()     NAME = os.getenv("OTHER_NAME")
    f.calls()            callee plus keyword arguments

WHAT `ast` FIXES HERE
---------------------
`^def\\s+([a-z][a-z0-9_]*)\\s*\\(` with `re.M` is how the RPA keyword surface was read. It
matched three things it should not have: methods inside classes (indentation is invisible
to `^` under re.M when the line starts with spaces... and visible when it does not, so
the behaviour depended on formatting), nested helper functions, and any `def` appearing
inside a docstring or a comment. A component's PUBLIC surface is its module-level
functions, and `ast` is the only thing that knows which those are.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class Func:
    name: str
    line: int
    top_level: bool
    decorators: tuple = ()
    owner: str = ""          # enclosing class, when there is one
    is_async: bool = False


@dataclass(frozen=True)
class Cls:
    name: str
    line: int
    bases: tuple = ()
    decorators: tuple = ()


@dataclass(frozen=True)
class Str:
    """A string CONSTANT, with where it sits. Mirrors `java.Str` so an extractor can
    read both languages without caring which it is looking at."""
    value: str
    line: int
    role: str                # KWARG | CALL_ARG | ASSIGN | RETURN | DOCSTRING | OTHER
    context: str = ""        # the keyword name, the callee, or the assigned name


@dataclass(frozen=True)
class Call:
    callee: str
    line: int
    args: tuple = ()         # positional, as source text
    kwargs: tuple = ()       # ((name, text), ...)


def _name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}" if node.value is not None else node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return ""


class PyFile:
    def __init__(self, text: str):
        self.text = text
        try:
            self.tree = ast.parse(text)
            self.ok = True
        except SyntaxError:
            # A file that does not parse is reported as such rather than silently
            # yielding nothing, which is how a repo on an unsupported syntax version
            # would look identical to a repo with no keywords.
            self.tree = ast.Module(body=[], type_ignores=[])
            self.ok = False

    def functions(self) -> list[Func]:
        out = []
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            out.append(Func(
                name=node.name, line=node.lineno,
                top_level=node in self.tree.body,
                decorators=tuple(_name(d) for d in node.decorator_list),
                owner=self._owner(node),
                is_async=isinstance(node, ast.AsyncFunctionDef)))
        return out

    def _owner(self, target) -> str:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and target in node.body:
                return node.name
        return ""

    def classes(self) -> list[Cls]:
        """Python class declarations.

        Needed because several extractors legitimately span languages: a `*Aggregator`
        is a handler whether it is written in Java or in Python, and devops-pipeline's
        `WorkflowAggregator` is Python. Omitting this made the first port of `handlers`
        lose a real class, which the differential harness caught.
        """
        return [Cls(name=n.name, line=n.lineno,
                    bases=tuple(_name(b) for b in n.bases),
                    decorators=tuple(_name(d) for d in n.decorator_list))
                for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef)]

    def imports(self) -> list[tuple[str, int]]:
        out = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                out += [(a.name, node.lineno) for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                out += [(f"{node.module}.{a.name}", node.lineno) for a in node.names]
        return out

    def env_bindings(self) -> list[tuple[str, str, int]]:
        """`VIRTUAL_QNAME = os.getenv("VIRTUAL_QNAME", "")` -> (var, env_var, line).

        The variable and the env var are not always spelled the same -
        rf-fb-worker-core has `PLATFORM_VIRTUAL_QUEUE = os.getenv("PLATFORM_VIRTUAL_QNAME")`
        - and the ENV VAR is the contract while the variable is local. Both are returned
        so a caller cannot accidentally use the wrong one.
        """
        out = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            callee = _name(node.value.func)
            if callee not in ("os.getenv", "os.environ.get", "getenv", "environ.get"):
                continue
            if not node.value.args or not isinstance(node.value.args[0], ast.Constant):
                continue
            env = node.value.args[0].value
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.append((t.id, env, node.lineno))
        return out

    def strings(self) -> list[Str]:
        """Every string constant, with the construct it sits in.

        A docstring is a string whose value is the whole expression statement, and it
        is reported as DOCSTRING rather than dropped: the regex extractors matched
        identifiers inside docstrings and reported them as code, so the distinction has
        to be visible rather than silently applied.
        """
        out, role, ctx = [], {}, {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        role[id(a)], ctx[id(a)] = "CALL_ARG", _name(node.func)
                for k in node.keywords:
                    if isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
                        role[id(k.value)], ctx[id(k.value)] = "KWARG", k.arg or ""
            elif isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    role[id(node.value)] = "ASSIGN"
                    ctx[id(node.value)] = ", ".join(_name(t) for t in node.targets)
            elif isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                role[id(node.value)] = "RETURN"
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                role[id(node.value)] = "DOCSTRING"
        for node in ast.walk(self.tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            out.append(Str(value=node.value, line=node.lineno,
                           role=role.get(id(node), "OTHER"),
                           context=ctx.get(id(node), "")))
        return out

    def calls(self) -> list[Call]:
        out = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            out.append(Call(
                callee=_name(node.func), line=node.lineno,
                args=tuple(_name(a) for a in node.args),
                kwargs=tuple((k.arg or "", _name(k.value)) for k in node.keywords)))
        return out

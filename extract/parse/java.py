"""Java, through tree-sitter. Structure, never regex.

    f = JavaFile(src)
    f.types()            class / interface / enum / record declarations
    f.methods()          with annotations and enclosing type
    f.imports()
    f.strings()          every string literal WITH THE ROLE IT PLAYS
    f.switches()         selector plus case labels, literal and constant alike
    f.calls()            method invocations, with receiver and argument text
    f.constants()        `final String NAME = "value"` - the case-label dictionary
    f.field_reads()      `Foo.BAR` static accesses

WHY A PARSER AND NOT A REGEX
----------------------------
The regex extractors this replaces were corrected one defect at a time, and the defects
were all one defect: text has no structure, so a pattern matches wherever the characters
occur.

  * `(?:class|interface)\\s+(\\w*TaskHandler\\w*)` matches inside a comment that MENTIONS
    TaskHandler, and inside a string literal containing it.
  * `^def\\s+([a-z]\\w*)` in Python matched methods inside classes and text inside
    docstrings, so a private method became part of a component's public keyword surface.
  * The single most expensive one: a SQL comment `-- Create table x` matched `CREATE`,
    made `CREATE` the table name, and `finditer` then resumed past the real statement, so
    a real table was never extracted at all.

`strings()` returning the ROLE is the part that earns the dependency. It is how
`"monitoring_service_vq"` passed to `getTask(...)` in production code is told apart from
the same literal declared as a constant in a JUnit fixture - a distinction no amount of
regex tuning can make, and one this estate needs because 31% of its queue facts came
from fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

try:
    import tree_sitter_java
    from tree_sitter import Language, Parser
except ImportError as e:            # pragma: no cover - the message IS the behaviour
    raise ImportError(
        "the Java front end needs tree-sitter:\n"
        "    python3 -m pip install -r tools/requirements-extract.txt\n"
        "Retrieval does not need it - only extraction does. See extract/PORTING.md."
    ) from e


@lru_cache(maxsize=1)
def _parser() -> Parser:
    return Parser(Language(tree_sitter_java.language()))


# The syntactic position of a string literal. Deliberately coarse: these are the
# distinctions the platform knowledge actually turns on, and a finer taxonomy would be
# a parser detail leaking into the extractors.
ANNOTATION = "ANNOTATION"        # @KafkaListener(topics = "...")
CONSTANT = "CONSTANT_FIELD"      # static final String X = "..."
SWITCH_LABEL = "SWITCH_LABEL"    # case "PAUSE_OR_RESUME_APP" ->
CALL_ARG = "CALL_ARG"            # getTask("...")
LOCAL_VAR = "LOCAL_VAR"          # String x = "..."
RETURN = "RETURN"                # return "..."
OTHER = "OTHER"


@dataclass(frozen=True)
class Str:
    value: str
    line: int
    role: str
    owner: str = ""          # enclosing method, where there is one
    context: str = ""        # the annotation name, the invoked method, or the field name


@dataclass(frozen=True)
class Type:
    name: str
    kind: str                # class | interface | enum | record
    line: int
    extends: str = ""
    implements: tuple = ()
    modifiers: tuple = ()


@dataclass(frozen=True)
class Method:
    name: str
    line: int
    owner: str
    annotations: tuple = ()
    modifiers: tuple = ()


@dataclass(frozen=True)
class Call:
    name: str
    line: int
    receiver: str = ""
    args: tuple = ()         # argument SOURCE TEXT, not values


@dataclass(frozen=True)
class Arm:
    """One `case ... ->` or `case ...:` group, with its labels AND its body.

    Arms rather than a flat label list, because the routing question is not "which task
    names appear" but "which task name maps to which destination", and that is the
    label-to-body association.
    """
    labels: tuple          # ((text, is_literal), ...)
    body: str
    line: int
    calls: tuple = ()      # (Call, ...) made INSIDE this arm and no other


@dataclass(frozen=True)
class Switch:
    selector: str
    line: int
    owner: str
    arms: tuple = ()         # (Arm, ...)
    has_default: bool = False
    style: str = ""          # arrow | colon - Java forbids mixing them in one switch

    @property
    def labels(self) -> tuple:
        return tuple(l for a in self.arms for l in a.labels)


def _txt(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf8", "replace")


def _walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


class JavaFile:
    def __init__(self, text: str):
        self.src = text.encode("utf8", "replace")
        self.tree = _parser().parse(self.src)

    # ---------------------------------------------------------------- helpers
    def _t(self, node) -> str:
        return _txt(node, self.src)

    def _enclosing(self, node, kinds, name_field="name") -> str:
        n = node.parent
        while n is not None:
            if n.type in kinds:
                c = n.child_by_field_name(name_field)
                return self._t(c) if c is not None else ""
            n = n.parent
        return ""

    def _owner_method(self, node) -> str:
        return self._enclosing(node, {"method_declaration", "constructor_declaration"})

    def _owner_type(self, node) -> str:
        return self._enclosing(node, {"class_declaration", "interface_declaration",
                                      "enum_declaration", "record_declaration"})

    # ---------------------------------------------------------------- surfaces
    def types(self) -> list[Type]:
        out = []
        kinds = {"class_declaration": "class", "interface_declaration": "interface",
                 "enum_declaration": "enum", "record_declaration": "record"}
        for n in _walk(self.tree.root_node):
            if n.type not in kinds:
                continue
            nm = n.child_by_field_name("name")
            if nm is None:
                continue
            sup = n.child_by_field_name("superclass")
            ifc = n.child_by_field_name("interfaces")
            mods = n.child_by_field_name("modifiers") or next(
                (c for c in n.children if c.type == "modifiers"), None)
            out.append(Type(
                name=self._t(nm), kind=kinds[n.type], line=nm.start_point[0] + 1,
                extends=self._t(sup).removeprefix("extends").strip() if sup else "",
                implements=tuple(self._t(c) for c in (ifc.children if ifc else [])
                                 if c.type in ("type_list", "type_identifier")),
                modifiers=tuple(self._t(c) for c in (mods.children if mods else []))))
        return out

    def methods(self) -> list[Method]:
        out = []
        for n in _walk(self.tree.root_node):
            if n.type != "method_declaration":
                continue
            nm = n.child_by_field_name("name")
            if nm is None:
                continue
            anns, mods = [], []
            for c in n.children:
                if c.type != "modifiers":
                    continue
                for m in c.children:
                    if m.type in ("annotation", "marker_annotation"):
                        a = m.child_by_field_name("name")
                        anns.append(self._t(a) if a is not None else self._t(m))
                    else:
                        mods.append(self._t(m))
            out.append(Method(name=self._t(nm), line=nm.start_point[0] + 1,
                              owner=self._owner_type(n), annotations=tuple(anns),
                              modifiers=tuple(mods)))
        return out

    def imports(self) -> list[tuple[str, int]]:
        out = []
        for n in _walk(self.tree.root_node):
            if n.type != "import_declaration":
                continue
            txt = self._t(n).removeprefix("import").strip()
            txt = txt.removeprefix("static").strip().rstrip(";").strip()
            out.append((txt, n.start_point[0] + 1))
        return out

    def strings(self) -> list[Str]:
        """Every string literal, with the construct it sits in."""
        out = []
        for n in _walk(self.tree.root_node):
            if n.type != "string_literal":
                continue
            raw = self._t(n)
            val = raw[1:-1] if len(raw) >= 2 and raw[0] in "\"'" else raw
            role, ctx = self._role(n)
            out.append(Str(value=val, line=n.start_point[0] + 1, role=role,
                           owner=self._owner_method(n), context=ctx))
        return out

    def _role(self, node) -> tuple[str, str]:
        n, child = node.parent, node
        while n is not None:
            if n.type in ("annotation", "marker_annotation"):
                a = n.child_by_field_name("name")
                return ANNOTATION, self._t(a) if a is not None else ""
            if n.type == "switch_label":
                return SWITCH_LABEL, ""
            if n.type == "argument_list" and n.parent is not None and \
                    n.parent.type in ("method_invocation", "object_creation_expression"):
                callee = n.parent.child_by_field_name("name")
                return CALL_ARG, self._t(callee) if callee is not None else ""
            if n.type in ("field_declaration", "constant_declaration"):
                d = n.child_by_field_name("declarator")
                nm = d.child_by_field_name("name") if d is not None else None
                return CONSTANT, self._t(nm) if nm is not None else ""
            if n.type == "local_variable_declaration":
                d = n.child_by_field_name("declarator")
                nm = d.child_by_field_name("name") if d is not None else None
                return LOCAL_VAR, self._t(nm) if nm is not None else ""
            if n.type == "return_statement":
                return RETURN, ""
            if n.type in ("method_declaration", "class_declaration", "program"):
                break
            child, n = n, n.parent
        return OTHER, ""

    def _calls_in(self, node) -> list[Call]:
        out = []
        for n in _walk(node):
            if n.type != "method_invocation":
                continue
            nm = n.child_by_field_name("name")
            if nm is None:
                continue
            obj = n.child_by_field_name("object")
            al = n.child_by_field_name("arguments")
            args = tuple(self._t(c) for c in (al.children if al else [])
                         if c.type not in ("(", ")", ","))
            out.append(Call(name=self._t(nm), line=nm.start_point[0] + 1,
                            receiver=self._t(obj) if obj is not None else "",
                            args=args))
        return out

    def calls(self) -> list[Call]:
        return self._calls_in(self.tree.root_node)

    def constants(self) -> dict:
        r"""`static final String NAME = "value"` -> {NAME: (value, line)}.

        The case-label dictionary: the platform writes the same routing table once with
        string literals and once with named constants, so resolving a constant label to
        its wire value is what lets the two be compared at all.

        An interface field is implicitly `public static final`, so the keyword is absent
        and the regex this replaces (`(?:static\s+)?final\s+String`) could not see it.
        tree-sitter gives it a node type of its own - `constant_declaration` - which is
        the grammar stating the same thing.
        """
        out = {}
        for n in _walk(self.tree.root_node):
            if n.type not in ("field_declaration", "constant_declaration"):
                continue
            ty = n.child_by_field_name("type")
            if ty is None or self._t(ty) != "String":
                continue
            if n.type == "field_declaration":
                mods = next((c for c in n.children if c.type == "modifiers"), None)
                if mods is None or "final" not in self._t(mods).split():
                    continue
            for d in n.children:
                if d.type != "variable_declarator":
                    continue
                nm, val = d.child_by_field_name("name"), d.child_by_field_name("value")
                if nm is None or val is None or val.type != "string_literal":
                    continue
                out.setdefault(self._t(nm), (self._t(val)[1:-1],
                                             nm.start_point[0] + 1))
        return out

    def switches(self) -> list[Switch]:
        """Both Java switch forms, and both label forms.

        `tools/extractors.py` needed two regexes here - `case X ->` and `case X:` - and
        records that the platform writes the SAME routing table twice, once with string
        literals and once with named constants, so the compiler cannot compare them. The
        parser sees both as case labels and reports whether each was a literal, which is
        the distinction `check_routing.py` exists to police.
        """
        out = []
        for n in _walk(self.tree.root_node):
            if n.type not in ("switch_expression", "switch_statement"):
                continue
            cond = n.child_by_field_name("condition")
            body = n.child_by_field_name("body")
            arms, has_default, style = [], False, ""
            # `switch_rule` is the arrow form; `switch_block_statement_group` the colon
            # form. The platform writes its routing table in BOTH, in different classes,
            # which is why neither can be assumed.
            for grp in (body.children if body is not None else []):
                if grp.type not in ("switch_rule", "switch_block_statement_group"):
                    continue
                style = "arrow" if grp.type == "switch_rule" else "colon"
                labels, body_parts = [], []
                for c in grp.children:
                    if c.type == "switch_label":
                        for lc in c.children:
                            if lc.type == "string_literal":
                                labels.append((self._t(lc).strip('"'), True))
                            elif lc.type in ("identifier", "field_access",
                                             "scoped_identifier"):
                                labels.append((self._t(lc), False))
                    elif c.type not in (":", "->"):
                        body_parts.append(self._t(c))
                if labels:
                    arms.append(Arm(labels=tuple(labels), body=" ".join(body_parts),
                                    line=grp.start_point[0] + 1,
                                    calls=tuple(self._calls_in(grp))))
                elif any(c.type == "switch_label" and "default" in self._t(c)
                         for c in grp.children):
                    # A default arm has no labels, so it is not an Arm - but its
                    # ABSENCE is the finding `check_routing.py` blocks on, and that
                    # cannot be read from a list that never contained it.
                    has_default = True
            out.append(Switch(selector=self._t(cond).strip("()") if cond else "",
                              line=n.start_point[0] + 1, owner=self._owner_method(n),
                              arms=tuple(arms), has_default=has_default,
                              style=style))
        return out

    def field_reads(self) -> list[tuple[str, str, int]]:
        """`DBAccessType.STATE` -> ("DBAccessType", "STATE", line)."""
        out = []
        for n in _walk(self.tree.root_node):
            if n.type != "field_access":
                continue
            obj = n.child_by_field_name("object")
            fld = n.child_by_field_name("field")
            if obj is None or fld is None or obj.type != "identifier":
                continue
            out.append((self._t(obj), self._t(fld), n.start_point[0] + 1))
        return out

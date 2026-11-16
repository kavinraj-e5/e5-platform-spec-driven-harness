"""GraphQL SDL, through `graphql-core`. The reference parser, not a line regex.

    f = GqlFile(src)
    f.ok / f.error
    f.types()          object, input, interface, enum, union, scalar - and extensions
    f.fields(name)     the fields of one type, with their own lines and arguments

WHY THE REFERENCE PARSER
------------------------
The regex version read a root type's body **one line at a time** with
`(\\w+)\\s*(\\([^)]*\\))?\\s*:\\s*(.+)`. Three things in ordinary SDL defeat that:

  * an argument list broken across lines - `[^)]*` never closes on one line;
  * a `\"\"\"description\"\"\"` block, which does not match and is silently skipped;
  * a field whose type is written on the next line.

And it gave every root field the **root type's** line, so `Query.foo` and `Query.bar`
cited the same line - the same defect the proto port found for rpcs.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    from graphql import parse as _parse, print_ast
    from graphql.error import GraphQLSyntaxError
    from graphql.language import ast as A
except ImportError as e:            # pragma: no cover - the message IS the behaviour
    raise ImportError(
        "the GraphQL front end needs graphql-core:\n"
        "    python3 -m pip install -r tools/requirements-extract.txt\n"
        "Retrieval does not need it - only extraction does. See extract/PORTING.md."
    ) from e


@dataclass(frozen=True)
class Arg:
    name: str
    type: str


@dataclass(frozen=True)
class GqlField:
    name: str
    line: int
    type: str
    args: tuple = ()        # (Arg, ...)


@dataclass(frozen=True)
class GqlType:
    name: str
    kind: str               # type | input | interface | enum | union | scalar
    line: int
    fields: tuple = ()      # (GqlField, ...)
    values: tuple = ()      # enum values
    is_extension: bool = False


_KINDS = {
    A.ObjectTypeDefinitionNode: "type",
    A.InputObjectTypeDefinitionNode: "input",
    A.InterfaceTypeDefinitionNode: "interface",
    A.EnumTypeDefinitionNode: "enum",
    A.UnionTypeDefinitionNode: "union",
    A.ScalarTypeDefinitionNode: "scalar",
}
_EXTENSIONS = {
    A.ObjectTypeExtensionNode: "type",
    A.InputObjectTypeExtensionNode: "input",
    A.InterfaceTypeExtensionNode: "interface",
    A.EnumTypeExtensionNode: "enum",
    A.UnionTypeExtensionNode: "union",
}


def _type_str(node) -> str:
    return print_ast(node)


class GqlFile:
    def __init__(self, text: str):
        self.ok, self.error, self._types = True, "", []
        try:
            doc = _parse(text, no_location=False)
        except GraphQLSyntaxError as e:
            self.ok, self.error = False, str(e).replace("\n", " ")[:200]
            return
        for d in doc.definitions:
            kind = _KINDS.get(type(d)) or _EXTENSIONS.get(type(d))
            if kind is None:
                continue
            self._types.append(GqlType(
                name=d.name.value, kind=kind, line=d.loc.start_token.line,
                is_extension=type(d) in _EXTENSIONS,
                fields=tuple(
                    GqlField(name=f.name.value, line=f.loc.start_token.line,
                             type=_type_str(f.type),
                             args=tuple(Arg(a.name.value, _type_str(a.type))
                                        for a in getattr(f, "arguments", ()) or ()))
                    for f in (getattr(d, "fields", ()) or ())),
                values=tuple(v.name.value for v in (getattr(d, "values", ()) or ()))))

    def types(self) -> list[GqlType]:
        return list(self._types)

    def fields(self, name: str) -> list[GqlField]:
        return [f for t in self._types if t.name == name for f in t.fields]

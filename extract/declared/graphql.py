"""The GraphQL surface `interface` exposes: types, and the root operations.

A root field is the platform's HTTP-facing contract - every Neos call enters here - so
its line number is evidence a reader follows. The regex gave all of them the line of
the `type Query {` that contains them.
"""
from __future__ import annotations

from extract.core import Repo, fact
from extract.parse.gqlfile import GqlFile

ROOTS = ("Query", "Mutation", "Subscription")
_SDL = (".graphqls",)
_MAYBE_SDL = (".graphql", ".gql")


def _is_sdl(path: str) -> bool:
    # A `.graphql` outside `resources/` is an OPERATION document - a client-side query,
    # not a schema. Kept from the regex version: it is a selection rule, and a correct
    # one.
    return path.endswith(_SDL) or (path.endswith(_MAYBE_SDL) and "resources" in path)


def graphql(repo: Repo) -> list[dict]:
    out = []
    for path in sorted(repo.files(*_SDL, *_MAYBE_SDL, include_tests=True)):
        if not _is_sdl(path):
            continue
        f = GqlFile(repo.read(path))
        if not f.ok:
            out.append(fact("GraphQLParseError", path, repo.id, path, 1, "code",
                            issue="SDL does not parse", detail=f.error,
                            severity="high"))
            continue
        for t in f.types():
            out.append(fact("GraphQLType", t.name, repo.id, path, t.line, "code",
                            graphql_kind=t.kind, field_count=len(t.fields),
                            **({"enum_values": list(t.values)} if t.values else {}),
                            **({"is_extension": True} if t.is_extension else {})))
            if t.kind != "type" or t.name not in ROOTS:
                continue
            for fl in t.fields:
                out.append(fact("GraphQLRootField", f"{t.name}.{fl.name}", repo.id,
                                path, fl.line, "code", operation=t.name.lower(),
                                field=fl.name, returns=fl.type,
                                args=[{"name": a.name, "type": a.type}
                                      for a in fl.args]))
    return out

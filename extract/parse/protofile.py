"""Protobuf, through `protoc` descriptor sets. The compiler, not a pattern.

    s = ProtoSet(repo)
    s.files()           one Desc per .proto, in repo order
    d.services / d.messages / d.enums     with LINE NUMBERS from SourceCodeInfo

WHY THE COMPILER
----------------
A `.proto` file is the one artefact in this estate that ships with its own parser, and
the parser answers two questions a regex structurally cannot:

  * **It follows `import`.** `rpc Delete(DeploymentVersionId) returns (Empty)` says
    nothing about which `Empty`; the descriptor says `.google.protobuf.Empty`. A
    short name cannot join two repositories, a fully-qualified one can - and that join
    is the point of extracting protos at all.
  * **It scopes nesting.** A `message` declared inside another is a different type with
    a different wire identity. The regex read brace blocks with `_block()` and reported
    a nested message as a top-level one, with the enclosing message's fields mixed in.

It also carries `SourceCodeInfo`, so every rpc gets ITS OWN line. The regex gave every
rpc in a service the SERVICE's line - so `./e5 cite check` was verifying, for 118 rpcs
in deployment-mgmt, that one line exists.

WHEN IT CANNOT COMPILE
----------------------
`.ok` is False and `.error` says why, per file. That is a finding about the repository,
not a reason to go quiet: an extractor that silently returns nothing when the compiler
fails is worse than one that never used the compiler.
"""
from __future__ import annotations

import hashlib
import pathlib
import tempfile
from dataclasses import dataclass, field

try:
    import grpc_tools
    from grpc_tools import protoc as _protoc
    from google.protobuf import descriptor_pb2
except ImportError as e:            # pragma: no cover - the message IS the behaviour
    raise ImportError(
        "the proto front end needs grpcio-tools:\n"
        "    python3 -m pip install -r tools/requirements-extract.txt\n"
        "Retrieval does not need it - only extraction does. See extract/PORTING.md."
    ) from e

_WELL_KNOWN = str(pathlib.Path(grpc_tools.__file__).parent / "_proto")

# Field numbers inside FileDescriptorProto, which is how SourceCodeInfo addresses a
# declaration. Named rather than inlined because `[6, i, 2, j]` is unreadable twice.
_MESSAGE, _ENUM, _SERVICE = 4, 5, 6
_MSG_FIELD, _MSG_NESTED = 2, 3
_SVC_METHOD = 2


@dataclass(frozen=True)
class Rpc:
    name: str
    line: int
    request: str             # fully qualified, leading dot stripped
    response: str
    client_streaming: bool = False
    server_streaming: bool = False


@dataclass(frozen=True)
class Service:
    name: str
    line: int
    rpcs: tuple = ()


@dataclass(frozen=True)
class Field:
    name: str
    tag: int
    type: str                # `string`, `map<string, int32>`, or a qualified type name
    label: str               # "repeated" | "optional" | ""


@dataclass(frozen=True)
class Message:
    name: str                # scoped within the file: Outer.Inner
    line: int
    fields: tuple = ()
    is_map_entry: bool = False


@dataclass(frozen=True)
class Enum:
    name: str
    line: int
    values: tuple = ()


@dataclass
class Desc:
    path: str
    ok: bool = True
    error: str = ""
    package: str = ""
    md5: str = ""
    imports: tuple = ()
    services: tuple = ()
    messages: tuple = ()
    enums: tuple = ()


_SCALARS = {
    1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 6: "fixed64",
    7: "fixed32", 8: "bool", 9: "string", 12: "bytes", 13: "uint32", 15: "sfixed32",
    16: "sfixed64", 17: "sint32", 18: "sint64",
}


def _lines(fdp) -> dict:
    out = {}
    for loc in fdp.source_code_info.location:
        out.setdefault(tuple(loc.path), loc.span[0] + 1)
    return out


class ProtoSet:
    """Every .proto in one repo, compiled once.

    The whole repo at once because `import` is the reason to use the compiler: a file
    compiled in isolation cannot resolve a type its sibling declares.
    """

    def __init__(self, repo):
        self.repo = repo
        self.paths = sorted(repo.files(".proto", include_tests=True))
        self._files: list[Desc] | None = None

    def files(self) -> list[Desc]:
        if self._files is None:
            self._files = self._compile()
        return self._files

    # ------------------------------------------------------------------ compile
    def _compile(self) -> list[Desc]:
        if not self.paths:
            return []
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            for p in self.paths:
                fp = root / p
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(self.repo.read(p))
            dirs = sorted({str(root / p.rsplit("/", 1)[0]) for p in self.paths})
            # Every ancestor directory, because an import is written relative to
            # whichever root the build happens to use: workflow-ops keeps its protos
            # under `src/` and imports them as `proto/clients/v1/common.proto`.
            ancestors = {str(root)}
            for p in self.paths:
                parts = p.split("/")
                for i in range(1, len(parts)):
                    ancestors.add(str(root / "/".join(parts[:i])))
            out = []
            for p in self.paths:
                out.append(self._one(root, p, dirs, sorted(ancestors, key=len,
                                                           reverse=True)))
            return out

    def _one(self, root, path, dirs, ancestors) -> Desc:
        src = self.repo.read(path)
        d = Desc(path=path, md5=hashlib.md5(src.encode()).hexdigest())
        own = str(root / path.rsplit("/", 1)[0])
        # The file's OWN directory first. Three repos carry forks of
        # `pipeline_ops_v1.proto` at different depths, and protoc refuses an input it
        # can reach by two different relative names ("Input is shadowed").
        incs = ([own] + [x for x in dirs if x != own]
                + [x for x in ancestors if x not in dirs and x != own] + [_WELL_KNOWN])
        pb = root / "_descriptor.pb"
        rc = _protoc.main(["protoc"] + [f"-I{i}" for i in incs] +
                          [f"--descriptor_set_out={pb}", "--include_source_info",
                           str(root / path)])
        if rc != 0 or not pb.exists():
            d.ok, d.error = False, "protoc could not compile this file"
            return d
        fds = descriptor_pb2.FileDescriptorSet()
        fds.ParseFromString(pb.read_bytes())
        pb.unlink()
        fdp = next((f for f in fds.file if f.name.endswith(path.rsplit("/", 1)[-1])),
                   None)
        if fdp is None:
            d.ok, d.error = False, "descriptor set did not contain this file"
            return d
        self._read(d, fdp)
        return d

    # --------------------------------------------------------------------- read
    def _read(self, d: Desc, fdp) -> None:
        ln = _lines(fdp)
        d.package = fdp.package
        d.imports = tuple(fdp.dependency)
        maps = {}
        msgs: list[Message] = []
        for i, m in enumerate(fdp.message_type):
            self._message(m, "", (_MESSAGE, i), ln, msgs, maps)
        d.messages = tuple(m for m in msgs if not m.is_map_entry)
        d.enums = tuple(Enum(name=e.name, line=ln.get((_ENUM, i), 1),
                             values=tuple((v.name, v.number) for v in e.value))
                        for i, e in enumerate(fdp.enum_type))
        svcs = []
        for i, s in enumerate(fdp.service):
            rpcs = tuple(
                Rpc(name=mth.name, line=ln.get((_SERVICE, i, _SVC_METHOD, j), 1),
                    request=mth.input_type.lstrip("."),
                    response=mth.output_type.lstrip("."),
                    client_streaming=mth.client_streaming,
                    server_streaming=mth.server_streaming)
                for j, mth in enumerate(s.method))
            svcs.append(Service(name=s.name, line=ln.get((_SERVICE, i), 1), rpcs=rpcs))
        d.services = tuple(svcs)
        # A map field compiles to `repeated <Outer>.<X>Entry`. Restoring `map<k, v>` is
        # not cosmetic: the entry message is an implementation detail of the wire
        # format and naming it would invent a type the .proto does not declare.
        d.messages = tuple(
            Message(name=m.name, line=m.line, is_map_entry=False,
                    fields=tuple(self._unmap(f, d.package, maps) for f in m.fields))
            for m in d.messages)

    def _message(self, m, prefix, path, ln, out, maps) -> None:
        name = f"{prefix}{m.name}"
        fields = tuple(
            Field(name=f.name, tag=f.number, type=self._type(f),
                  label=("repeated" if f.label == 3
                         else "optional" if getattr(f, "proto3_optional", False)
                         else ""))
            for f in m.field)
        entry = bool(m.options.map_entry)
        if entry:
            maps[name] = (self._type(m.field[0]), self._type(m.field[1]))
        out.append(Message(name=name, line=ln.get(path, 1), fields=fields,
                           is_map_entry=entry))
        for k, n in enumerate(m.nested_type):
            self._message(n, f"{name}.", path + (_MSG_NESTED, k), ln, out, maps)

    @staticmethod
    def _type(f) -> str:
        if f.type_name:
            return f.type_name.lstrip(".")
        return _SCALARS.get(f.type, f"type_{f.type}")

    @staticmethod
    def _unmap(f: Field, package: str, maps: dict) -> Field:
        if f.label != "repeated":
            return f
        local = f.type[len(package) + 1:] if package and f.type.startswith(package + ".") \
            else f.type
        kv = maps.get(local)
        if not kv:
            return f
        return Field(name=f.name, tag=f.tag, label="", type=f"map<{kv[0]}, {kv[1]}>")

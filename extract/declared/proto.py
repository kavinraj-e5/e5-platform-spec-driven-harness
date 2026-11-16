"""gRPC services, rpcs, messages and enums, from the compiler's own descriptor set.

The cross-repo value here is type IDENTITY. `returns (Empty)` is not joinable;
`google.protobuf.Empty` is, and so is `com.e5.platform.ss.proto.Empty` — and this
estate has both, in one file. Only the compiler can tell them apart, because only the
compiler follows `import`.
"""
from __future__ import annotations

from extract.core import Repo, fact
from extract.parse.protofile import ProtoSet


def _streaming(r) -> dict:
    """Only when there is some. A prop that is `None` on 99% of facts is noise in every
    reader and in every diff."""
    if not (r.client_streaming or r.server_streaming):
        return {}
    kind = ("bidi" if r.client_streaming and r.server_streaming
            else "client" if r.client_streaming else "server")
    return {"streaming": kind}


def _q(package: str, name: str) -> str:
    return f"{package}.{name}" if package else name


def proto(repo: Repo) -> list[dict]:
    out = []
    for d in ProtoSet(repo).files():
        if not d.ok:
            # An extractor that goes quiet when the compiler fails is worse than one
            # that never used the compiler: the facts silently lose a whole service.
            out.append(fact("ProtoParseError", d.path, repo.id, d.path, 1, "code",
                            issue="protoc could not compile this file", detail=d.error,
                            severity="high"))
            continue
        for s in d.services:
            out.append(fact("GrpcService", _q(d.package, s.name), repo.id, d.path,
                            s.line, "code", package=d.package, rpc_count=len(s.rpcs),
                            proto_md5=d.md5, simple_name=s.name))
            for r in s.rpcs:
                out.append(fact("GrpcRpc", _q(d.package, f"{s.name}.{r.name}"),
                                repo.id, d.path, r.line, "code",
                                service=s.name, request=r.request,
                                response=r.response, rpc=r.name,
                                **_streaming(r)))
        for m in d.messages:
            out.append(fact("ProtoMessage", _q(d.package, m.name), repo.id, d.path,
                            m.line, "code",
                            fields=[{"label": f.label, "type": f.type,
                                     "name": f.name, "tag": f.tag} for f in m.fields],
                            field_count=len(m.fields),
                            **({"nested_in": m.name.rsplit(".", 1)[0]}
                               if "." in m.name else {})))
        for e in d.enums:
            out.append(fact("ProtoEnum", _q(d.package, e.name), repo.id, d.path, e.line,
                            "code", values=[v for v, _ in e.values],
                            value_count=len(e.values)))
    return out

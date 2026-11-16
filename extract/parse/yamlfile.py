"""YAML, through PyYAML's composer. Structure and line marks, never a line regex.

    f = YamlFile(src)
    f.ok               False if the document does not parse - and that is a FINDING
    f.nodes()          every node, scalar and container alike, with its path and line
    f.scalars()        the scalar subset
    f.at(path)         one node by path, or None

WHY THE COMPOSER AND NOT `yaml.safe_load`
-----------------------------------------
`safe_load` returns plain dicts, and a plain dict has no line numbers. A fact without a
line is a fact a reader cannot check, and `./e5 cite check` cannot verify. `compose_all`
returns the node tree with `start_mark`, which is the same parse with the provenance
still attached.

WHY NOT A LINE REGEX
--------------------
`extract_config` asked whether the NEXT LINE matched `value:` to decide whether a
`- name:` entry was an environment variable, because in a Kubernetes manifest the key
`name` also names containers, ports and volumes. That heuristic is the structure, badly:
the real question is whether the path ends in `...env[i].name`, which the composer
answers outright. It also needed `[A-Z][A-Z0-9_]{2,}` as a second guess, which quietly
made the NAMING CONVENTION part of the definition of an environment variable.

Paths are written `spec.template.spec.containers[0].env[3].name` - the form a reader can
paste into `yq`.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    import yaml
except ImportError as e:            # pragma: no cover - the message IS the behaviour
    raise ImportError(
        "the YAML front end needs PyYAML:\n"
        "    python3 -m pip install -r tools/requirements-extract.txt\n"
        "Retrieval does not need it - only extraction does. See extract/PORTING.md."
    ) from e


@dataclass(frozen=True)
class Node:
    path: str          # spec.template.spec.containers[0].env[3].name
    key: str           # the leaf key; "" for a sequence element
    value: str | None  # the scalar as written; None for a mapping or a sequence
    line: int          # 1-based, of the value
    key_line: int      # 1-based, of the key - what a reader looks at for a `name:` entry
    doc: int = 0       # which document in a multi-document file


class YamlFile:
    def __init__(self, text: str):
        self.ok = True
        self.error = ""
        self._nodes: list[Node] = []
        try:
            docs = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
        except yaml.YAMLError as e:
            # A file that does not parse and a file with nothing in it must not look
            # the same - the first is a defect in the repository, the second is not.
            self.ok, self.error = False, str(e).replace("\n", " ")[:200]
            return
        for i, d in enumerate(docs):
            if d is not None:
                self._collect(d, "", "", i, d.start_mark.line + 1)

    def _collect(self, node, path: str, key: str, doc: int, key_line: int) -> None:
        line = node.start_mark.line + 1
        if isinstance(node, yaml.ScalarNode):
            self._nodes.append(Node(path, key, node.value, line, key_line, doc))
            return
        self._nodes.append(Node(path, key, None, line, key_line, doc))
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                nm = k.value if isinstance(k, yaml.ScalarNode) else "?"
                self._collect(v, f"{path}.{nm}" if path else nm, nm, doc,
                              k.start_mark.line + 1)
        elif isinstance(node, yaml.SequenceNode):
            for j, v in enumerate(node.value):
                self._collect(v, f"{path}[{j}]", key, doc, line)

    def nodes(self) -> list[Node]:
        return list(self._nodes)

    def scalars(self) -> list[Node]:
        return [n for n in self._nodes if n.value is not None]

    def at(self, path: str, doc: int = 0) -> Node | None:
        return next((n for n in self._nodes if n.path == path and n.doc == doc), None)

    def has(self, path: str, doc: int = 0) -> bool:
        return self.at(path, doc) is not None

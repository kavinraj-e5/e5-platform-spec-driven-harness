"""A YAML *subset* reader, in the spirit of load_registry() in tools/index.py.

Why not pyyaml: the indexer and every gate it runs must work in a bare CI container
with nothing but python3. tools/index.py already hand-rolls a reader for registry.yaml;
release documents need a slightly richer subset, so this is that reader, extracted.

Supported: nested block mappings, block sequences (scalars and inline maps),
block scalars (`|`, `>`, with `-`/`+` chomping), flow `[]` / `{}` when empty or a
simple comma list, quoted and bare scalars, `true/false/null/~`, ints, floats,
`#` comments.

NOT supported (and deliberately so - a release document that needs them is too
clever): anchors, aliases, tags, multi-document streams, complex keys, nested flow
collections, multi-line flow collections.

Deviation from the YAML spec, documented on purpose: block scalars are returned
*chomped* (no trailing newline) regardless of the chomping indicator, because
nothing in the KB needs a trailing newline and round-tripping one through a
markdown table is pure noise. `|+` / `>+` keep interior blank lines as usual.
"""
import re

__all__ = ["load", "frontmatter", "YamlError"]


class YamlError(ValueError):
    """Raised with a 1-based line number whenever the subset is exceeded."""


_INT = re.compile(r'^-?\d+$')
_FLOAT = re.compile(r'^-?\d+\.\d+([eE][-+]?\d+)?$')
_KEY = re.compile(r'^(?P<k>"[^"]*"|\'[^\']*\'|[^:#]+?)\s*:(?:\s+(?P<v>.*))?$')
_BLOCK = re.compile(r'^[|>][-+]?$')


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _blank(line):
    s = line.strip()
    return s == "" or s.startswith("#")


def _skip(lines, i):
    while i < len(lines) and _blank(lines[i]):
        i += 1
    return i


def _strip_comment(s):
    """Drop a trailing ` # comment`, respecting quotes. Not a tokenizer; good enough
    for the one-line scalars a release document contains."""
    s = s.rstrip()
    if s[:1] in ('"', "'"):
        q = s[0]
        end = s.find(q, 1)
        if end != -1:
            return s[: end + 1]
        return s
    cut = s.find(" #")
    return s[:cut].rstrip() if cut != -1 else s


def _scalar(raw, lineno):
    s = _strip_comment(raw).strip()
    if s == "" or s in ("~", "null", "Null", "NULL"):
        return None
    if s in ("true", "True", "TRUE", "yes", "on"):
        return True
    if s in ("false", "False", "FALSE", "no", "off"):
        return False
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s == "[]":
        return []
    if s == "{}":
        return {}
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        if "[" in inner or "{" in inner:
            raise YamlError(f"line {lineno}: nested flow collections are not supported")
        return [_scalar(x, lineno) for x in inner.split(",")]
    if s.startswith("{") or s.startswith("["):
        raise YamlError(f"line {lineno}: unsupported flow collection {s!r}")
    if _INT.match(s):
        return int(s)
    if _FLOAT.match(s):
        return float(s)
    return s


def _block_scalar(lines, i, parent_indent, style):
    buf, base = [], None
    while i < len(lines):
        raw = lines[i]
        if raw.strip() == "":
            buf.append("")
            i += 1
            continue
        ind = _indent(raw)
        if ind <= parent_indent:
            break
        if base is None:
            base = ind
        buf.append(raw[base:] if len(raw) >= base else raw.strip())
        i += 1
    while buf and buf[-1] == "":
        buf.pop()
    if style[0] == "|":
        return "\n".join(buf), i
    paras, cur = [], []
    for b in buf:
        if b == "":
            paras.append(" ".join(cur))
            cur = []
        else:
            cur.append(b.strip())
    if cur:
        paras.append(" ".join(cur))
    return "\n".join(paras), i


def _parse_map(lines, i, indent):
    out = {}
    while True:
        i = _skip(lines, i)
        if i >= len(lines):
            break
        ind = _indent(lines[i])
        if ind < indent:
            break
        if ind > indent:
            raise YamlError(f"line {i + 1}: unexpected indent (expected {indent})")
        body = lines[i].strip()
        if body.startswith("- ") or body == "-":
            break
        m = _KEY.match(body)
        if not m:
            raise YamlError(f"line {i + 1}: not a `key: value` mapping entry: {body!r}")
        key = m.group("k").strip().strip("\"'")
        rest = (m.group("v") or "").strip()
        if key in out:
            raise YamlError(f"line {i + 1}: duplicate key {key!r}")
        if _BLOCK.match(rest):
            out[key], i = _block_scalar(lines, i + 1, indent, rest)
            continue
        if rest == "" or rest.startswith("#"):
            j = _skip(lines, i + 1)
            if j < len(lines) and _indent(lines[j]) > indent:
                out[key], i = _parse_block(lines, j, _indent(lines[j]))
            else:
                out[key], i = None, i + 1
            continue
        out[key] = _scalar(rest, i + 1)
        i += 1
    return out, i


def _parse_seq(lines, i, indent):
    out = []
    while True:
        i = _skip(lines, i)
        if i >= len(lines):
            break
        ind = _indent(lines[i])
        if ind < indent:
            break
        body = lines[i].strip()
        if not (body.startswith("- ") or body == "-"):
            break
        if ind > indent:
            raise YamlError(f"line {i + 1}: unexpected indent in sequence")
        raw = lines[i]
        dash = raw.index("-", indent)
        after = raw[dash + 1:]
        content = after.strip()
        if content == "" or content.startswith("#"):
            j = _skip(lines, i + 1)
            if j < len(lines) and _indent(lines[j]) > indent:
                v, i = _parse_block(lines, j, _indent(lines[j]))
            else:
                v, i = None, i + 1
            out.append(v)
            continue
        if _KEY.match(content):
            # `- key: value` plus any continuation lines aligned under `key`.
            col = dash + 1 + (len(after) - len(after.lstrip(" ")))
            lines[i] = " " * col + raw[col:]
            v, i = _parse_map(lines, i, col)
            out.append(v)
            continue
        out.append(_scalar(content, i + 1))
        i += 1
    return out, i


def _parse_block(lines, i, indent):
    i = _skip(lines, i)
    if i >= len(lines):
        return None, i
    body = lines[i].strip()
    if body.startswith("- ") or body == "-":
        return _parse_seq(lines, i, indent)
    if not _KEY.match(body):
        # `key:` followed by a bare scalar on its own indented line, which is how a
        # human writes an empty list: `contracts_added:\n  []`
        return _scalar(body, i + 1), i + 1
    return _parse_map(lines, i, indent)


def load(text):
    """Parse a YAML-subset document into python data. Returns {} for an empty doc."""
    lines = text.replace("\t", "    ").split("\n")
    val, _ = _parse_block(lines, 0, 0)
    return {} if val is None else val


_FM = re.compile(r'\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)', re.S)


def frontmatter(text):
    """Split `---\\n<yaml>\\n---\\n<body>`. Returns (dict, body)."""
    m = _FM.match(text)
    if not m:
        raise YamlError("no YAML frontmatter block: file must start with `---`")
    return load(m.group(1)), text[m.end():]

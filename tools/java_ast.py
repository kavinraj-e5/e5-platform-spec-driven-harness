"""Java method index and DECLARED-TYPE call resolution over source, with tree-sitter.

Promoted 2026-11-14 from the four-approach trial (scratchpad trial; memory
`ast-vs-lsp-trial-result`): on workflow-manager this resolution reached exactly the same
outbound sites as method-level bytecode and as jdtls. Used by tools/gql_tasks.py.

Resolution is what a good syntactic extractor can do without a type checker:
  * receiver is a local, parameter or field  -> its DECLARED type, looked up in the same file
  * receiver is a capitalised name          -> a static call on that class
  * no receiver / this                      -> own class and its supertypes
  * declared type is an interface/abstract  -> plus every in-repo subtype declaring the name
  * anything else (a chain, a.b.c())        -> fall back to EVERY repo method with that name
Callers must not use the `name-only` result: it is a guess (see resolve_call)."""
import re
from pathlib import Path
import tree_sitter_java as tsj
from tree_sitter import Language, Parser

JAVA = Language(tsj.language())
P = Parser(JAVA)


def txt(n, src):
    return src[n.start_byte:n.end_byte].decode()


class JavaAST:
    def __init__(self, src_root: Path, files=None):
        """`files`: parse only these (tools/call_graph.py passes main sources without the
        generated protobuf code); default every .java under src_root."""
        self.root = src_root
        self.methods = {}   # id -> {cls, name, file, start, end, argc, calls:[...], nested:[...]}
        self.classes = {}   # fqn -> {file, extends:[simple], implements:[simple], fields:{name:type}, kind, imports, pkg}
        self.by_simple = {}
        for f in sorted(files if files is not None else src_root.rglob("*.java")):
            self._file(f)
        for fqn in self.classes:
            self.by_simple.setdefault(fqn.split(".")[-1].split("$")[-1], []).append(fqn)
        self.subs = {}
        for fqn, c in self.classes.items():
            for s in c["extends"] + c["implements"]:
                for t in self.type_fqn(s, fqn):
                    self.subs.setdefault(t, set()).add(fqn)
        self.by_name = {}
        for mid, m in self.methods.items():
            self.by_name.setdefault(m["name"], []).append(mid)

    # ---------------------------------------------------------------- parse
    def _file(self, f: Path):
        src = f.read_bytes()
        tree = P.parse(src)
        pkg, imports = "", {}
        for ch in tree.root_node.children:
            if ch.type == "package_declaration":
                pkg = txt(ch, src).replace("package", "").strip(" ;")
            elif ch.type == "import_declaration":
                s = txt(ch, src).replace("import", "").replace("static", "").strip(" ;")
                imports[s.split(".")[-1]] = s
        self._walk_types(tree.root_node, src, f, pkg, imports, outer=None)

    def _walk_types(self, node, src, f, pkg, imports, outer):
        for ch in node.children:
            if ch.type in ("class_declaration", "interface_declaration", "enum_declaration",
                           "record_declaration"):
                name = txt(ch.child_by_field_name("name"), src)
                fqn = f"{outer}${name}" if outer else (f"{pkg}.{name}" if pkg else name)
                ext, imp = [], []
                sc = ch.child_by_field_name("superclass")
                if sc is not None:
                    ext = [re.sub(r"<.*", "", txt(sc, src).replace("extends", "").strip())]
                for k in ch.children:
                    if k.type in ("super_interfaces", "extends_interfaces"):
                        body = txt(k, src).replace("implements", "").replace("extends", "")
                        imp += [re.sub(r"<.*", "", x).strip() for x in re.sub(r"<[^<>]*>", "", body).split(",") if x.strip()]
                self.classes[fqn] = {"file": str(f), "extends": ext, "implements": imp, "fields": {},
                                     "kind": ch.type.split("_")[0], "imports": imports, "pkg": pkg,
                                     "consts": {}}
                body = ch.child_by_field_name("body")
                if body is not None:
                    self._walk_body(body, src, f, pkg, imports, fqn)

    def _walk_body(self, body, src, f, pkg, imports, fqn):
        for m in body.children:
            if m.type == "field_declaration":
                ty = txt(m.child_by_field_name("type"), src)
                for d in m.children:
                    if d.type == "variable_declarator":
                        nm = txt(d.child_by_field_name("name"), src)
                        self.classes[fqn]["fields"][nm] = re.sub(r"<.*", "", ty)
                        v = d.child_by_field_name("value")
                        if v is not None and v.type == "string_literal":
                            self.classes[fqn]["consts"][nm] = txt(v, src).strip('"')
            elif m.type in ("method_declaration", "constructor_declaration"):
                self._method(m, src, f, fqn)
            elif m.type in ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration"):
                self._walk_types(body, src, f, pkg, imports, fqn) if False else None
        # nested types
        self._walk_types(body, src, f, pkg, imports, fqn)
        for ch in body.children:
            if ch.type == "enum_body_declarations":
                self._walk_body(ch, src, f, pkg, imports, fqn)

    def _method(self, m, src, f, fqn, parent=None):
        name = txt(m.child_by_field_name("name"), src) if m.type == "method_declaration" else "<init>"
        params = m.child_by_field_name("parameters")
        locs = {}
        argc = 0
        if params is not None:
            for p in params.children:
                if p.type in ("formal_parameter", "spread_parameter"):
                    argc += 1
                    t, n = p.child_by_field_name("type"), p.child_by_field_name("name")
                    if t is not None and n is not None:
                        locs[txt(n, src)] = re.sub(r"<.*", "", txt(t, src))
        mid = f"{fqn}#{name}@{m.start_point[0] + 1}"
        nn = m.child_by_field_name("name")
        rec = {"cls": fqn, "name": name, "file": str(f), "start": m.start_point[0] + 1,
               "name_pos": (nn.start_point[0], nn.start_point[1]) if nn is not None else None,
               "has_body": m.child_by_field_name("body") is not None,
               "end": m.end_point[0] + 1, "argc": argc, "calls": [], "locals": locs, "node": None,
               "nested": []}
        self.methods[mid] = rec
        if parent:
            self.methods[parent]["nested"].append(mid)
        body = m.child_by_field_name("body")
        if body is not None:
            self._calls(body, src, f, fqn, mid, rec)

    def _calls(self, node, src, f, fqn, mid, rec):
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "local_variable_declaration":
                t = n.child_by_field_name("type")
                for d in n.children:
                    if d.type == "variable_declarator" and t is not None:
                        rec["locals"][txt(d.child_by_field_name("name"), src)] = re.sub(r"<.*", "", txt(t, src))
            if n.type == "lambda_expression":
                ps = n.child_by_field_name("parameters")
            if n.type == "method_invocation":
                nm = n.child_by_field_name("name")
                obj = n.child_by_field_name("object")
                args = n.child_by_field_name("arguments")
                argc = sum(1 for a in args.named_children) if args is not None else 0
                rec["calls"].append({"name": txt(nm, src), "recv": txt(obj, src) if obj is not None else None,
                                     "recv_type": obj.type if obj is not None else None, "argc": argc,
                                     "line": nm.start_point[0], "col": nm.start_point[1]})
            if n.type == "object_creation_expression":
                cb = [c for c in n.children if c.type == "class_body"]
                if cb:     # anonymous class: its methods are separate, linked from here
                    for mm in cb[0].children:
                        if mm.type == "method_declaration":
                            self._method(mm, src, f, fqn, parent=mid)
                    continue
            stack.extend(reversed(n.children))

    # ---------------------------------------------------------------- resolution
    def type_fqn(self, simple, ctx_fqn):
        simple = simple.split("<")[0].strip()
        if "." in simple:
            if simple in self.classes:
                return [simple]
            simple = simple.split(".")[-1]
        c = self.classes.get(ctx_fqn, {})
        imp = c.get("imports", {}).get(simple)
        if imp and imp in self.classes:
            return [imp]
        # nested in the context class or its outers
        parts = ctx_fqn.split("$")
        for i in range(len(parts), 0, -1):
            cand = "$".join(parts[:i]) + "$" + simple
            if cand in self.classes:
                return [cand]
        pkg = c.get("pkg", "")
        if f"{pkg}.{simple}" in self.classes:
            return [f"{pkg}.{simple}"]
        if imp:
            return []           # imported from outside the repo
        return [x for x in self.classes if x.split(".")[-1].split("$")[-1] == simple][:1]

    def supers(self, fqn):
        out, stack = [], [fqn]
        while stack:
            x = stack.pop(0)
            if x in out or x not in self.classes:
                continue
            out.append(x)
            c = self.classes[x]
            for s in c["extends"] + c["implements"]:
                stack += self.type_fqn(s, x)
        return out

    def all_subs(self, fqn):
        out, stack = set(), [fqn]
        while stack:
            for s in self.subs.get(stack.pop(), ()):
                if s not in out:
                    out.add(s); stack.append(s)
        return out

    def methods_in(self, fqn, name, argc):
        return [mid for mid in self.by_name.get(name, ())
                if self.methods[mid]["cls"] == fqn and self.methods[mid]["argc"] == argc]

    def on_type(self, fqn, name, argc):
        for c in self.supers(fqn):
            hit = self.methods_in(c, name, argc)
            if hit:
                out = list(hit)
                break
        else:
            out = []
        for s in self.all_subs(fqn):
            out += self.methods_in(s, name, argc)
        return out

    def resolve_call(self, mid, call):
        """-> (targets, how)"""
        m = self.methods[mid]
        cls, recv, name, argc = m["cls"], call["recv"], call["name"], call["argc"]
        if recv is None or recv == "this":
            return self.on_type(cls, name, argc), "own-class"
        if recv == "super":
            sup = self.classes[cls]["extends"]
            t = self.type_fqn(sup[0], cls) if sup else []
            return (self.on_type(t[0], name, argc) if t else []), "super"
        r = recv[5:] if recv.startswith("this.") else recv
        if re.fullmatch(r"\w+", r):
            ty = m["locals"].get(r)
            if ty is None:
                for c in self.supers(cls) + [cls.split("$")[0]]:
                    if r in self.classes.get(c, {}).get("fields", {}):
                        ty = self.classes[c]["fields"][r]; break
            if ty is not None:
                t = self.type_fqn(ty, cls)
                return (self.on_type(t[0], name, argc) if t else []), "declared-type" if t else "external-type"
            if r[:1].isupper():
                t = self.type_fqn(r, cls)
                return (self.on_type(t[0], name, argc) if t else []), "static" if t else "external-static"
        # unknown receiver: every method of that name and arity in the repo
        return [x for x in self.by_name.get(name, ()) if self.methods[x]["argc"] == argc], "name-only"

    def method_at(self, file, line1):
        """Innermost method whose range contains a 1-based line."""
        best = None
        for mid, m in self.methods.items():
            if m["file"] == file and m["start"] <= line1 <= m["end"]:
                if best is None or m["start"] >= self.methods[best]["start"]:
                    best = mid
        return best

#!/usr/bin/env python3
"""Extract contracts from published JFrog artifacts — the ones with no source anywhere.

  python3 tools/artifacts.py list                    what the catalog pins
  python3 tools/artifacts.py probe app-sdk           versions available on JFrog
  python3 tools/artifacts.py extract --all           introspect and emit facts
  python3 tools/artifacts.py extract app-sdk

WHY THIS EXISTS
---------------
The platform's most-shared contracts are not in any repository. 10 of 22 repos pin a task
SDK; `AppSDK` defines the function blocks a workflow actually calls, and a workflow imports
`e5.appsdk.functionblock.generated.*` — so a source-only index cannot see what an automation
does. `StateService` holds the AKST dequeue ordering, the platform's hottest query.

NEVER EXECUTES THE ARTIFACT
---------------------------
A jar is read as a zip of class files and introspected with `javap`, which decompiles
signatures without running anything. A wheel is read with `ast.parse`, which also does not
execute. This matters: these artifacts are built to run inside the platform, and an indexer
is not a sandbox.

WHAT IT CANNOT SEE
------------------
Method bodies. `javap` without `-c` gives signatures, not logic — so "why does the dequeue
order this way" stays unanswerable from here and the facts say so via
`provenance: sdk-artifact` plus `limits` on each. That limit is recorded rather than hidden,
because a reader who assumes otherwise will trust an answer the data cannot support.
"""
from __future__ import annotations
import argparse, ast, base64, json, os, re, subprocess, sys, tempfile, urllib.error
import urllib.request, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import credentials as CRED
import catalog as C

KB = HERE.parent
GRAPH = KB / "graph"
ARTIFACT_FACTS = GRAPH / "artifact-facts.jsonl"
CACHE = Path(os.environ.get("E5_ARTIFACT_CACHE", Path.home() / ".e5" / "artifacts"))
BASE = "https://element5.jfrog.io/artifactory"
# Gradle coordinates live here; the pypi repos are separate and named per package.
MAVEN_REPOS = ["automation-fabric-libs", "automation-fabric-libs-dev"]


def _auth_header() -> dict:
    c = CRED.jfrog_creds()
    if not c:
        raise RuntimeError(
            "no JFrog credentials. Set E5_JFROG_USER / E5_JFROG_TOKEN, or add a `jfrog` "
            "stanza to the nominated credentials file.")
    u, p = c
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Strip Authorization when a redirect crosses hosts.

    JFrog serves small artifacts inline but redirects larger ones to a presigned S3 URL.
    urllib re-sends the Basic auth header to S3, which rejects it with
    `InvalidArgument: Only one auth mechanism allowed`. AppSDK (30KB) downloaded fine and
    the task SDK did not, which made this look like a path or permission problem for a
    while. The presigned URL carries its own credentials; ours must not tag along.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        import urllib.parse as _up
        if _up.urlparse(newurl).netloc != _up.urlparse(req.full_url).netloc:
            for h in ("Authorization", "authorization"):
                new.headers.pop(h, None)
                new.unredirected_hdrs.pop(h, None)
        return new


_OPENER = urllib.request.build_opener(_DropAuthOnRedirect)


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=_auth_header())
    with _OPENER.open(req, timeout=timeout) as r:
        return r.read()


# --------------------------------------------------------------------------- discovery
def search_gavc(group: str, artifact: str) -> list[dict]:
    """Which versions exist. Returns [{version, uri}] sorted newest-last."""
    q = f"{BASE}/api/search/gavc?g={group}&a={artifact}"
    try:
        body = json.loads(_get(q).decode())
    except Exception as e:
        raise RuntimeError(CRED.mask(str(e))) from None
    out = {}
    for r in body.get("results", []):
        uri = r.get("uri", "")
        m = re.search(rf"/{re.escape(artifact)}/([^/]+)/", uri)
        if not m:
            continue
        v = m.group(1)
        # prefer the .jar over .module/.pom for the same version
        # Prefer the plain `.jar`. `-all.jar` is the shaded fat jar: for
        # e5-task-sdk-java:0.7.6 that is 30,814 classes, almost all bundled Netty,
        # protobuf and Jackson. The contract is in the handful of com.e5 classes.
        prev = out.get(v, "")
        if uri.endswith(".jar") and not uri.endswith("-all.jar"):
            out[v] = uri
        elif v not in out or (prev.endswith((".module", ".pom")) and uri.endswith(".jar")):
            out[v] = uri
    def vkey(v):
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[._-]", v)[:4])
    return [{"version": v, "uri": out[v]} for v in sorted(out, key=vkey)]


def _coord_parts(artifact: str) -> tuple[str | None, str]:
    """`e5.functionblock:AppSDK` -> ('e5.functionblock', 'AppSDK'); bare name -> (None, name)."""
    if ":" in artifact:
        g, a = artifact.split(":", 1)
        return g, a
    return None, artifact


def fetch(artifact: str, version: str) -> Path:
    """Download to the local cache and return the path. Cached by coordinate+version."""
    group, name = _coord_parts(artifact)
    CACHE.mkdir(parents=True, exist_ok=True)
    if group:                                       # maven / gradle
        local = CACHE / f"{name}-{version}.jar"
        if local.is_file():
            return local
        # Resolve via the gavc search rather than constructing a path. Constructing it
        # looked right and 404'd on versions that demonstrably exist: this estate has
        # versions published as .module/.pom with no plain .jar, and others with a
        # classifier in the filename. The search index knows which files are actually
        # there; a guessed URL does not.
        hits = [h for h in search_gavc(group, name) if h["version"] == version]
        uri = hits[0]["uri"] if hits else None
        if uri:
            dl = uri.replace("/api/storage/", "/")
            if dl.endswith(".jar"):
                try:
                    local.write_bytes(_get(dl))
                    return local
                except Exception:
                    pass
        # fall back to the conventional layout, and try a sources jar too
        gpath = group.replace(".", "/")
        last = None
        for repo in MAVEN_REPOS:
            for suffix in (".jar", "-sources.jar"):
                url = f"{BASE}/{repo}/{gpath}/{name}/{version}/{name}-{version}{suffix}"
                try:
                    local.write_bytes(_get(url))
                    return local
                except Exception as e:
                    last = e
        raise RuntimeError(
            f"{artifact}:{version} exists in the search index but no downloadable .jar "
            f"was found (last: {CRED.mask(str(last))[:90]}). It may be published as "
            f".module/.pom only.")

    # Python wheel. Resolved from the repo's STORAGE listing rather than the simple
    # index: the pinned version is often not the published one. `e5_task_sdk_python`
    # pins 1.0.9 in pyproject.toml and what exists on JFrog is 1.0.9.post7 - a post
    # release the pin silently resolves to. Matching on prefix surfaces that rather
    # than 404-ing.
    local = CACHE / f"{name}-{version}.whl"
    if local.is_file():
        return local
    stem = name.replace("_", "-")
    repos = [f"{stem}-local", f"{stem}-pypi-local", f"{stem}-local-prod"]
    last = None
    for repo in repos:
        try:
            listing = json.loads(_get(
                f"{BASE}/api/storage/{repo}?list&deep=1&listFolders=0", timeout=60).decode())
        except Exception as e:
            last = e
            continue
        whls = [f["uri"] for f in listing.get("files", []) if f["uri"].endswith(".whl")]
        exact = [w for w in whls if f"-{version}-" in w]
        prefixed = [w for w in whls if f"-{version}." in w]
        pick = (exact or sorted(prefixed))[-1:] if (exact or prefixed) else []
        if not pick:
            last = RuntimeError(f"no wheel matching {version} among {len(whls)} in {repo}")
            continue
        resolved = re.search(rf"{re.escape(stem.replace('-', '_'))}-([^-]+)-", pick[0])
        if resolved and resolved.group(1) != version:
            print(f"  note: {name} pins {version}; JFrog publishes "
                  f"{resolved.group(1)} - using that", file=sys.stderr)
        try:
            local.write_bytes(_get(f"{BASE}/{repo}{pick[0]}", timeout=120))
            return local
        except Exception as e:
            last = e
    raise RuntimeError(f"{name}:{version} not found in pypi repos {repos}: "
                       f"{CRED.mask(str(last))[:110]}")


# --------------------------------------------------------------------------- fact shape
def _fact(kind: str, name: str, component: str, artifact: str, version: str,
          **props) -> dict:
    return {
        "id": f"{kind}:{artifact}:{version}:{name}",
        "contract_key": f"{kind}:{name}",
        "kind": kind, "name": name,
        "repo": artifact, "component": component, "system": "build-input",
        "path": f"{artifact}:{version}", "line": 0,
        "provenance": "sdk-artifact", "confidence": "EXTRACTED",
        "commit": version, "branch": f"artifact:{version}",
        "props": {"artifact": artifact, "version": version,
                  "limits": "signatures only - javap/ast give no method bodies",
                  **props},
    }


# --------------------------------------------------------------------------- java jars
_PUBLIC_TYPE = re.compile(
    r"^public\s+(?:final\s+|abstract\s+|static\s+)*"
    r"(class|interface|enum|record)\s+([\w.$]+)", re.M)
# Enum constant casing is inconsistent across this SDK and each narrowing of this regex
# silently emptied a different enum:
#   Command       TaskCreate, TaskComplete      (CamelCase)
#   EndpointType  kafka, akst, http             (lowercase)
#   TaskProtocol  v1, v2                        (lowercase+digit)
#   TaskName      RPA_GATEWAY                   (SCREAMING_CASE)
# So: match any identifier, and let the `static final <FQN>` shape do the discriminating.
_ENUM_CONST = re.compile(r"^\s+public static final \S+ (\w+);", re.M)
_METHOD = re.compile(r"^\s+public\s+(?:static\s+|final\s+|abstract\s+)*"
                     r"[\w.<>\[\],$? ]+\s+(\w+)\(", re.M)


def _own_prefixes(artifact: str) -> tuple:
    """Zip-entry prefixes belonging to the artifact itself, not to what it bundles."""
    group, name = _coord_parts(artifact)
    pfx = {"com/e5/", "e5/"}
    if group:
        pfx.add(group.replace(".", "/") + "/")
        # `e5.task.sdk.java` publishes under `com.e5.platform.sdk`, so the group path
        # alone is not enough; the e5 roots above cover it.
    return tuple(sorted(pfx))


def introspect_jar(jar: Path, artifact: str, version: str, component: str) -> list[dict]:
    """Public types, enum constants, public method names, and bundled JSON schemas.

    The schemas matter as much as the classes here. AppSDK-1.10.3 is a 30KB jar with 22
    classes and 13 `*.schema.json` files - and it is the SCHEMAS that define a function
    block's input and output payloads, which is the thing a workflow author actually needs.
    A class-only introspection would have called this SDK almost empty.
    """
    out = []
    # bundled JSON schemas - the function-block payload contracts
    with zipfile.ZipFile(jar) as z:
        for n in z.namelist():
            if not n.endswith(".schema.json"):
                continue
            stem = Path(n).name.removesuffix(".schema.json")
            try:
                doc = json.loads(z.read(n).decode("utf-8", "replace"))
            except Exception:
                doc = {}
            props = sorted((doc.get("properties") or {}).keys())
            out.append(_fact("SdkSchema", stem, component, artifact, version,
                             schema_path=n, fields=props[:30],
                             field_count=len(props),
                             required=sorted(doc.get("required") or [])[:20]))
    # Own-package filter. Even a plain jar can vendor; a fat jar certainly does. The
    # contract is what the artifact PUBLISHES, not what it bundles - introspecting
    # bundled Netty would be slow and would say nothing about this platform.
    own = _own_prefixes(artifact)
    with zipfile.ZipFile(jar) as z:
        classes = [n for n in z.namelist()
                   if n.endswith(".class") and "$" not in Path(n).name
                   and any(n.startswith(pfx) for pfx in own)]
    # Package inventory first - cheap, and already answers "what is in here"
    pkgs = {}
    for c in classes:
        pkg = c.rsplit("/", 1)[0].replace("/", ".") if "/" in c else "(default)"
        pkgs[pkg] = pkgs.get(pkg, 0) + 1
    for pkg, n in sorted(pkgs.items()):
        out.append(_fact("SdkPackage", pkg, component, artifact, version, classes=n))

    # Signature detail via javap. Batched: one JVM start for many classes is the
    # difference between seconds and minutes on a jar this size.
    fqns = [c[:-6].replace("/", ".") for c in classes]
    BATCH = 60
    for i in range(0, len(fqns), BATCH):
        chunk = fqns[i:i + BATCH]
        r = subprocess.run(["javap", "-public", "-classpath", str(jar), *chunk],
                           capture_output=True, text=True, timeout=180)
        if r.returncode and not r.stdout:
            continue
        for block in r.stdout.split("\n}"):
            m = _PUBLIC_TYPE.search(block)
            if not m:
                continue
            kindword, fqn = m.group(1), m.group(2)
            short = fqn.rsplit(".", 1)[-1]
            # javap renders an enum as `public final class X extends java.lang.Enum<X>`,
            # never with the `enum` keyword - so keying on the keyword silently missed
            # every enum in the task SDK, including Command, TaskName, EndpointType and
            # WorkerType, which are the wire protocol's vocabulary.
            if kindword == "class" and "extends java.lang.Enum" in block:
                kindword = "enum"
            if kindword == "enum":
                consts = _ENUM_CONST.findall(block)
                out.append(_fact("SdkEnum", fqn, component, artifact, version,
                                 values=consts[:40], value_count=len(consts)))
                for cst in consts:
                    out.append(_fact("SdkEnumValue", f"{short}.{cst}", component,
                                     artifact, version, enum=fqn))
            else:
                methods = sorted(set(_METHOD.findall(block)))
                out.append(_fact("SdkType", fqn, component, artifact, version,
                                 type_kind=kindword, methods=methods[:30],
                                 method_count=len(methods)))
    return out


# --------------------------------------------------------------------------- py wheels
def introspect_wheel(whl: Path, artifact: str, version: str, component: str) -> list[dict]:
    """Module/class/function surface via ast.parse. Never imports the package."""
    out = []
    with zipfile.ZipFile(whl) as z:
        pys = [n for n in z.namelist() if n.endswith(".py")]
        for n in pys:
            mod = n[:-3].replace("/", ".").removesuffix(".__init__")
            try:
                tree = ast.parse(z.read(n).decode("utf-8", "replace"))
            except Exception:
                continue
            out.append(_fact("SdkPackage", mod, component, artifact, version))
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                    meths = sorted({b.name for b in node.body
                                    if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                                    and not b.name.startswith("_")})
                    out.append(_fact("SdkType", f"{mod}.{node.name}", component,
                                     artifact, version, type_kind="class",
                                     methods=meths[:30], method_count=len(meths)))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and not node.name.startswith("_"):
                    out.append(_fact("SdkFunction", f"{mod}.{node.name}", component,
                                     artifact, version))
    return out


# --------------------------------------------------------------------------- catalog glue
def pinned() -> list[dict]:
    """Components in the catalog that name a published artifact."""
    out = []
    for name, c in sorted(C.load()["Component"].items()):
        if c.get("artifact"):
            out.append({"component": name, "artifact": c["artifact"],
                        "version": str(c.get("pinned_version") or ""),
                        "pinned_by": c.get("pinned_by", "")})
    return out


def extract(which: list[str] | None = None) -> dict:
    rows, facts, errs = pinned(), [], []
    for row in rows:
        if which and row["component"] not in which:
            continue
        art, ver, comp = row["artifact"], row["version"], row["component"]
        if not ver:
            errs.append(f"{comp}: no pinned_version in the catalog")
            continue
        try:
            path = fetch(art, ver)
        except Exception as e:
            errs.append(f"{comp} ({art}:{ver}): {CRED.mask(str(e))[:170]}")
            continue
        try:
            got = (introspect_jar(path, art, ver, comp) if path.suffix == ".jar"
                   else introspect_wheel(path, art, ver, comp))
        except Exception as e:
            errs.append(f"{comp}: introspection failed: {type(e).__name__}: {e}")
            continue
        facts.extend(got)
    return {"facts": facts, "errors": errs, "attempted": rows}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("probe"); p.add_argument("component")
    e = sub.add_parser("extract")
    e.add_argument("component", nargs="*")
    e.add_argument("--all", action="store_true")
    a = ap.parse_args()

    if a.cmd == "list":
        print(f"{'component':<18} {'artifact':<42} {'pinned':<10} pinned by")
        print("-" * 96)
        for r in pinned():
            print(f"{r['component']:<18} {r['artifact']:<42} {r['version']:<10} "
                  f"{r['pinned_by']}")
        print(f"\n{len(pinned())} artifact Components. "
              f"JFrog creds: {CRED.jfrog_available()['present']}")
        return 0

    if a.cmd == "probe":
        rows = {r["component"]: r for r in pinned()}
        r = rows.get(a.component)
        if not r:
            print(f"no artifact Component {a.component!r}; see `list`")
            return 2
        g, n = _coord_parts(r["artifact"])
        if not g:
            print(f"{r['artifact']} is a Python package - gavc search is maven-only")
            return 0
        try:
            vs = search_gavc(g, n)
        except Exception as ex:
            print(f"search failed: {ex}")
            return 1
        print(f"{r['artifact']} - {len(vs)} versions on JFrog")
        for v in vs[-12:]:
            mark = "  <- pinned" if v["version"] == r["version"] else ""
            print(f"  {v['version']}{mark}")
        if r["version"] not in {v["version"] for v in vs}:
            print(f"\n  ! the catalog pins {r['version']}, which JFrog does not list")
        return 0

    which = a.component or None
    if a.all:
        which = None
    out = extract(which)
    if out["facts"]:
        GRAPH.mkdir(parents=True, exist_ok=True)
        out["facts"].sort(key=lambda f: (f["repo"], f["kind"], f["name"]))
        with ARTIFACT_FACTS.open("w") as fh:
            for f in out["facts"]:
                fh.write(json.dumps(f, sort_keys=True) + "\n")
    import collections
    kinds = collections.Counter(f["kind"] for f in out["facts"])
    byart = collections.Counter(f["repo"] for f in out["facts"])
    print(f"\n{len(out['facts'])} facts from {len(byart)} artifacts")
    for art, n in byart.most_common():
        print(f"  {art:<44} {n}")
    print(f"\n  by kind: {dict(kinds)}")
    if out["errors"]:
        print(f"\n{len(out['errors'])} could not be read:")
        for e in out["errors"]:
            print(f"  {e}")
    if out["facts"]:
        print(f"\n-> {ARTIFACT_FACTS.relative_to(KB)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

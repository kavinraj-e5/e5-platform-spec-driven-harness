#!/usr/bin/env python3
"""Newest published version of every internal E5 library, so consumers that are BEHIND can
be listed. A library change is only done when every consumer has bumped its pin.

    python3 tools/lib_versions.py extract     query JFrog -> graph/library-versions.json
    python3 tools/lib_versions.py report      who is behind, per library

Sources, in order of authority:
  1. JFrog: the versions actually published (maven: gavc search; python: the pypi repo listing)
  2. the library repo's own build file at its latest release (`Publishes` in build-facts)
A version is a RELEASE only if it is purely numeric (1.10.0). Snapshots, rc and dev builds
are recorded but never count as "latest", because nobody should be told to pin one.

"Behind" is measured against the newest release IN THE CONSUMER'S OWN MAJOR LINE. Measured
2026-11-14: JFrog's last-deployed release of task-sdk-java is 111.5.0 and of AppSDK
100.1.2.2.4, while every consumer pins 0.x / 1.x. Whether those are real releases or stray
uploads the data cannot say, so they are reported as `outside_every_consumer_line` for the
owner to confirm, and never used as the upgrade target.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
KB = HERE.parent
BUILD = KB / "graph" / "build-facts.jsonl"
CONSUMERS = KB / "graph" / "consumer-build-facts.jsonl"
OUT = KB / "graph" / "library-versions.json"
_REL = re.compile(r"^\d+(?:\.\d+)*$")


def vkey(v: str) -> tuple:
    return tuple(int(x) for x in v.split(".")) if v and _REL.match(v) else ()


def _read(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.is_file() else []


def norm(a: str | None) -> str:
    """Python package names mix `-` and `_`; Maven ids do not. Compare normalised."""
    return (a or "").lower().replace("-", "_")


def _platform_libs() -> set:
    """Normalised names of libraries the PLATFORM publishes (its repos, or catalog artifacts)."""
    import catalog as C
    names = {norm(f.get("artifact")) for f in _read(BUILD) if f["kind"] == "Publishes"}
    names |= {norm((c.get("artifact") or "").split(":")[-1]) for c in C.load()["Component"].values()
              if c.get("artifact")}
    return names - {""}


def _facts() -> list[dict]:
    """Platform build facts, plus consumer facts that depend on a PLATFORM library."""
    plat = _platform_libs()
    return _read(BUILD) + [f for f in _read(CONSUMERS)
                           if f["kind"] == "DependsOnArtifact" and norm(f.get("artifact")) in plat]


def libraries() -> dict:
    """artifact -> {group, kind (maven|python), published_by_repo, repo_version}"""
    out = {}
    for f in _facts():
        if f["kind"] not in ("DependsOnArtifact", "Publishes") or not f.get("artifact"):
            continue
        key = f["artifact"] if f.get("group") not in ("pypi", None) else norm(f["artifact"])
        a = out.setdefault(key, {"group": f.get("group"),
                                           "ecosystem": "python" if f.get("group") == "pypi"
                                           or f.get("configuration") == "poetry" else "java"})
        if f["kind"] == "Publishes":
            a["published_by_repo"] = f["repo"]
            a["repo_version"] = f.get("version")
        elif not a.get("group") and f.get("group"):
            a["group"] = f["group"]
    return out


def _jfrog_versions(lib: str, info: dict) -> tuple[list[str], str | None]:
    import artifacts as A
    try:
        if info["ecosystem"] == "java" and info.get("group"):
            return [h["version"] for h in A.search_gavc(info["group"], lib)], None
        # python: list the pypi repo and read versions out of the wheel/sdist file names
        stem = lib.replace("-", "_")
        found = set()
        dash = lib.replace("_", "-")          # the repo names artifacts.py resolves against
        for repo in (f"{dash}-local", f"{dash}-pypi-local", f"{dash}-local-prod"):
            try:
                d = json.loads(A._get(f"{A.BASE}/api/storage/{repo}?list&deep=1&listFolders=0").decode())
            except Exception:
                continue
            for f in d.get("files", []):
                m = re.search(rf"{re.escape(stem)}-(\d+(?:\.\d+)*)(?:\.post\d+)?[-.]", f.get("uri", ""))
                if m:
                    found.add(m.group(1))
        return sorted(found, key=vkey), None if found else "no python package listing found"
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:120]}"


def extract() -> dict:
    libs = libraries()
    res = {}
    for lib, info in sorted(libs.items()):
        versions, err = _jfrog_versions(lib, info)
        rel = sorted({v for v in versions if _REL.match(v)}, key=vkey)
        latest = rel[-1] if rel else None
        by_major = {}
        for v in rel:
            by_major[v.split(".")[0]] = v              # rel is ascending, so the last wins
        pins = {f.get("version") for f in _facts()
                if f["kind"] == "DependsOnArtifact" and norm(f.get("artifact")) == norm(lib) and f.get("version")}
        lines = sorted({p.split(".")[0] for p in pins if _REL.match(p)})
        outside = latest if latest and lines and latest.split(".")[0] not in lines else None
        repo_v = info.get("repo_version")
        if repo_v and _REL.match(repo_v) and (not latest or vkey(repo_v) > vkey(latest)):
            # the repo's latest release declares a version JFrog does not have yet
            note = f"release branch declares {repo_v}, not yet on JFrog"
        else:
            note = None
        res[lib] = {"group": info.get("group"), "ecosystem": info["ecosystem"],
                    "published_by_repo": info.get("published_by_repo"),
                    "latest_published": latest, "latest_by_major": by_major,
                    "consumer_major_lines": lines,
                    "outside_every_consumer_line": outside,
                    "repo_release_version": repo_v,
                    "versions_seen": len(versions), "error": err, "note": note}
    return res


def behind(versions: dict | None = None) -> list[dict]:
    """Every consumer whose pin is older than the newest published release."""
    v = versions if versions is not None else (json.loads(OUT.read_text()) if OUT.is_file() else {})
    rows = []
    for f in _facts():
        if f["kind"] != "DependsOnArtifact":
            continue
        lib = v.get(f.get("artifact")) or v.get(norm(f.get("artifact"))) or {}
        pin = f.get("version")
        latest = (lib.get("latest_by_major") or {}).get(pin.split(".")[0]) if pin else None
        if latest and pin and _REL.match(pin) and vkey(pin) < vkey(latest):
            rows.append({"repo": f["repo"], "outside_platform": bool(f.get("outside_platform")),
                         "artifact": f["artifact"], "pinned": pin,
                         "latest": latest, "path": f.get("path"), "transitive": bool(f.get("transitive"))})
    return sorted(rows, key=lambda r: (r["artifact"], r["repo"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["extract", "report"])
    a = ap.parse_args()
    if a.cmd == "extract":
        res = extract()
        OUT.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
        for lib, r in res.items():
            line = ", ".join(f"{m}.x->{r['latest_by_major'][m]}" for m in r["consumer_major_lines"]
                             if m in r["latest_by_major"])
            print(f"  {lib:34} in-line {line or '-':22} last-deployed {r['latest_published'] or '-':12}"
                  + (" (OUTSIDE every consumer line - confirm)" if r["outside_every_consumer_line"] else "")
                  + (f"  ERROR {r['error']}" if r["error"] else "") + (f"  NOTE {r['note']}" if r["note"] else ""))
        print(f"-> {OUT.relative_to(KB)}")
        return 0
    rows = behind()
    for r in rows:
        print(f"  {r['artifact']:30} {r['repo']:24} pins {r['pinned']:10} latest {r['latest']}")
    print(f"{len(rows)} consumer pin(s) behind the newest published release")
    return 0


if __name__ == "__main__":
    sys.exit(main())

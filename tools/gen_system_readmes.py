#!/usr/bin/env python3
"""Generate the navigation layer of knowledge/: system READMEs, area READMEs, dependencies.md.

    python3 tools/gen_system_readmes.py

The hierarchy is   00-overview.md -> <system>/README.md -> <system>/<area>/README.md -> <component>.md

Prose (why a system or area exists) is authored HERE, because nothing can infer it.
Everything listed under it is GENERATED:
  * the member tables, from catalog/
  * which documents exist for each component, from the files on disk
  * dependencies.md, from graph/build-facts.jsonl (extracted from build files)

A hand-kept list goes wrong the first time someone adds a component. Regenerate; do not edit.
"""
import collections, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import catalog as C

KB = HERE.parent
KN = KB / "knowledge"
BUILD_FACTS = KB / "graph" / "build-facts.jsonl"

# catalog System -> folder under knowledge/
DIRS = {
    "workflow-development":  "workflow-development",
    "app-development":       "app-development",
    "workflow-interactions": "subsystems",
    "reporting":             "reporting",
    "communication":         "communication",
    "platform-delivery":     "platform-delivery",
}

# Areas inside a system: folder -> (title, members, authored intro)
AREAS = {
    "workflow-development": {
        "workflow": ("Workflow (runtime roles)",
                     ["wf-engine", "wf-worker", "wf-aggregator", "wf-trigger", "rpa-gateway"],
                     "One variant image runs several roles, chosen at runtime by `WORKER_MODE`. "
                     "These roles have no image of their own. Their code is in `platform-core` "
                     "(see `../workflow-library/`). The in-namespace task queue `akst` is the same "
                     "kind of role, documented under `communication/`."),
        "workflow-library": ("Workflow library",
                             ["platform-core", "wf-scaffolding", "variant-scaffolding"],
                             "What a workflow developer builds with. `platform-core` is the SDK and "
                             "the code of every workflow role. `wf-scaffolding` is the seed project "
                             "for a new workflow. `variant-scaffolding` composes platform-core, "
                             "AppSDK and the workflow jar into the variant image. A change here is "
                             "a library release: it reaches production only when each variant "
                             "bumps its pin. Versions: `../../dependencies.md`."),
    },
    "app-development": {
        "app": ("App (runtime)", ["fb-worker", "rf-fb-base-image"],
                "A running app is `fb-worker`: `rf-fb-base-image` + `rf-fb-worker-core` + a "
                "customer app's Robot Framework source, one image per app. "
                "`fb-worker-app-contract.md` says exactly what an app must supply. The "
                "customer app itself is not a platform deliverable."),
        "app-library": ("App library", ["rf-fb-worker-core", "app-sdk", "non-rpa-worker-core"],
                        "`rf-fb-worker-core` is the Python library every app runs on: the poll "
                        "loop, the AKST pull, the `TaskComplete` reply and the Citrix helpers. "
                        "Its task contract comes from `task-sdk-python`. `app-sdk` (AppSDK, the "
                        "function blocks) is built from `app-lib-scaffolding`. `non-rpa-worker-core` "
                        "is the Java core for non-RPA app workers."),
        "ui-synthesizer": ("ui-synthesizer", ["ui-synthesizer"],
                           "Renders text to a bitmap reproducing the remote session's "
                           "rasterisation, so a worker can template-match controls in a Citrix "
                           "session. A runtime service plus a Python SDK."),
    },
}

INTRO = {
"workflow-development": """
Everything a **workflow developer** builds with, and the roles a workflow runs as.
Two areas:

- `workflow/`: the runtime roles of one variant image (`WORKER_MODE`)
- `workflow-library/`: platform-core, wf-scaffolding, variant-scaffolding
""",
"app-development": """
Everything an **app developer** builds with. An app is the set of function blocks that
drive a real EHR through a Citrix session. The platform owns the base image, the Python
library and the Citrix tooling. Screens, locators and business logic belong to the app
team and are not here. Three areas:

- `app/`: the running fb-worker and the app contract
- `app-library/`: rf-fb-worker-core
- `ui-synthesizer/`
""",
"workflow-interactions": """
The **subsystems** (catalog name: workflow-interactions) act on running workflows and apps.
Audience: support, and the Neos operators who reach it through `interface`.

**Orchestrators.** Only `workflow-manager`, `workflow-ops` and `monitoring-service` follow
the full pattern: poll a task -> handler by task name -> hand sub-tasks to other
subsystems -> consume results -> resume. Their task paths are in `../flows/`.

**Everything else:**
- `interface` is the front door.
- `deployment-mgmt` is the central store; it calls no one.
- `dynamic-scaling` decides scaling and sends `ENABLE_DEPLOYMENT` / `DISABLE_DEPLOYMENT`
  tasks to workflow-manager (through AKST), which has workflow-ops change the pods.
- `cds` distributes shared credentials and caps replicas through deployment-mgmt.
- `credential-monitor` monitors credential assignment; like monitoring-service (it is a
  fork) it sends `RestartWorkflow` to workflow-ops.
- `workflow-api` starts workflows from business details.

**`workflow-manager` and `workflow-ops` are different services.**
""",
"reporting": """
What happened, recorded and sent onward. Audience: the **hub / reporting team**.

There are two separate chains:
- **platform events:** event-store -> event-processor -> subscribers
- **workflow-api webhooks:** workflow-api -> webhook-service -> the customer

`event-processor` does **not** feed `webhook-service`. `audit-service` is the HIPAA
compliance log.
""",
"communication": """
The **task fabric**. Every system depends on it; none owns it. AKST is the priority
queue, in two packagings of one capability:
- `akst`: a `WORKER_MODE` role inside the variant image (code in platform-core)
- `task-manager`: its own image (e5-akst-service), the Global Router

`state-service` holds the dequeue ordering (source: `e5-state-service`). The task SDKs define
the task envelope, the **transport task protocol v2** that all task traffic follows: read
[transport-task-protocol-v2](../contracts/transport-task-protocol-v2.md) and
[task-completion-envelope](../contracts/task-completion-envelope.md). Every service pins
its own SDK version; see `../dependencies.md`.
""",
"platform-delivery": """
How every system is built, configured and shipped. This is support, not a product
system. Every workflow-plane pod receives its configuration as environment variables,
which `devops-pipeline` generates from `workflow-config`. The config layers are
env -> workflow -> variant -> tenant -> version.
""",
}


def _docs_for(name: str, root: Path) -> list[Path]:
    """Every document about a component: `<name>.md` and `<name>-*.md`, anywhere below root."""
    return sorted(p for p in root.rglob("*.md")
                  if p.name != "README.md" and (p.stem == name or p.stem.startswith(name + "-")))


def _table(cat, members, base: Path, sysdir: Path) -> str:
    rows = ["| component | kind | repo | purpose (catalog) | documents |", "|---|---|---|---|---|"]
    for n in members:
        c = cat["Component"].get(n) or {}
        repo = (c.get("source") or {}).get("repo") or "—"
        docs = _docs_for(n, sysdir)
        links = ", ".join(f"[{p.name}]({p.relative_to(base).as_posix()})" for p in docs) or "none yet"
        role = " ".join((c.get("role") or "").split())
        first = (role.split(". ")[0].rstrip(".") + ".") if role else "—"
        rows.append(f"| `{n}` | {c.get('kind')} | `{repo}` | {first.replace('|', '/')} | {links} |")
    return "\n".join(rows)


FOOT = ("\n> Tables generated by `tools/gen_system_readmes.py` from `catalog/` and the files on "
        "disk. Regenerate rather than editing them.\n")


def gen_dependencies() -> int:
    facts = [json.loads(l) for l in BUILD_FACTS.read_text().splitlines() if l.strip()]
    pub = {f["repo"]: (f.get("artifact"), f.get("version")) for f in facts if f["kind"] == "Publishes"}
    deps = collections.defaultdict(set)
    users = collections.defaultdict(set)
    for f in facts:
        if f["kind"] != "DependsOnArtifact":
            continue
        deps[f["repo"]].add((f.get("artifact"), f.get("version")))
        users[f.get("artifact")].add((f["repo"], f.get("version")))
    out = ["# Dependencies (generated)", "",
           "Extracted from build files (`graph/build-facts.jsonl`, `tools/build_graph.py`). "
           "Only internal E5 artifacts are listed. Repos with no extracted build facts are "
           "absent; absent does **not** mean no dependencies.", "",
           "## What each repo depends on", "",
           "| repo | publishes | depends on (artifact version) |", "|---|---|---|"]
    for repo in sorted(set(deps) | set(pub)):
        p = pub.get(repo)
        d = ", ".join(f"`{n}` {v}" for n, v in sorted(deps.get(repo, ()), key=str)) or "—"
        out.append(f"| `{repo}` | {f'`{p[0]}` {p[1]}' if p and p[0] else '—'} | {d} |")
    out += ["", "## Who depends on each internal artifact", "",
            "| artifact | used by (repo version) | versions in use |", "|---|---|---|"]
    for art in sorted(users, key=str):
        us = sorted(users[art])
        vs = sorted({v for _, v in us if v})
        out.append(f"| `{art}` | " + ", ".join(f"`{r}` {v}" for r, v in us)
                   + f" | {len(vs)}{' (skew)' if len(vs) > 1 else ''} |")
    import lib_versions as LV
    vers = json.loads(LV.OUT.read_text()) if LV.OUT.is_file() else {}
    rows = LV.behind(vers)
    plat = [r for r in rows if not r["outside_platform"]]
    cons = [r for r in rows if r["outside_platform"]]
    out += ["", "## Behind the newest release in their own major line", "",
            "From `graph/library-versions.json` (JFrog). A release outside every consumer's line "
            "is listed after this table for the owner to confirm, and is never the target.", "",
            f"**Platform repos ({len(plat)}):**", "", "| library | repo | pins | newest in line |",
            "|---|---|---|---|"]
    out += [f"| `{r['artifact']}` | `{r['repo']}` | {r['pinned']} | {r['latest']} |" for r in plat]
    out += ["", f"**Consumer repos outside the platform ({len(cons)})** - customer workflows and apps, "
            "from `graph/consumer-build-facts.jsonl`:", "",
            "| library | repo | pins | newest in line |", "|---|---|---|---|"]
    out += [f"| `{r['artifact']}` | `{r['repo']}` | {r['pinned']} | {r['latest']} |" for r in cons]
    odd = [(k, v["outside_every_consumer_line"]) for k, v in sorted(vers.items())
           if v.get("outside_every_consumer_line")]
    if odd:
        out += ["", "**Last deployed release outside every consumer's line (confirm whether real):** "
                + ", ".join(f"`{k}` {v}" for k, v in odd)]
    imgs = [json.loads(l) for l in (KB / "graph" / "consumer-build-facts.jsonl").read_text().splitlines()
            if l.strip()] if (KB / "graph" / "consumer-build-facts.jsonl").is_file() else []
    base = sorted({(f["repo"], f.get("tag")) for f in imgs if f["kind"] == "BuildsFromImage"
                   and f.get("image") == "e5-rf-fb-base"})
    if base:
        out += ["", "## Built FROM the fb-worker base image (`e5-rf-fb-base`)", "",
                "| app repo | tag |", "|---|---|"] + [f"| `{r}` | {tg} |" for r, tg in base]
    (KN / "dependencies.md").write_text("\n".join(out) + "\n")
    print(f"  dependencies.md  ({len(facts)} build facts)")
    return 0


def main() -> int:
    cat = C.load()
    for sysname, dirname in DIRS.items():
        sysdir = KN / dirname
        sysdir.mkdir(parents=True, exist_ok=True)
        comps = sorted(n for n, c in cat["Component"].items() if c.get("system") == sysname)
        areas = AREAS.get(sysname, {})
        in_area = {m for _, ms, _ in areas.values() for m in ms}
        sysdef = cat["System"][sysname]
        body = [f"# {dirname}", "",
                f"**Audience: {sysdef.get('audience')}** · {len(comps)} components",
                INTRO[sysname]]
        if areas:
            body += ["## Areas", "", "| area | members |", "|---|---|"]
            for folder, (title, members, _) in areas.items():
                body.append(f"| [{title}]({folder}/README.md) | "
                            + ", ".join(f"`{m}`" for m in members) + " |")
            for folder, (title, members, intro) in areas.items():
                ad = sysdir / folder
                ad.mkdir(exist_ok=True)
                (ad / "README.md").write_text(
                    f"# {title}\n\nPart of [{dirname}](../README.md).\n\n{intro}\n\n"
                    f"## Members\n\n{_table(cat, members, ad, sysdir)}\n{FOOT}")
                print(f"  {dirname}/{folder}/README.md")
        rest = [n for n in comps if n not in in_area]
        if rest:
            body += ["", "## Members", "", _table(cat, rest, sysdir, sysdir)]
        (sysdir / "README.md").write_text("\n".join(body) + "\n" + FOOT)
        print(f"  {dirname}/README.md  ({len(comps)} components)")
    return gen_dependencies()


if __name__ == "__main__":
    sys.exit(main())

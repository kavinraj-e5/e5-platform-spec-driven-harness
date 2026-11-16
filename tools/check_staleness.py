#!/usr/bin/env python3
"""Compare each indexed checkout against what is actually deployed.

A KB built from a stale checkout states yesterday's truth with today's confidence.
This is the guard: every fact inherits the staleness of the commit it came from.

Deployed image tags are read once (read-only) from the cluster and cached in
graph/deployed.json, so this runs offline afterwards.
"""
import json, re, subprocess, sys
from pathlib import Path

KB = Path(__file__).resolve().parent.parent
DEPLOYED = KB / "graph" / "deployed.json"
REG = KB / "registry.yaml"

TAG = re.compile(r'v(\d+)\.(\d+)\.(\d+)(?:[.\-](\w+))?')

def registry_rows():
    txt = REG.read_text(); rows, cur, inr = [], None, False
    for line in txt.splitlines():
        if line.startswith("repos:"): inr = True; continue
        if inr and re.match(r'^\w', line): inr = False
        if not inr: continue
        m = re.match(r'\s+-\s+id:\s*(\S+)', line)
        if m: cur = {"id": m.group(1)}; rows.append(cur); continue
        m = re.match(r'\s+(\w+):\s*(.*)$', line)
        if m and cur is not None: cur[m.group(1)] = m.group(2).strip()
    return rows

def main():
    if not DEPLOYED.exists():
        print(f"no deployed snapshot at {DEPLOYED.relative_to(KB)}")
        print("capture one with (read-only):")
        print('  KUBECONFIG=<path> kubectl get deploy -n platform-subsystem-qa -o json \\')
        print('    | python3 -c \'import json,sys; d=json.load(sys.stdin); '
              'print(json.dumps({x["metadata"]["labels"].get("app.kubernetes.io/name",x["metadata"]["name"]): '
              'x["spec"]["template"]["spec"]["containers"][0]["image"] for x in d["items"]}, indent=1))\' '
              f'> {DEPLOYED.relative_to(KB)}')
        return 2
    dep = json.loads(DEPLOYED.read_text())
    rows = {r["id"]: r for r in registry_rows()}
    print(f"{'service':22} {'indexed branch':26} {'deployed image tag':46} verdict")
    print("-" * 118)
    stale = 0
    for rid, r in sorted(rows.items()):
        depname = r.get("deployment")
        if not depname: continue
        img = next((v for k, v in dep.items() if k in depname or depname.startswith(k)), None)
        if not img:
            print(f"{rid:22} {r.get('branch',''):26} {'(not deployed in this namespace)':46} -")
            continue
        tag = img.split(":")[-1]
        bm, tm = TAG.search(r.get("branch", "") or ""), TAG.search(tag)
        verdict = "?"
        # platform-core and the task-manager image use unrelated version schemes
        # (library version vs AKST image version). Comparing them is meaningless.
        if rid == "task-manager":
            print(f"{rid:22} {r.get('branch',''):26} {tag[:46]:46} "
                  f"N/A    (library vs image version schemes differ)")
            continue
        if bm and tm:
            bv, tv = tuple(map(int, bm.groups()[:3])), tuple(map(int, tm.groups()[:3]))
            if bv == tv:   verdict = "MATCH"
            elif bv < tv:  verdict = f"STALE  (deployed is ahead: {'.'.join(map(str,tv))})"; stale += 1
            else:          verdict = f"AHEAD  (checkout newer than deployed)"
        elif not bm:
            verdict = "STALE? (checkout not on a release branch)"; stale += 1
        print(f"{rid:22} {r.get('branch',''):26} {tag[:46]:46} {verdict}")
    print(f"\n{stale} checkout(s) look stale. Facts from those repos must be labelled with "
          f"their commit and NOT presented as current platform behaviour.")
    return 1 if stale else 0

if __name__ == "__main__":
    sys.exit(main())

---
type: design-doc
status: in-flight
provenance: design-doc
subject: smart safe deployment
source: ~/Proj/platform/Understandwebhoook/docs/smart_deployment/ (copied 2026-11-13)
state: not committed to any repository; see ../smart-safe-deployment.md
---

> **In-flight design, not platform behaviour.** Copied unchanged from the working
> documents of the smart-safe-deployment effort. Its code references point at an
> uncommitted working copy and are not checked. For what safe deployment does today
> see [safe-deployment](../../../flows/safe-deployment.md).

# Smart Safe Deployment – Implementation Report and Code Walkthrough

**Date:** 2026-11-13
**Status:** Implemented and unit-tested. Not committed. The end-to-end run on dev Kubernetes is still pending.

**Related documents (this folder):**
- `Smart Deployment Rules.md`
- `Dry run rules.md`
- `Deployment Pipeline Validation Reference and Configuration Checks.md`
- `2026-11-13-smart-safe-deployment-design.md`

---

## 1. Summary

Today's safe deployment pauses every app, drains every fb-worker pod and scales down every workflow component, whatever changed.

Smart safe deployment compares the config being deployed with the config that is running, and stops only what Helm is actually going to restart:

| What changed (decided by the Kubernetes **pod template**) | What smart safe deployment does |
|---|---|
| A workflow component's template (akst, rpa-gateway, wf-engine, wf-worker, wf-aggregator-*), or one was added or removed | Scale down **all** workflow components, one by one, in today's order |
| An app's template (image, env incl. platform-injected values, resources, node selectors, worker type) | **Pause** that app and **drain** all of its pods |
| Only an app's min/max or replica count | **No pause.** Gracefully scale down only the running pods above the new deployment count |
| Nothing at all | **No changes.** Jenkins does not run; DSS and the held schedules are restored |
| Anything that cannot be compared safely | **Full**: today's flow |

How it works:
- Config generation moved from the Jenkins pipeline into workflow-ops as an API, so the same generator produces both the config Helm deploys and the config smart deployment compares.
- Each deployment's generated config is saved in DMS as the next deployment's **baseline**. Env values are replaced by keyed fingerprints, so no secrets are stored.
- The comparison and the rules live in a **shared library**, which the dry-run feature (built separately) reuses and extends.

It is always on; there is no feature flag. Whenever a smart plan can't be made (first deployment, missing baseline, generation error), the plan is **FULL**, which pauses and drains exactly like today's safe deployment.

---

## 2. Scope, repositories and branches

| Repository | Base branch | Working branch | Worktree |
|---|---|---|---|
| e5-platform-ss-workflow-ops | `release/v1.7.4` | `feat/smart-deployment` | `~/Proj/platform/Understandwebhoook/smart-deployment/e5-platform-ss-workflow-ops` |
| e5-deployment-mgmt-service (DMS) | `release/v1.9.8` | `feat/smart-deployment` | `.../smart-deployment/e5-deployment-mgmt-service` |
| e5-platform-devops-pipeline | `feat/tenant-credential-sharing` (owner's choice: `releases/v4.1.1` lacks the credential-sharing generator) | `feat/smart-deployment` | `.../smart-deployment/e5-platform-devops-pipeline` |

**In scope:**
- the shared library;
- the config generation API and the generator move;
- saving the baseline;
- the narrowed safe-deployment state machine;
- the Jenkins change;
- one new DMS RPC.

**Out of scope (by owner decision):**
- the config validation layer and the dry-run feature (other developers);
- linking a dry-run SHA to a later deployment;
- any detection for hard deployments;
- a Jenkins guard before `helm upgrade`;
- DSS quiesce rules and live Kubernetes pod counts;
- app removal, which validation rejects;
- prod and other Jenkinsfiles.

---

## 3. Architecture

```
                         ┌───────────────────────── workflow-ops ──────────────────────────────┐
 Interface ──DEPLOYMENT──►  INIT ── pins config-repo SHA (flag on, safe only)                   │
 (task)                  │   │                                                                  │
                         │   ▼                                                                  │
                         │  CONFIG_FETCHED ── SmartDeploymentPlanner.build_plan()               │
                         │   │                 ├─ DMS GetDeploymentStateByKey(active dv,        │
                         │   │                 │       "generated_config")  → baseline          │
                         │   │                 ├─ DeploymentConfigGenerationService (PREVIEW)   │
                         │   │                 │       → candidate (same SHA, no writes)        │
                         │   │                 ├─ ConfigChangeDetector  → ConfigChangeSet       │
                         │   │                 └─ DeploymentImpactAnalyzer → DeploymentImpact    │
                         │   │  NO_CHANGES ──► task completion (DSS on, schedules released)     │
                         │   ▼                                                                  │
                         │  DRAIN_APPS_INITIATED (new) ── pause all needed apps → wait for all →│
                         │   │       per-app count from DMS → scale down all (workflow-manager)  │
                         │   ▼                                                                  │
                         │  SCALE_DOWN (workflow components: all, or none)                      │
                         │   ▼                                                                  │
                         │  JENKINS ── params CONFIG_REPO_SHA, USE_CONFIG_GENERATION_API=true    │
                         │   ▼            │                                                     │
                         │  RESUME ...    │  Jenkins "Generate Config Files"                    │
                         │                ▼                                                     │
                         │  PipelineOps.GenerateDeploymentConfig (DEPLOY) ◄─────────────────────┼── Jenkins
                         │   ├─ GitHub tarball @SHA (cached)                                     │
                         │   ├─ vendored generator in a subprocess (CreateDeploymentVersion,S3) │
                         │   └─ DMS SaveDeploymentState(new dv, "generated_config", baseline)   │
                         └──────────────────────────────────────────────────────────────────────┘
```

**Key idea:** the baseline is saved against the deployment version that DEPLOY generation creates. That version only becomes *active* after a successful deployment. So "active version → its `generated_config` row" always means "the config of the last successful deployment", and no extra bookkeeping is needed.

---

## 4. End-to-end flows

### 4.1 Safe deployment, flag on

| Step | State | What happens |
|---|---|---|
| 1 | INIT | Existing checks: credential-group pre-check, DSS disable, trigger hold. **New:** resolve the config branch head to a commit SHA. Store it as `task_payload.config_repo_sha` and as Jenkins parameter `CONFIG_REPO_SHA`. Also set `USE_CONFIG_GENERATION_API=true`. |
| 2 | CONFIG_FETCHED | Build the plan, persist it as `task_payload.deployment_plan`, and build the pause and scale-down entries from it. With NO_CHANGES, complete the task here. |
| 3 | DRAIN_APPS_INITIATED | Same order as today: all needed pauses are sent together. Once **every** pause has completed, all needed scale-downs (paused apps and excess-only apps) are sent together. Each app's pod count is read from DMS right before its scale-down is sent. |
| 4 | SCALE_DOWN_FB_WORKERS_COMPLETED → SCALE_DOWN | Workflow components are scaled down from `deployment_hierarchy`: today's full list, or `[]`. |
| 5 | JENKINS | Jenkins checks out `CONFIG_REPO_SHA` and calls `GenerateDeploymentConfig` (DEPLOY) at the same SHA. Helm deploys. |
| 6 | NEW_CONFIG_FETCHED → RESUME | Unchanged. Only apps paused by workflow-ops are resumed (`paused_by == WORKFLOW_OPS`). |
| 7 | TASK_COMPLETION | Unchanged. DSS is enabled for the new version. |

### 4.2 Hard deployment

The flow is unchanged and there is no detection. workflow-ops still passes `USE_CONFIG_GENERATION_API=true`, so the hard deployment's generated config is saved as a baseline too.

### 4.3 No changes

CONFIG_FETCHED stores the plan, sets `current_state = TASK_COMPLETION` and calls the existing task-completion handler. That handler:
- marks the deployment up;
- calls `enable_dss_after_deploy` with the current version, which handles old == new and re-enables that version's groups and DSS;
- runs the queued workflows;
- reports success.

Jenkins is never triggered.

### 4.4 Failure

Any failure in DRAIN_APPS_INITIATED (a pause failed, an incomplete scale-down, or an exception) runs `rollback_from_fb_scale_down`, which:
- re-enables the pods that were scaled down, using the counts recorded per app;
- resumes the apps workflow-ops paused;
- re-enables DSS and releases the held triggers.

The task then fails with stage `drain-apps`.

---

## 5. Shared library: `src/services/smart_deployment/`

Pure logic, except `planner.py`. These are the classes the dry-run developer builds on.

### 5.1 Always on (no flag)

Every safe deployment pins the SHA at INIT and builds a plan at CONFIG_FETCHED. Later states route on whether `task_payload.deployment_plan` exists. A deployment that was already in flight when this release rolled out has no plan, so it finishes on today's PAUSE_APPS path, which is kept for exactly that.

### 5.2 Replica rule: `replica_policy.py`

```python
def target_deployment_count(scaling_entry: Dict[str, Any]) -> int:
    sharing = scaling_entry.get("credentialSharingGroup")
    if isinstance(sharing, dict) and sharing.get("enabled") is True:
        total = sharing.get("totalDeploymentCount")
        if total is not None:
            return int(total)
    if "minReplicas" in scaling_entry and "maxReplicas" in scaling_entry:
        return int(scaling_entry["maxReplicas"])
    return int(scaling_entry.get("replicaCount", DEFAULT_REPLICA_COUNT))


def calculate_scale_down_count(running: Optional[int], target: int) -> int:
    """Running pods Helm would otherwise delete: `max(0, running - target)`."""
    return max(0, int(running or 0) - int(target))
```

**What the two numbers mean.**
- *running*: DMS `fb_worker_pod.replica_count`, i.e. pods running now. DMS sets it at deploy time and DSS/CDS move it by +n/−n on every scale (`FbWorkerPodRepositoryImpl`, delta update under a row lock). DSS is disabled at INIT, so it is stable during the deployment.
- *target*: how many deployments (`{app}-fb-worker-{configVersion}-{index}`) the new config will have. Every fb-worker deployment holds **at most one pod**, so this is also the most pods the app can keep.
- Scale-down = `max(0, running − target)`, the running pods that would otherwise lose their deployment to Helm.

**How the generator decides the deployment count** (`services/workflow/fb_worker.py`):

| App type | Deployments created | Which get a pod (`replicaCount: 1`) | `target_deployment_count` |
|---|---|---|---|
| Credential sharing member (`credentialSharingGroup.enabled: true`) | `totalDeploymentCount` slots | Running slots are kept, then filled up to `minReplicas`; every other slot is idle (0) | `totalDeploymentCount` |
| Dynamic scaling, task based (`minReplicas` / `maxReplicas`) | `maxReplicas` | Running kept, filled up to `minReplicas` | `maxReplicas` |
| Fixed (`replicaCount`) | `replicaCount` | All | `replicaCount` |
| Count 0 (`maxReplicas: 0` or `replicaCount: 0`) | One deployment `01` with 0 pods | None | 0 (drain everything) |

**Credential sharing and its headroom.** The group is defined in two parts. The tenant file sets `sharedCount` (how many credentials the CCS pool has) and `groupName`; a workflow joins with `enabled: true` and the same `groupName`. The generator's `apply_sizing` (`credential_sharing.py`) then adds:

```python
MULTIPLIER_ABOVE = 25; MINIMUM_DEPLOYMENTS = 50; DEPLOYMENT_MULTIPLIER = 2

def calculate_sizing(shared_count):
    if shared_count <= MULTIPLIER_ABOVE:
        return MINIMUM_DEPLOYMENTS, 1            # 50 slots
    return shared_count * DEPLOYMENT_MULTIPLIER, DEPLOYMENT_MULTIPLIER   # 2 x sharedCount slots
```

- `totalDeploymentCount` is always **more** than `sharedCount`: at least 50 for a pool of up to 25, otherwise double. The extra idle slots are the headroom. CDS can switch any idle slot on when a credential frees up, without a redeploy.
- The sized block is written into `userConfig` (`deployment-config-values.json`), which is where `scaling` comes from, so the target is read straight from the generator's own result rather than recomputed.
- For a member the generator **overwrites `maxReplicas` with `minReplicas`** and drops `replicaCount`. That is why the credential-sharing check comes first. Using `maxReplicas` would make a member with `minReplicas: 2` and 8 running pods look like it had 6 too many.
- A member's running pods can't exceed the credentials in the pool, so running ≤ old `sharedCount`. Since every slot count is above its `sharedCount`, the result is normally 0. Only a large cut leaves running pods above the new slot count (e.g. `sharedCount` 100 → 10: slots 200 → 50, up to 100 running). Those excess pods are scaled down gracefully first.
- When the slot count drops, `get_deployment_numbers` keeps the running slots and removes idle ones first. So without an excess, no running pod is touched.

| Running | Change | Slots after | Scale-down |
|---|---|---|---|
| 8 | `sharedCount` 20, `minReplicas` 2 | 50 | 0 (not 6) |
| 35 | `sharedCount` 40 → 30 | 80 → 60 | 0 |
| 30 | `sharedCount` 30 → 10 | 60 → 50 | 0 |
| 90 | `sharedCount` 100 → 10 | 200 → 50 | 40 |

These rows are the test `test_credential_sharing_headroom_is_never_scaled_down`.

**How a change is detected.** The detection is the same with or without credential sharing:
1. Only the deployment count or the pods that are on changed: `replicaCount`, `deploymentIndex` and `dssEnabled` are not part of the pod template, and copies are compared as a set. So the templates match, the app gets `SCALE_DOWN_EXCESS` (no pause), and the amount above is worked out at drain time.
   - Without credential sharing this covers `minReplicas`, `maxReplicas` or `replicaCount` edits, and turning dynamic scaling on or off (`dssEnabled`).
   - With credential sharing it covers `sharedCount` edits (more or fewer slots) and `minReplicas` edits.
   - `userConfig` differs, so the deployment is not treated as NO_CHANGES and Helm still applies the new count.
2. Anything that changes what is inside the pod (image, env, resources, worker type) is a template change: pause and drain the whole app. That includes adding `CCS_CRED_*` env when an app joins a pool.

- `calculate_scale_down_count` is the single rule from the rules doc.
- The examples table of `Smart Deployment Rules.md` is also a test vector in `tests/test_smart_deployment_replica_policy.py`.

### 5.3 Secrets only: `fingerprint.py`

Only **credentials and tokens** are fingerprinted. Every other value stays readable, so the dry run can report old and new values: a credential sharing group change, a timeout, `akst.inMemoryTaskStoreEnabled: false → true`.

```python
def is_secret(name) -> bool:
    normalised = name.lower().replace("_", "").replace("-", "").replace(".", "")
    return normalised in SECRET_NAMES or normalised.endswith(SECRET_SUFFIXES)
```

**What counts as secret.** It is a superset of the generator's own list for the masked config it uploads to S3 (`mask_sensitive_configs` in the vendored `utils.py`):

| | Names |
|---|---|
| Exact names (the generator's `keys_to_mask`) | `aggregatorsConfig`, `kafka`, `KAFKA_BOOTSTRAP_SERVERS`, `CCS_CRED_POOL_ACCESS_TOKEN`, `CCS_E5_API_ACCESS_TOKEN`, `AWS_ACCESS_KEY`, `AWS_SECRET_ACCESS_KEY`, `AppPass`, `AppUser`, `APP_USER`, `APP_PASS`, `stateservice`, `fileStorage`, `dbconfig`, `PLATFORM_CONFIG`, `WORKER_CONFIG` |
| Name endings (the generator's `suffixes_to_mask`) | `…SECRET_KEY`, `…SECRET_ACCESS_KEY`, `…API_KEY`, `…TOKEN`, `…PASSWORD` |
| Extra endings, same kind of value under camelCase or other names | `…accessKey`, `…accessKeyId`, `…secret`, `…privateKey`, `…credentials`, `…passwd`, `…pwd`, `…connectionString` |

- Matching ignores case, `_`, `-` and `.`. So `secretAccessKey`, `ccsAPIAccessToken` and `password` inside a JSON blob are caught as well.
- **Not secret:** `credentialSharingGroup` (and its `groupName`, `sharedCount`, `enabled`), `akst`, `TASK_QUEUE`, `CREDENTIAL_EVENTS_TOPIC`, `maxTokens`, images, resources, replica fields.
- `test_everything_the_generator_masks_is_secret` reads the generator's two lists from its source and fails if a name is ever added there that this rule doesn't cover.

**`mask_secrets(document)`** returns a copy in which only secrets are replaced. It walks any document: services, `userConfig`, global settings.
- **Env entry** (`{"name": …, "value": …}`): a secret name means the value is fingerprinted. Otherwise a JSON-object string is parsed into a dict, so key order doesn't matter and the changed inner key can be named, and nested secret keys inside it (e.g. `password`) are fingerprinted.
- **Dict key:** a secret key name means its value is fingerprinted; otherwise the walk continues into the value.
- **Fingerprint:** `hmac:<hex>` (HMAC-SHA256 with `CONFIG_FINGERPRINT_KEY`). Equal values give equal fingerprints, so a password rotation is still detected (it changes the pod template, so the app is drained), but the value can't be recovered.
- **Secret JSON blobs** (`dbconfig`, `kafka`, `PLATFORM_CONFIG`…) are fingerprinted per top-level key. The report can say `env:dbconfig.password` changed without showing it.

### 5.4 Baseline document: `baseline.py`

**Why it exists.** To know which pods Helm will restart, we compare *what Helm received last time* with *what it will receive now*. "Now" is easy: the PREVIEW generation at CONFIG_FETCHED. "Last time" is the hard part:

| Source of "last time" | Why not |
|---|---|
| Kubernetes live manifests | Ruled out (no live Kubernetes state), and the Helm values would have to be reverse-built from manifests |
| Re-generating the previous commit | Gives a different answer: the generator also reads SSM, DynamoDB (image tags) and DMS, which may have changed since |
| The existing `deployment_state` `<configVersion>` row | A different shape and lossy: it is not the Helm input |

So at every DEPLOY generation (the one Jenkins hands to Helm) we **save the generated output itself** against the new deployment version. That saved copy is the baseline, and the next safe deployment compares against it. That is the "save the final generated config and compare against it" decision.

It can't be stored as raw `config-values.yaml`, and each field is there for a reason:

| Field | Why it is there |
|---|---|
| `services` | The Helm services. The raw file holds DB passwords, access keys and tokens, so those are fingerprinted (§5.3); everything else stays readable. |
| `user_config` | The merged workflow config (`userConfig`), secrets fingerprinted. Some edits never reach a pod template but still need a deployment (timeouts, schedule rows, credential sharing groups that DMS stores). Without it the plan could say NO_CHANGES and skip them, and the dry run reads old and new values from it. |
| `scaling` (property) | Per app `minReplicas` / `maxReplicas` / `replicaCount` / `credentialSharingGroup`, read from `user_config`, not stored twice. Needed for the target deployment count (§5.2). |
| `global_settings` | Values outside `services` in `config-values.yaml` (e.g. `priorityEnabled`), secrets fingerprinted. Same reason as `user_config`. |
| `config_repo_sha` | Which commit it came from; for tracing and debugging only. |
| `schema_version` | If the format ever changes, an old row is refused, and that deployment safely runs FULL. |

**Same class on both sides.** The PREVIEW result (the candidate) is turned into a `DeploymentBaseline` by the same code, so both sides are normalised and masked identically. Any remaining difference is a real one.

**Life cycle:**
1. Jenkins calls `GenerateDeploymentConfig` (DEPLOY), and DMS creates deployment version `dv-2`.
2. `DeploymentBaseline.from_generated_files(...)` is saved in `deployment_state(dv-2, "generated_config")`.
3. Helm deploys `dv-2`.
4. The next safe deployment loads `generated_config` of the active version (`dv-2`), runs PREVIEW, and builds the candidate with the same class. `ConfigChangeDetector.detect(baseline, candidate)` follows.
5. No row (the first deployment after this release), an unreadable row or an unknown schema means FULL. That deployment saves a row, so the next one is smart.

```python
@dataclass
class DeploymentBaseline:
    services: List[Dict[str, Any]]            # config-values.yaml services, secrets fingerprinted
    user_config: Dict[str, Any]               # whole userConfig, secrets fingerprinted
    global_settings: Dict[str, Any]           # non-service keys of config-values.yaml
    config_repo_sha: Optional[str] = None
    schema_version: int = SCHEMA_VERSION      # 1

    @property
    def scaling(self):                        # per app: workerType, min/max, replicaCount,
        return _scaling_by_app(self.user_config)   # credentialSharingGroup
```

**Normalisation.** Values that are created fresh for every deployment version would otherwise always look changed. They are neutralised before masking:

```python
def _normalise_service(service):
    result = copy.deepcopy(service)
    if result.get("type") == CRONJOB_TYPE:
        for key in CRONJOB_MINTED_FIELDS:          # schedule_id, schedule_expression,
            result.pop(key, None)                  # start_date_time, end_date_time
    for env in result.get("env") or []:
        if result.get("type") in FB_WORKER_TYPES and env.get("name") == "DEPLOYMENT_ID":
            env["value"] = NORMALISED_VALUE
        if result.get("name") == TASK_READER_NAME and env.get("name") == "deploymentDetails":
            env["value"] = _normalise_deployment_details(env.get("value"))  # deploymentVersionId
    return result
```

The top-level `deploymentVersionId` is left out of the global settings as well. As a result, a DEPLOY generation (real ids) and a later PREVIEW of the same config (placeholder ids) compare equal. `TestDeployThenPreviewOfTheSameConfig` checks exactly this.

**Storage.** `to_json()` / `from_json()` use camelCase keys: `schemaVersion`, `configRepoSha`, `services`, `userConfig`, `globalSettings`. A row that isn't JSON, or has an unknown `schemaVersion`, raises `UnsupportedBaselineError`; the planner turns that into FULL. The row is stored under `BASELINE_STATE_KEY = "generated_config"`.

### 5.5 Change detection: `change_detection.py`

The **pod template is the source of truth**. Replica fields are not part of it:

```python
NON_TEMPLATE_KEYS = frozenset({"replicaCount", "deploymentIndex", "dssEnabled"})

def pod_template(service):
    return {k: v for k, v in service.items() if k not in NON_TEMPLATE_KEYS}
```

**Classification** (`classify`), based on the generator's output format:

| Generated service | `ComponentKind` |
|---|---|
| `type` = `fb-worker` / `nonrpa-worker` | `FB_APP` (all deployment copies of the app are grouped as one component) |
| `type` = `cronjob` | `CRONJOB` (keyed `wf-trigger/<trigger_id>`) |
| `name` = `task-reader` | `TASK_READER` |
| any other `app` (akst, rpa-gateway, wf-engine, wf-worker, wf-aggregator-*) | `WORKFLOW` |

**Comparison** (`_compare`) gives each component a `ChangeType`:
- `ADDED` or `REMOVED`.
- `POD_TEMPLATE_CHANGED` when the **set** of normalised templates differs. Comparing sets makes adding or removing identical deployment copies, i.e. a replica change, not count as a change.
- `UNCHANGED` otherwise.

`type` is part of the template, so a worker-type change (fb-worker ↔ nonrpa-worker) on the same app name is a template change. `changed_fields` names what differs, never values: `image`, `resources`, `type`, `env:API_URL`, `env:akst.inMemoryTaskStoreEnabled`.

```python
@dataclass
class ConfigChangeSet:
    changes: List[ComponentChange]
    baseline: DeploymentBaseline
    candidate: DeploymentBaseline
    def has_changes(self) -> bool: ...
    def changed(self, kind=None) -> List[ComponentChange]: ...
    def of_kind(self, kind) -> List[ComponentChange]: ...
```

Each `ComponentChange` also carries its full `baseline` and `candidate` service lists. That is the extension point for the dry run: new business checks can read any field without changing this module.

### 5.6 Rules: `impact.py`

```python
class DeploymentImpactAnalyzer:
    def analyze(self, change_set: ConfigChangeSet) -> DeploymentImpact:
        app_changes = change_set.of_kind(ComponentKind.FB_APP)
        if any(c.change_type == ChangeType.REMOVED for c in app_changes):
            return DeploymentImpact.full("APP_REMOVED")          # defensive: validation rejects removal

        if not change_set.has_changes and _same_settings(change_set):
            return DeploymentImpact(mode=DeploymentMode.NO_CHANGES)

        changed_workflow = [c.name for c in change_set.changed(ComponentKind.WORKFLOW)]
        scaling = change_set.candidate.scaling
        return DeploymentImpact(
            mode=DeploymentMode.PARTIAL,
            apps={c.name: _app_impact(c, scaling) for c in app_changes},
            scale_down_workflow_components=bool(changed_workflow),
            changed_workflow_components=changed_workflow,
        )
```

- **Workflow components:** one or more changed → `scale_down_workflow_components = True`, which means today's full ordered list. A task-reader or cron job change alone does not trigger this.
- **Per app** (`_app_impact`):
  - `POD_TEMPLATE_CHANGED` → `PAUSE_AND_DRAIN`
  - `ADDED` → `NONE`
  - `UNCHANGED` → `SCALE_DOWN_EXCESS`, with `target_deployment_count` from the candidate's scaling. "Unchanged" apps are always checked, because the running count can already be above max.
- **NO_CHANGES:** no component changed, and `userConfig` (which includes scaling) and the global settings are identical. A timeout edit therefore prevents NO_CHANGES even though no template changed.
- `DeploymentImpact.full(reason)` represents today's flow. `to_dict()` is what gets persisted in the ops state.

> The plan decides **which** apps are affected and **what** happens to them. It never decides **how many** pods: that is worked out later, per app (§7.3).

### 5.7 Planner: `planner.py`

```python
class SmartDeploymentPlanner:
    async def build_plan(self, fb_payload, deployment_details, config_repo_sha) -> DeploymentImpact:
        try:
            change_set = await self.load_change_set(fb_payload, deployment_details, config_repo_sha)
        except SmartPlanUnavailable as unavailable:
            return DeploymentImpact.full(unavailable.reason)
        except Exception:
            return DeploymentImpact.full("PLAN_ERROR")
        return DeploymentImpactAnalyzer().analyze(change_set)

    async def load_change_set(self, fb_payload, deployment_details, config_repo_sha) -> ConfigChangeSet:
        # raises SmartPlanUnavailable(reason)
        ...
```

- `build_plan` never raises: any problem gives FULL with a reason.
- `load_change_set` raises `SmartPlanUnavailable(reason)`. The dry run can call it to get the raw change set.

| Fallback reason | When |
|---|---|
| `CONFIG_REPO_SHA_UNRESOLVED` | INIT could not pin the commit |
| `NO_ACTIVE_DEPLOYMENT_VERSION` | the deployment details carry no version id |
| `NO_BASELINE` | DMS returns NOT_FOUND or an empty value (first smart deployment; deployed through another Jenkinsfile) |
| `UNREADABLE_BASELINE` | not JSON, or an unknown schema version |
| `PREVIEW_GENERATION_FAILED` | the generator rejected the config or failed |
| `APP_REMOVED` | from the analyser |
| `PLAN_ERROR` | anything unexpected (e.g. `CONFIG_FINGERPRINT_KEY` missing) |

---

## 6. Config generation in workflow-ops

### 6.1 Vendored generator: `src/libs/config_generator/`

The devops generator (`libs/deployment-config-generator`), the parts of `libs/common` it imports, and `libs/proto/ss_deployment_mgmt.proto` were copied in. The layout is kept (`generator/`, `common/`, `proto/`) so the generator's `sys.path` hacks and its top-level imports (`utils`, `common`, `proto`) keep working. Only three intended edits were made, and `diff -r` against the devops origin shows nothing else.

**1. PREVIEW mode** (`generator/utils.py`):

```python
def is_preview_mode():
    return os.getenv("CONFIG_GENERATION_MODE", "").strip().upper() == PREVIEW_MODE

def preview_schedule_details(triggers):
    """Stands in for the CreateDeploymentVersion response in PREVIEW mode."""
    ...  # deploymentVersionId / deploymentId = "preview",
         # one scheduleInfo per trigger (scheduleId "preview-<name>", scheduleExpression from WORKFLOW_SCHEDULE)

def get_schedule_details(...):
    if is_preview_mode():
        return preview_schedule_details(triggers)
    ...  # unchanged: DMS CreateDeploymentVersion
```

`persist_generated_configs` skips `upload_file_to_s3` in PREVIEW. Those two calls (`CreateDeploymentVersion` and the S3 upload) were the generator's only external writes. Everything else it does (SSM, DynamoDB, DMS client lookup, Kubernetes active-index lookup) is a read. Cron jobs are still generated in PREVIEW, so schedule changes stay visible to the dry run.

**2. Fixed-`replicaCount` keeps running pods first** (`generator/services/workflow/fb_worker.py`):

```python
active_indices = get_active_deployment_indices(kube_namespace, self.config_version, worker_name, service_type)
deploymentIndices = get_deployment_numbers(kube_namespace, self.config_version, worker_name,
                                           deployment_count, service_type, active_indices)
```

Without this, a graceful 5 → 3 scale-down could stop pods 01–02 (whichever took the scale-down tasks), and Helm would then delete the still-running 04–05. The min/max path already did this.

**3. Aggregator fallback fix:** the `except` block referenced an undefined `EMAIL_PLATFORM_CONFIG_TABLE`. A DynamoDB failure therefore crashed generation instead of falling back to the default attachment size.

These edits are covered by `generator/tests/test_workflow_ops_changes.py`. It runs in a **separate interpreter** (`tests/test_config_generator_vendored.py` launches it), because the generator's top-level module names would clash with workflow-ops' in one process.

### 6.2 Runner: `config_generation/generator_runner.py`

The generator mutates the global YAML loader, appends to `sys.path`, resolves the config repo relative to its working directory and prints the merged config (with resolved secrets) to stdout. Each run therefore gets a fresh process and a private folder:

```
<tmp>/config-repo  -> symlink to the extracted commit
<tmp>/work         -> cwd; outputs land in work/configs-<build>/
```

```python
process = await asyncio.create_subprocess_exec(
    self.python_executable, os.path.join(self.generator_dir, "main.py"),
    request.workflow_name, request.variant_name, request.environment, request.tenant_name,
    request.config_version, request.build_number, request.app_type, request.event_type,
    cwd=work_dir, env=self._environment(request.mode),        # PYTHONPATH removed, CONFIG_GENERATION_MODE set
    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
```

- **Exit codes:** 0 reads the four files (`config-values.yaml`, `masked-config-values.yaml`, `resource-configurations.json`, `deployment-config-values.json`). Exit 10 is `FailureKind.VALIDATION`; anything else is `GENERATION`. The timeout is 600 s.
- **No secret leaks:** the generator's output is never logged or returned. Only its last `Config validation failed:` / `Config generation failed:` line becomes the message.
- The temp folder is always removed.

### 6.3 Commit snapshots: `config_repo.py` and `snapshot_cache.py`

**New on `GitHubContents`:**
- `resolve_commit_sha(ref=None)` calls `GET /repos/{o}/{r}/commits/{ref}` and returns the SHA. An unknown ref raises `BadRequestError`.
- `download_tarball(sha, destination)` calls `GET /repos/{o}/{r}/tarball/{sha}` and extracts **regular files and folders only**. It refuses any member that would land outside the destination (absolute paths, `..`, links), then returns the single top-level folder.

The existing retrying GET became `_request(url, description, as_bytes=False)`, so the new calls share the session, token header (never in the URL) and tenacity retry rules. The tarball is needed because the generator scans the whole environment folder for credential-sharing checks.

**`ConfigRepoSnapshotCache`** keeps extracted commits at `<tmp>/config-repo-snapshots/<sha>/`:
- A download lands in a `.download-*` folder and is **renamed** into place, so concurrent callers never see a half-extracted tree.
- At most 5 snapshots, least recently used evicted first.
- A snapshot used within the last hour is never evicted, because a generation may still be reading it.
- A safe deployment generates twice at the same SHA (preview, then Jenkins), so the second generation reuses the snapshot.

### 6.4 Generation service: `config_generation/service.py`

```python
class DeploymentConfigGenerationService:
    async def generate(self, request: GenerationRequest) -> GenerationResult:
        async with GitHubContents() as client:
            sha = await client.resolve_commit_sha(request.config_repo_ref)
            repo_dir = await self.snapshot_cache.get(sha, client)
        result = await self.runner.run(request, repo_dir)
        result.config_repo_sha = sha
        # Config validation (separate work) plugs in here, before anything is saved.
        if result.success and request.mode == GenerationMode.DEPLOY:
            result.deployment_version_id = <deploymentVersionId from config-values.yaml>
            await self._save_baseline(result)       # DeploymentBaseline → DMS SaveDeploymentState
        return result
```

- A config repo read failure becomes a `GENERATION` failure result; `generate` never raises for expected failures.
- A failed baseline save is logged and does **not** fail the generation. The next smart deployment of that version just runs FULL.

### 6.5 gRPC API: `pipeline_ops_v1.proto` and `pipeline_webhook_service.py`

```proto
rpc GenerateDeploymentConfig (GenerateDeploymentConfigRequest) returns (GenerateDeploymentConfigResponse);

message GenerateDeploymentConfigRequest {
    string environment = 1; string workflow_name = 2; string variant_name = 3;
    string tenant_name = 4; string config_version = 5;
    string config_repo_ref = 6;            // SHA, branch or tag; empty = configured branch
    ConfigGenerationMode mode = 7;         // DEPLOY (PREVIEW is rejected over gRPC)
    string build_number = 8; string app_type = 9; string event_type = 10;
}
message GenerateDeploymentConfigResponse {
    bool success = 1; string message = 2; ConfigGenerationFailure failure_kind = 3;
    string config_repo_sha = 4; string deployment_version_id = 5; repeated GeneratedFile files = 6;
}
```

- **Servicer:** it validates the required fields and returns `INVALID_ARGUMENT` when one is missing. gRPC servicers are synchronous and run on worker threads, so it calls the async service with `asyncio.run(...)`, then maps the result.
- **Only DEPLOY is served.** PREVIEW returns the unmasked config without creating anything, so it stays in-process (the planner and the dry run) rather than handing resolved secrets to any caller on the network.
- **Message size:** server send/receive limits are raised to 32 MB (`server.py`), because large workflows' generated configs exceed the 4 MB default.

---

## 7. State machine changes (workflow-ops)

### 7.1 INIT: `handle_init_state.py`

```python
config_repo_branch = os.getenv("DEPLOYMENT_CONFIG_REPO")      # the branch Jenkins checks out
config_repo_sha = None
if not hard_deployment and smart_safe_deployment_enabled():
    config_repo_sha = await self._pin_config_repo_sha(task_data.get("task_id"), config_repo_branch)
...
"config_repo_sha": config_repo_sha,                            # in task_payload
...
if config_repo_sha:
    data["task_payload"]["jenkinsJobParameters"]["CONFIG_REPO_SHA"] = config_repo_sha
data["task_payload"]["jenkinsJobParameters"]["USE_CONFIG_GENERATION_API"] = "true"   # hard and safe
```

- `_pin_config_repo_sha` never fails INIT. A lookup error returns `None`, and the plan then falls back to FULL.
- One SHA is used for the whole safe deployment, so a commit made mid-deployment can't slip into Jenkins unplanned.

### 7.2 CONFIG_FETCHED: `handle_config_fetched_state.py`

For every safe deployment:

```python
plan = await self.planner.build_plan(task_payload["fb_payload"], deployment_details, config_repo_sha)
task_payload["deployment_plan"] = {**plan.to_dict(), "config_repo_sha": config_repo_sha}
if plan.mode == DeploymentMode.NO_CHANGES:
    task_payload["current_state"] = constants.DEPLOYMENT_STATE_TASK_COMPLETION
    await self.task_completion.handle_task_completion_state(task_metadata)
    return None
```

The entries are then built per app by `_pause_entry` and `_scale_down_entry`:

| App's action | Pause entry | Scale-down entry |
|---|---|---|
| FULL, or `PAUSE_AND_DRAIN`, and RUNNING | `PENDING`, `paused_by: WORKFLOW_OPS` | `PENDING`, `scale_mode: ALL` |
| FULL, or `PAUSE_AND_DRAIN`, neither RUNNING nor PAUSED | `COMPLETED`, `paused_by: WORKFLOW_OPS` (resumed afterwards, as today) | `PENDING`, `scale_mode: ALL` |
| `SCALE_DOWN_EXCESS` | `COMPLETED`, `paused_by: NOT_PAUSED` | `PENDING`, `scale_mode: EXCESS`, `target_deployment_count` |
| `NONE` (added app) | `COMPLETED`, `NOT_PAUSED` | `COMPLETED`, `original_replica_count: 0` |
| Paused by the user | `COMPLETED`, `paused_by: USER` (as today) | per its action |

- `paused_by: NOT_PAUSED` is the existing resume/rollback convention: only `WORKFLOW_OPS` apps are ever resumed, so untouched apps are never touched again.
- `deployment_hierarchy` keeps today's list for FULL or when workflow components change; otherwise it becomes `[]`. The existing workflow scale-down handler already moves straight to `SCALE_DOWN_COMPLETED` on an empty list.
- The next state is `DRAIN_APPS_INITIATED` when there's a plan, else `PAUSE_APPS` (today).

### 7.3 New state DRAIN_APPS_INITIATED: `handle_drain_apps_state.py`

This replaces PAUSE_APPS + SCALE_DOWN_FB_WORKERS when a plan exists, and exits to the existing `SCALE_DOWN_FB_WORKERS_COMPLETED`, so everything downstream is unchanged.

```python
async def handle_drain_apps_state(self, task_data, task_metadata, service_type):
    state = task_metadata["task_payload"].get("current_state")
    if state == CONFIG_FETCHED:                       # POLLING / STARTUP: start the phase
        task_metadata = await self._start(task_metadata)
    elif state == DRAIN_APPS_INITIATED:
        if service_type == CONSUMER_SERVICE:          # a pause or scale-down callback
            task_metadata = await self._record_response(task_data, task_metadata)
        elif service_type == STARTUP_LOADER_SERVICE:  # restart recovery
            task_metadata = await self._send_ready_scale_downs(task_metadata)
    return await self._finish_if_done(task_metadata)  # None = waiting; metadata = drained
```

**`_start`**
- Sends `PAUSE_RESUME_APP` for every `PENDING` pause entry.
- Saves the state as `DRAIN_APPS_INITIATED`.
- Calls `_send_ready_scale_downs`. If no app needs a pause, every scale-down goes out now; otherwise nothing is sent until all pauses complete.

**`_send_ready_scale_downs` (claim before send)**

```python
if _has_failure(task_metadata):
    return task_metadata                                   # phase will roll back: send nothing new
ready = _ready_scale_downs(task_metadata)                  # [] until EVERY pause is COMPLETED, then all PENDING
counts = {e["app_name"]: await _scale_down_count(e) for e in ready}   # read from DMS *now*

def claim(metadata):                                       # re-applied on every conflict retry
    claimed.clear()
    if _has_failure(metadata):
        return
    for entry in _ready_scale_downs(metadata):             # still PENDING in the *latest* state
        entry["status"] = INITIATED if count > 0 else COMPLETED
        entry["original_replica_count"] = count
        claimed.append(entry["app_name"])

task_metadata = await self._save(task_metadata, claim)     # optimistic lock decides the winner
await create_remove_and_scale_down_task([... only claimed apps with count > 0 ...])
```

- **The amount is decided at the last moment.** `_scale_down_count` reads `fb_worker_pod.replica_count` from DMS (`get_fb_worker_pod_details`) right before the request is sent. Pauses take time and the count may have changed since the plan.

  ```python
  running = int((details or {}).get("replica_count") or 0)
  if entry.get("scale_mode") == SCALE_MODE_EXCESS:
      return calculate_scale_down_count(running, entry["target_deployment_count"])
  return running                                            # ALL: drain every pod
  ```
- **Pause all, then scale down all.** `_ready_scale_downs` returns nothing while any pause entry is not `COMPLETED` (apps that need no pause are saved as `COMPLETED` at CONFIG_FETCHED, so they never block). The callback that completes the last pause sends every scale-down in one `create_remove_and_scale_down_task` call. This matches today's PAUSE_APPS → SCALE_DOWN_FB_WORKERS order.
- **Why claim before send.** Kafka callbacks are scheduled on the shared loop without waiting, so the last two apps' pause callbacks can run at the same time and both see every pause completed. If entries were sent first and marked afterwards, a second callback reloading in between could send the same app's scale-down again. For EXCESS that means double the excess; for ALL it means a failed duplicate request and a spurious rollback. Claiming in the saved state first means the version check lets exactly one caller win each entry. `ConcurrentDrainCallbacksTest` reproduces the race with a store that has real version checks.
- `original_replica_count` is the count actually requested. Rollback re-enables exactly that many: workflow-ops `EnableDeployment` adds N zero-replica deployments.
- The request is the existing `FbWorkerPodScaleDown` task (`updateGitConfig: false`). workflow-manager sends one SCALE_DOWN task per pod to the app's virtual queue, so a pod finishes its current work before it stops. This is the same graceful mechanism Scale App Worker uses.

**`_record_response`**
- Routes callbacks by `taskPayload.fbName` (`PAUSE_RESUME_APP` or `FbWorkerPodScaleDown`). When `fbName` is missing it falls back to the payload shape (`metaData` means a pause; `requestedCount` means a scale-down).
- Pause semantics are the existing handler's: `success`, `"App is already in PAUSED state"` or `"Same request is already in progress"` all count as paused.
- Every completed pause calls `_send_ready_scale_downs`; it sends only when that pause was the last one outstanding.
- A scale-down succeeds only if `status == COMPLETED` and `requestedCount == completedCount`.

**`_finish_if_done`**
- **Outstanding** means a pause in flight, a scale-down `INITIATED`, or a scale-down `PENDING` (not yet sent). Once anything has failed, unsent entries no longer count as outstanding. A failed pause therefore rolls back as soon as the other pauses have answered, without any scale-down having been sent.
- While anything is outstanding, it returns `None` (wait for callbacks).
- If any pause or scale-down failed, it runs `rollback_from_fb_scale_down` and fails the task with stage `drain-apps`.
- Otherwise it saves `SCALE_DOWN_FB_WORKERS_COMPLETED` (next: `SCALE_DOWN_INITIATED`) and returns the metadata.

**`_save` (optimistic locking)**

```python
for attempt in range(MAX_SAVE_RETRIES):
    mutate(task_metadata)
    task_metadata["version"] = int(task_metadata.get("version") or 0) + 1
    response = await update_task_state(task_metadata)
    if response.get("success"):
        return task_metadata
    if not is_version_conflict_update(response):
        raise RuntimeError(...)
    task_metadata = await reload_task_metadata(task_metadata)   # apply the same mutation to the latest state
```

Every change is a small `mutate` function, so on a conflict it is re-applied to the freshly loaded state rather than overwriting someone else's update. This follows the convention in `CLAUDE.md`.

**Restart recovery.** On `STARTUP_LOADER_SERVICE`, if every pause has completed but some scale-downs were never sent (a crash in between), those are sent. If a pause is still running, nothing is sent; the pause callback will do it.

### 7.4 Wiring and registration

**`async_workflow_service.py`.** A new block before the existing CONFIG_FETCHED → pause block:

```python
if (task_metadata and task_metadata["task_payload"].get("deployment_plan")
        and current_state in (CONFIG_FETCHED, DRAIN_APPS_INITIATED)):
    task_metadata = await self.drain_apps_state.handle_drain_apps_state(task_data, task_metadata, service_type)
    if not task_metadata:
        return None
```

Unplanned tasks fall through to today's pause path untouched.

**Other registrations:**
- `constants.py`: `DEPLOYMENT_STATE_DRAIN_APPS_INITIATED`, `SCALE_MODE_ALL` / `SCALE_MODE_EXCESS`, `PAUSED_BY_NOT_PAUSED`. The state is added to `ROLLBACK_ELIGIBLE_DEPLOYMENT_STATES`.
- `deployment_rollback.py`: the state is in `FB_SCALE_DOWN_STATES`, so the generic rollback restores workers, resumes paused apps and finalises.
- `failure_details.py`: new stage `drain-apps` (code `DRAIN_APPS_FAILED`, user message "Failed to pause or scale down the affected applications before deployment. Please retry the deployment.") and the user label "pausing and scaling down affected applications".

---

## 8. DMS: `SaveDeploymentState`

```proto
message SaveDeploymentStateRequest { string deployment_version_id = 1; string key = 2; string value = 3; }
message SaveDeploymentStateResponse { bool success = 1; string message = 2; }
rpc SaveDeploymentState (SaveDeploymentStateRequest) returns (SaveDeploymentStateResponse);
```

- **`SaveDeploymentStateUseCase`** validates the request: the version id must be a valid UUID, the key must not be blank and the value must not be empty; otherwise `INVALID_ARGUMENT`. It then calls the repository inside `HibernateSessionUtil.executeInTransaction`.
- **`DeploymentStateRepositoryImpl.upsertDeploymentState`** loads the `DeploymentVersionEntity` (unknown → `NOT_FOUND`), looks up the `(version, key)` row, and updates its value or inserts a new row. The table has no unique constraint on `(deployment_version_id, key)`, only a plain index, so this lookup-then-write inside one transaction is the upsert, and no migration is needed.
- **Endpoint:** `DeploymentManagementServiceImpl.saveDeploymentState` uses the standard `GrpcRequestHandler.handleRequest` pattern.
- **Client side:** workflow-ops has a matching proto copy (CRLF line endings preserved) and `save_deployment_state_async(dv, key, value)` in `src/helpers/async_workflow.py`.

---

## 9. devops-pipeline

**`deployment/Jenkinsfile-safe-deployment`** (non-prod):
- New parameters:
  - `CONFIG_REPO_SHA`: validated `^([0-9a-fA-F]{7,40})?$`, default empty.
  - `USE_CONFIG_GENERATION_API`: validated `^(true|false)$`, default `false`.
- **Checkout:** when `CONFIG_REPO_SHA` is set, it runs `git checkout --detach <sha>` after the branch checkout, so `Insert Workflow API Details` reads the same commit.
- **Generate Config Files:**

  ```groovy
  def generateScript = (env.USE_CONFIG_GENERATION_API == 'true')
      ? './bash/generate-values-yaml-api.sh'
      : './bash/generate-values-yaml.sh'
  ```

  The default is the old local generator. That makes the rollout order-independent: devops can ship before workflow-ops, and only workflow-ops (with the smart flag on) switches Jenkins to the API.

**`bash/generate-values-yaml-api.sh`** runs `poetry install`, compiles the webhook protos and runs `libs/webhook/scripts/generate-deployment-config.py`.

**The script** builds the request from the pipeline env. `config_repo_ref` is `CONFIG_REPO_SHA`, or the branch when no SHA was pinned. It writes the files where the old script left them:
- `libs/deployment-config-generator/configs-<BUILD_NUMBER>/`, which the webhook step reads `deployment-config-values.json` from;
- `values/`, which provisioning and Helm read.

**`libs/webhook/client.py` → `generate_deployment_config(fields, output_dirs)`:**
- Calls the stub **directly with a timeout and no automatic retry**. DEPLOY creates a deployment version, so a retry after a lost reply would create a second one.
- Opens the channel with 32 MB message limits; `common/grpc_client.py`'s `GRPCClient` gained an optional `options` argument for this.
- Returns exit code 10 for `VALIDATION` and 1 otherwise, so Jenkins keeps reporting `config-validation` / `config-generation` as before.

**`libs/webhook/proto/pipeline_ops_v1.proto`** is now an identical copy of the workflow-ops proto. The old copy was a stale subset.

---

## 10. Data shapes

**`task_payload.deployment_plan`** (persisted at CONFIG_FETCHED):

```json
{
  "mode": "PARTIAL",
  "fallback_reason": null,
  "scale_down_workflow_components": false,
  "changed_workflow_components": [],
  "config_repo_sha": "9f1c…",
  "apps": {
    "Invoice": {"action": "PAUSE_AND_DRAIN", "target_deployment_count": 10, "changed_fields": ["image"]},
    "Claims":  {"action": "SCALE_DOWN_EXCESS", "target_deployment_count": 6, "changed_fields": []},
    "Fresh":   {"action": "NONE", "target_deployment_count": 2, "changed_fields": []}
  }
}
```

**A scale-down entry** (`task_payload.scale_down_fb_workers_state[]`):

```json
{"app_name": "Claims", "deployment_version_id": "…", "status": "INITIATED",
 "scale_mode": "EXCESS", "target_deployment_count": 6, "original_replica_count": 1,
 "action": "SCALE_DOWN", "fb_name": "FbWorkerPodScaleDown"}
```

**The baseline row** (`deployment_state`, key `generated_config`):

```json
{"schemaVersion": 1, "configRepoSha": "9f1c…",
 "services": [{"name": "invoice", "type": "fb-worker", "image": {"tag": "2.4.1"},
               "env": [{"name": "APP_PASSWORD", "value": "hmac:3b7e…"},
                       {"name": "TASK_QUEUE", "value": "AKST"}], "…": "…"}],
 "userConfig": {"fb_worker": [{"name": "invoice", "workerType": "Robotframework",
                               "minReplicas": 5, "maxReplicas": 10,
                               "credentialSharingGroup": {"enabled": true, "groupName": "g1", "sharedCount": 20,
                                                          "totalDeploymentCount": 50},
                               "env": [{"name": "APP_PASS", "value": "hmac:…"}]}], "…": "…"},
 "globalSettings": {"priorityEnabled": true, "…": "…"}}
```

---

## 11. Safety properties

| Concern | How it is handled |
|---|---|
| FULL plan | Every app paused (if running) and drained, all workflow components scaled down, same `paused_by` marking as today (tested) |
| Upgrade with deployments in flight | Tasks without a `deployment_plan` finish on today's PAUSE_APPS path |
| Anything uncertain | Planner falls back to FULL with a recorded reason |
| Secrets at rest | Env values HMAC-fingerprinted. Generator stdout never logged. Only its failure line returned. |
| Secrets on the wire | PREVIEW (unmasked, no side effects) is rejected over gRPC. DEPLOY returns files to Jenkins, as the pipeline produced them before. |
| Concurrent callbacks | Claim-before-send under optimistic locking |
| Duplicate deployment versions | DEPLOY call from Jenkins is not retried automatically |
| Partial failure | Rollback restores exactly the scaled-down counts and resumes only apps workflow-ops paused |
| Mid-deployment commits | One SHA pinned for the preview and Jenkins |
| Restart of workflow-ops | Plan persisted. Drain state recovers unsent scale-downs on startup. |
| Large configs | 32 MB gRPC limits on server and Jenkins client |
| Archive safety | Tarball extraction refuses paths outside the destination and links |

---

## 12. Configuration and rollout

| Variable (workflow-ops) | Default | Purpose |
|---|---|---|
| `CONFIG_FINGERPRINT_KEY` | empty; filled from SSM `/platform-subsystem/{env}/workflow-ops/config` | HMAC key. Without it no baseline is saved and plans fall back to FULL. Rotating it makes everything look changed once. |

The generator reuses workflow-ops' existing env: `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, `KUBE_API_SERVER` / `KUBE_API_TOKEN`, `DEPLOYMENT_MANAMGENT_SERVICE` and `CONFIG_REPO_*`.

**Other changes:**
- **Dependencies:** `ruamel.yaml` 0.18.6, `pytz` 2025.1, `packaging >= 24.1`, `pyyaml >= 6.0.1`. The lock file adds only `pytz` and `ruamel.yaml`; nothing existing changes version.
- **Dockerfile:** an extra protoc step for the vendored generator's proto.
- **`.gitignore`:** ignores its generated stubs.

**Rollout steps:**
1. Deploy the three branches in any order. Jenkins keeps local generation until workflow-ops asks otherwise.
2. Set `CONFIG_FINGERPRINT_KEY` in SSM. Until it is set no baseline is saved, so every safe deployment runs FULL (today's behaviour).
3. Check that the workflow-ops IAM role can do what Jenkins could: read the SSM parameters, DynamoDB image-metadata/client-config/email tables, the S3 masked-config bucket, and GitHub.
4. The first deployment of each workflow runs FULL and saves its baseline. Smart behaviour starts from the second.

---

## 13. Tests

| Repo | Command | Result |
|---|---|---|
| workflow-ops | `. .venv/bin/activate && PYTHONPATH=.:src python -m unittest tests/test_*.py` | 470 run. The only failures are 6 that already fail on `release/v1.7.4` without these changes: `test_handle_jenkins_state` JqRetry, `test_deployment_timeout` soft_deployment_without_apps, 4 in `test_jenkins_reporting`. |
| DMS | `./gradlew test --offline` | 453 tests, 0 failures |
| devops | `cd libs/webhook && python -m unittest tests/test_generate_deployment_config.py` | 6 pass |

New workflow-ops test files and what they prove:

| File | Covers |
|---|---|
| `test_smart_deployment_replica_policy.py` | Target counts per mode. The rules-doc examples table. |
| `test_smart_deployment_fingerprint.py` | Only secrets fingerprinted; ordinary settings and sharing groups readable; nested secrets in plain JSON caught; generator's mask list covered; equality, hiding, keyed, order-independence |
| `test_smart_deployment_baseline.py` | No secrets stored. Minted ids ignored. Real changes kept. Round trip. Schema guard. **DEPLOY baseline vs PREVIEW of same config → NO_CHANGES.** |
| `test_smart_deployment_change_detection.py` | Classification. Copies grouped. Replica-only ≠ change. Image/env/JSON-key/worker-type changes. Add/remove. Cron jobs. |
| `test_smart_deployment_impact.py` | Every mode and action rule, incl. workflow changes, task-reader exclusion, removed app → FULL |
| `test_smart_deployment_planner.py` | Preview at pinned SHA. NO_CHANGES. Each fallback reason. |
| `test_config_repo_commit.py` | SHA resolution. Tarball extraction. Path traversal refused. |
| `test_config_repo_snapshot_cache.py` | Download once. LRU eviction. Recently used kept. |
| `test_config_generation_runner.py` | Real subprocess with a stand-in generator: argv contract, mode, exit-code mapping, no output leak, cleanup |
| `test_config_generation_service.py` | Preview saves nothing. DEPLOY saves the fingerprinted baseline. Save failure tolerated. Bad ref. |
| `test_generate_deployment_config_rpc.py` | Request mapping, failure kinds, invalid input, PREVIEW rejected |
| `test_config_generator_vendored.py` | Runs the generator's own tests (PREVIEW, fixed-replica fix, aggregator fix) in a separate interpreter |
| `test_save_deployment_state.py` | DMS client helper |
| `test_handle_init_state.py` (added) | SHA pinned for every safe deployment, not for hard. Lookup failure tolerated. Jenkins API toggle. |
| `test_handle_config_fetched_state_smart.py` | Every safe deployment goes to the drain state. FULL marks `paused_by` as today. Pause/scale entries per action. Hierarchy. FULL. NO_CHANGES completes without saving CONFIG_FETCHED. |
| `test_handle_drain_apps_state.py` | Pause only affected apps. No scale-down while any pause runs. Immediate scale-down when no pause is needed. All scale-downs in one request after the last pause, counts read per app at that moment. Failed pause rolls back with no scale-down. Completion. Failure rollback. Routing without `fbName`. Restart recovery. Version-conflict retry. **Overlapping callbacks send once.** **First of two pauses sends nothing.** **Nothing sent after a failure.** Registration. |
| `test_async_workflow_service_drain_routing.py` | Planned tasks go to the drain handler, unplanned to today's pause. Drained tasks continue to the workflow scale-down. |

---

## 14. Using the library from the dry run

```python
planner = SmartDeploymentPlanner()

# Impact (what safe deployment would stop), using the real rules:
impact = await planner.build_plan(fb_payload, deployment_details, config_repo_sha)

# Raw comparison for business-change reporting:
change_set = await planner.load_change_set(fb_payload, deployment_details, config_repo_sha)
for change in change_set.changed():
    change.name, change.kind, change.change_type, change.changed_fields
    change.baseline, change.candidate      # full service dicts for extra checks (schedules, inputs, …)

# Pod counts for "Y of X running pods will be scaled down":
running = (await get_fb_worker_pod_details(dv, app))["replica_count"]
calculate_scale_down_count(running, impact.apps[app].target_deployment_count)
```

- The dry run runs inside workflow-ops, so it calls `DeploymentConfigGenerationService` in PREVIEW in-process. Validation goes after generation.
- Values are fingerprinted, so env changes can be reported by name only, as the dry-run rules require. Image tags, resources, schedules (`schedule`, `suspend`) and scaling settings stay readable.

---

## 15. Known limitations and follow-ups

1. **Helm chart / `--set` changes are not detected.** They aren't in the generated config. Roll them out with a hard deployment (owner decision).
2. **The baseline is the generated config, saved before Helm runs.** If Helm fails part-way and the next deployment returns to the previous config, the plan can say NO_CHANGES while the cluster still has the failed templates. Use hard deployment or `force` in that case.
3. **NO_CHANGES is reported as a plain "completed"** to task-manager. The message "No changes to deploy" is only in the logs; the completion message has no field for it.
4. **The drain handler's response parsing is copied** from the existing pause and scale-down handlers, not shared with them.
5. **Only the non-prod safe-deployment Jenkinsfile is switched.** Deployments through other Jenkinsfiles save no baseline, so the next smart deployment runs FULL. The devops copy of the generator stays until every Jenkinsfile is switched.
6. **The preview generation time** (seconds to a couple of minutes) is not in the overall-timeout budget computed at INIT.
7. **A pause callback can arrive before the INIT → DRAIN save.** This window is inherited from today's pause handler.

---

## 16. File index

**workflow-ops (new):**
- `src/services/smart_deployment/{__init__,replica_policy,fingerprint,baseline,change_detection,impact,planner}.py`
- `src/services/config_generation/{__init__,generator_runner,snapshot_cache,service}.py`
- `src/services/deployment_action/handle_drain_apps_state.py`
- `src/libs/config_generator/` (vendored generator, `common/`, `proto/`, `generator/tests/`)
- the tests listed in §13

**workflow-ops (changed):**
- `src/constants.py`
- `src/server.py`
- `src/helpers/async_workflow.py`
- `src/libs/config_repo.py`
- `src/proto/v1/pipeline_ops_v1.proto`
- `src/proto/clients/v1/deployment_management.proto`
- `src/services/async_workflow_service.py`
- `src/services/deployment_action/{handle_init_state,handle_config_fetched_state,deployment_rollback,failure_details}.py`
- `src/services/rpc/pipeline_webhook_service.py`
- `pyproject.toml`, `poetry.lock`, `Dockerfile`, `.gitignore`, `.env.example`, `deployment-configs/*.yaml`
- `tests/test_config_repo.py`, `tests/test_handle_init_state.py`

**DMS:**
- `src/main/proto/deployment_management.proto`
- `domain/usecase/SaveDeploymentStateUseCase.java`
- `domain/repository/DeploymentStateRepository.java`
- `data/repository/DeploymentStateRepositoryImpl.java`
- `grpc/service/DeploymentManagementServiceImpl.java`
- tests: `SaveDeploymentStateUseCaseTest`, `DeploymentStateRepositoryImplUpsertTest`

**devops-pipeline:**
- `deployment/Jenkinsfile-safe-deployment`
- `bash/generate-values-yaml-api.sh`
- `libs/webhook/client.py`
- `libs/webhook/scripts/generate-deployment-config.py`
- `libs/webhook/proto/pipeline_ops_v1.proto`
- `libs/common/grpc_client.py`
- `libs/webhook/tests/test_generate_deployment_config.py`

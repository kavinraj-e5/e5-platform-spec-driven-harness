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

# Smart Safe Deployment – Implementation Design

Status: draft for approval · Date: 2026-11-13 · Rules source: `Smart Deployment Rules.md` (this folder), adjusted by the decisions below.

## 1. Intent

Safe deployment today pauses every app, drains every fb-worker pod and scales down every workflow component, whatever changed. Most deployments change one app's env, one app's replica limits or one schedule. Smart safe deployment compares the configuration being deployed with what is running, and pauses, drains or scales down **only what the change actually affects**. Anything it cannot reason about safely falls back to today's full flow.

Config generation moves from the Jenkins pipeline into workflow-ops as an API, so the same generator produces the config for Jenkins (to deploy) and for workflow-ops (to compare). The comparison logic is a shared library that a separate dry-run feature will reuse and extend.

## 2. Scope

| In scope (this work) | Out of scope |
|---|---|
| Config generation API in workflow-ops (generator moved from devops-pipeline) | Config validation layer (another developer; runs after generation) |
| Saving the generated config of every deployment as the comparison baseline | Dry run feature, its API contract and its business-change checks (another developer) |
| Shared change-detection + impact library | Linking a dry-run commit SHA to a later deployment |
| Narrowed safe-deployment state machine in workflow-ops | Any detection for **hard** deployment (unchanged) |
| Jenkins `Generate Config Files` stage calls the API (non-prod safe-deployment Jenkinsfile) | Jenkins guard before `helm upgrade` (decided: not in v1) |
| New DMS RPC to save a deployment-state row | DSS quiesce/wait rules, Kubernetes live counts (decided: not in v1) |
| | App removal (rejected by config validation) |

Repos: `e5-platform-ss-workflow-ops` (main work), `e5-platform-devops-pipeline` (Jenkinsfile + client script), `e5-deployment-mgmt-service` (one RPC). Branch name `feat/smart-deployment` in each. workflow-ops branches from `origin/fix/group-count-validation` (it contains the DSS disable at INIT); the others from their current release branches.

## 3. Decisions made with the owner

1. **Baseline = the generated final config** (the values Helm receives), saved per deployment version. Platform-injected values (SSM, DynamoDB) are therefore covered.
2. **No secrets at rest:** credentials and tokens (the generator's own masking list plus similar names) are replaced with keyed fingerprints (HMAC-SHA256) before saving. Everything else stays readable, so changes such as a credential sharing group can be reported. Equal values give equal fingerprints; values cannot be recovered.
3. **Config generation is an API in workflow-ops** (later moves to its own service). Jenkins calls it; safe deployment and dry run call it in a no-writes mode.
4. **One commit SHA per safe deployment:** resolved once at the start and used for the preview generation and for Jenkins.
5. **Running pod count comes from DMS** (`fb_worker_pod.replica_count`), not Kubernetes.
6. **The plan decides *which* apps; the *amount* is decided per app at the last moment**, right before that app's scale-down request is sent, because pauses take time and DSS may change counts.
7. **Pause all, then scale down all** (today's order): every needed pause is sent together; once every pause has completed, every needed scale-down is sent together, each app's count read from DMS right before sending. If no app needs a pause, scale-downs go out at once.
8. **The Deployment pod template is the source of truth for pod restarts.** Any workflow component (akst, rpa-gateway, wf-engine, wf-worker, aggregators) whose pod template changes (incl. env changes caused by app additions, workerType or timeout edits, the in-memory store switch) → scale down **all** workflow components one by one in safe deployment's order. Apps are still decided individually (not paused for this). A replica-count change with no template change never disturbs running pods.
10. **Helm chart / `--set` changes are not detected** (not in the generated config). They are rolled out with a hard deployment.
9. **No Jenkins guard in v1.**

## 4. End-to-end flows

### 4.1 Config generation API (workflow-ops `PipelineOps`)

```
rpc GenerateDeploymentConfig (GenerateDeploymentConfigRequest) returns (GenerateDeploymentConfigResponse);

GenerateDeploymentConfigRequest {
  environment, workflowName, variantName, tenantName, configVersion
  configRepoRef      // commit SHA or branch; empty = configured branch (CONFIG_REPO_BRANCH)
  mode               // DEPLOY | PREVIEW
  buildNumber, appType, eventType   // as the generator's argv today (DEPLOY)
}
GenerateDeploymentConfigResponse {
  success, message
  failureKind        // NONE | VALIDATION (generator exit 10) | GENERATION (exit 1)
  configRepoSha      // the commit actually used
  deploymentVersionId// DEPLOY only (minted by CreateDeploymentVersion)
  files[]            // {name, content}: config-values.yaml, masked-config-values.yaml,
                     //  resource-configurations.json, deployment-config-values.json
}
```

| | DEPLOY (Jenkins) | PREVIEW (safe deployment, dry run) |
|---|---|---|
| Reads config repo, SSM, DynamoDB, DMS, Kubernetes | yes | yes |
| `CreateDeploymentVersion` (mints version + schedule ids) | yes | **no** – placeholders |
| S3 upload of masked config | yes | **no** |
| Saves baseline in DMS | **yes**, against the minted version id | no |
| Output | files, same as today | files (in memory) |

How it runs:
- The generator code (`libs/deployment-config-generator`, the parts of `libs/common` it imports, `libs/proto/ss_deployment_mgmt.proto`) is **copied** into workflow-ops (`src/libs/config_generator/`). Its logic is unchanged apart from: a PREVIEW switch, explicit input/output directories, placeholder ids in PREVIEW, and one fix: the fixed-`replicaCount` path passes the active indices to `get_deployment_numbers` like the min/max path already does (`fb_worker.py:379` vs `:336`). Without it a graceful scale-down of 5→3 can stop pods 01–02 while Helm then deletes the still-running 04–05. The devops copy is deleted later, once every Jenkinsfile uses the API.
- It runs in a **separate process** per request with its own temp directory (it mutates the global YAML loader, depends on cwd and `sys.path`, prints to stdout). Called with `asyncio` subprocess so the event loop is not blocked.
- The config repo is fetched **at the commit** as a tarball (`GET /repos/{owner}/{repo}/tarball/{sha}`) and extracted into the temp dir as `config-repo/`. The generator needs the whole environment folder (credential-sharing checks scan every config in it). Extracted trees are cached per SHA on local disk (small LRU) since a safe deployment generates twice at the same SHA.
- Validation layer (other developer) runs after generation inside this API; on failure the response carries `failureKind=VALIDATION` and the message.

### 4.2 Baseline save (DEPLOY mode)

After a successful DEPLOY generation, workflow-ops builds the **baseline document** (§5) from the generated files and calls a new DMS RPC:

```
rpc SaveDeploymentState (SaveDeploymentStateRequest) returns (SaveDeploymentStateResponse);
SaveDeploymentStateRequest { deploymentVersionId, key, value }   // upsert into deployment_state
```
Key: `generated_config`. The minted version only becomes "active" after a successful deploy, so *active version → its `generated_config` row* is always the last successful deployment. A failed save is logged and does **not** fail the generation (next safe deployment simply runs Full).

Every deployment that goes through the API saves a baseline — hard and safe alike.

### 4.3 Safe deployment (flag on)

```
INIT            existing checks, DSS disable, trigger hold
                + resolve config branch head → config_repo_sha (stored; Jenkins param CONFIG_REPO_SHA)
CONFIG_FETCHED  existing deployment details
                + plan = SmartDeploymentPlanner.build_plan(...)        (§6)
                + persist plan; build pause / scale-down entries from it
                + NO_CHANGES → finalize_rollback (DSS on, triggers released), complete task, stop
DRAIN_APPS      (new state, replaces PAUSE_APPS + SCALE_DOWN_FB_WORKERS when the flag is on)
                send pauses for apps that need draining;
                when every pause has completed → per app, read the count from DMS → send all scale-downs together;
                on each scale-down response → record; when all entries are done → SCALE_DOWN_FB_WORKERS_COMPLETED
SCALE_DOWN      workflow components from deployment_hierarchy: FULL = today's list,
                PARTIAL = [] (or ["akst"] for the akst-only case)
JENKINS …       unchanged; Jenkins generates with CONFIG_REPO_SHA (DEPLOY) and deploys
RESUME          unchanged: only apps paused by WORKFLOW_OPS are resumed
```

Always on (no feature flag). A FULL plan behaves like today's safe deployment. A task already in flight at upgrade (no `deployment_plan`) finishes on today's path.

### 4.4 Hard deployment

Unchanged. Hard deployments started through workflow-ops use the same Jenkins job (`DEPLOYMENT_JENKINS_JOB_NAME`), so Jenkins calls the API in DEPLOY mode (branch head, no SHA) and the baseline is saved. Deployments started directly from other Jenkinsfiles save none (§9).

## 5. Baseline document

```json
{
  "schemaVersion": 1,
  "configRepoSha": "…",
  "services": [ /* config-values.yaml services, normalised, secrets fingerprinted */ ],
  "userConfig": { /* merged workflow config, secrets fingerprinted; scaling is read from fb_worker */ },
  "globalSettings": { /* non-service keys of config-values.yaml, secrets fingerprinted */ }
}
```

- **Fingerprinting (secrets only):** an env value or key whose name is secret (`is_secret`: the generator's `mask_sensitive_configs` names and suffixes, plus camelCase/other spellings such as `accessKey`, `secret`, `credentials`) becomes `hmac:<hex>` (HMAC-SHA256, key from env var `CONFIG_FINGERPRINT_KEY`); a secret JSON blob is fingerprinted per top-level key. Every other value stays readable; JSON-object env values are parsed into dicts so key order doesn't matter and the inner key that changed can be named.
- **Normalisation before fingerprinting** (values minted per deployment, so they would always look changed): deployment version id (top level, labels, task-reader `deploymentDetails.deploymentVersionId`), fb-worker `DEPLOYMENT_ID`, trigger schedule ids. Each is replaced with a constant on both sides. The exact field list is confirmed against the generator during implementation.
- `userConfig` (from `deployment-config-values.json`, sizing already applied) is kept whole, so DB-only changes (timeouts, schedule rows, credential sharing groups) prevent a false "no changes" and can be reported with values. `scaling` is read from its `fb_worker` entries.
- Rotating `CONFIG_FINGERPRINT_KEY` makes every secret look changed once (every app with a secret is drained on the next deployment). Acceptable; rotate rarely.

## 6. Shared library (workflow-ops `src/services/smart_deployment/`)

Pure logic with no I/O except `planner.py`. Names follow PEP 8; the dry-run developer builds on these.

| Module | Main names | Responsibility |
|---|---|---|
| `fingerprint.py` | `is_secret(name)`, `ConfigFingerprinter.mask_secrets(document)`, `.fingerprint_value(value)` | Fingerprint credentials and tokens only; secret JSON blobs per key |
| `baseline.py` | `DeploymentBaseline` (dataclass), `DeploymentBaseline.from_generated_files(files, fingerprinter)`, `.to_json()`, `.from_json()` | Build/serialise the baseline document (§5); normalisation |
| `change_detection.py` | `ComponentKind` (FB_APP, WORKFLOW, TASK_READER, CRONJOB), `ChangeType` (UNCHANGED, POD_TEMPLATE_CHANGED, ADDED, REMOVED), `ComponentChange` (name, kind, change_type, changed_fields), `ConfigChangeSet`, `ConfigChangeDetector.detect(baseline, candidate) -> ConfigChangeSet` | Per-component comparison. fb-worker copies are grouped into one app; pod template = everything except `replicaCount`, `deploymentIndex`, `dssEnabled`. `changed_fields` lists field/env names (never values). |
| `replica_policy.py` | `target_deployment_count(scaling_entry) -> int`, `calculate_scale_down_count(running, target) -> int` | Deployment count after deploy: credential sharing → `totalDeploymentCount` (what the generator actually creates; the rules doc's "sharedCount" is corrected here); min/max → `maxReplicas`; fixed → `replicaCount`. `max(0, running − target)`. For sharing apps this is in practice never > 0 (pool ≥ 50 deployments); DSS lowers the running count to the credential count after re-enable. |
| `impact.py` | `DeploymentMode` (FULL, PARTIAL, NO_CHANGES), `AppAction` (NONE, PAUSE_AND_DRAIN, SCALE_DOWN_EXCESS), `AppImpact`, `DeploymentImpact` (mode, fallback_reason, apps, workflow_components_to_scale_down), `DeploymentImpactAnalyzer.analyze(change_set, candidate_scaling) -> DeploymentImpact` | Applies the rules (§7) |
| `planner.py` | `SmartDeploymentPlanner.build_plan(deployment_details, config_repo_sha) -> DeploymentImpact` | I/O: PREVIEW generation, load baseline from DMS, detect, analyse. Any error → FULL with reason. |

Extension for the dry run: it reuses `ConfigChangeDetector`/`ConfigChangeSet` (which keep both baseline and candidate components, so new checks can read any field) and `calculate_scale_down_count` with the current DMS count; it adds its own business-change checks (schedules, workflow input, timeouts…) as new functions/classes without changing these.

## 7. Rules (safe deployment)

**Mode** (first match):

| # | Condition | Mode |
|---|---|---|
| 2 | No baseline, unreadable baseline, unknown `schemaVersion`, PREVIEW generation failed, SHA not resolved, any planner error | FULL (reason recorded) |
| 3 | An fb app is in the baseline but not in the candidate (removal/rename/type change) | FULL (defensive; validation should reject it) |
| 4 | (removed) | |
| 5 | (removed) | |
| 6 | Services identical (after normalisation), `userConfig` and global settings identical | NO_CHANGES |
| 7 | Otherwise | PARTIAL. Workflow scale-down list = today's full ordered list if any workflow component's pod template changed or a workflow component was added/removed, else `[]` |

**Per app in PARTIAL** (FULL = today's: every app paused if running, every pod drained):

| App change | Action | Pause | Scale-down amount (computed right before sending) |
|---|---|---|---|
| Pod template changed (image, env incl. platform-injected, resources, node selectors, workerType…) | PAUSE_AND_DRAIN | yes, if RUNNING (user-paused apps stay paused, as today) | all current pods (DMS `replica_count`) |
| Template unchanged (replica settings changed or not) | SCALE_DOWN_EXCESS | no | `max(0, current − target_deployment_count)`; 0 → nothing sent |
| Added | NONE | no | – |

task-reader (always restarts, stateless), CronJob/EventBridge triggers and added aggregators: no action.

## 8. State machine details (workflow-ops)

- **New state** `DRAIN_APPS_INITIATED` (handler module `handle_drain_apps_state.py`, class `AppDrainStateHandler`). Entered from CONFIG_FETCHED; exits to the existing `SCALE_DOWN_FB_WORKERS_COMPLETED`, so everything downstream is unchanged.
- Consumer messages in this state are routed by `action_payload.taskPayload.fbName`: `PAUSE_RESUME_APP` → pause response; `FbWorkerPodScaleDown` → scale-down response. Existing response parsing and version-conflict retries are reused.
- Scale-down entry fields: `scale_mode` (`ALL` | `EXCESS`), `target_deployment_count`, and, once sent, `original_replica_count` = the count actually requested (rollback re-enables exactly that many — workflow-ops `EnableDeployment` adds N zero-replica deployments).
- **Failure in DRAIN_APPS_INITIATED** (a pause or scale-down fails, or an exception): restore fb workers, resume apps paused by WORKFLOW_OPS, finalize (DSS on, triggers released), fail the task with stage `drain-apps`. The state is added to `ROLLBACK_ELIGIBLE_DEPLOYMENT_STATES`, the fb-scale-down rollback set, `failure_details` stage mapping and recovery/timeout handling.
- **Restart safety:** on STARTUP_LOADER in this state, entries whose pause completed but whose scale-down is still PENDING are sent.
- **NO_CHANGES:** `finalize_rollback`, then task completion reported as success with message "No changes to deploy". No Jenkins run.
- Plan persisted in `task_payload.deployment_plan` (mode, reason, per-app action, config_repo_sha) for visibility and restarts; never recomputed after CONFIG_FETCHED. Amounts are not in the plan.

## 9. Jenkins (devops-pipeline, non-prod `Jenkinsfile-safe-deployment`)

- New parameter `CONFIG_REPO_SHA` (default empty). `Checkout Config repo` checks out that commit when set (the `Insert Workflow API Details` stage still reads the checkout).
- `Generate Config Files` runs a small client (`libs/webhook/scripts/generate-deployment-config.py`, reusing the existing workflow-ops gRPC client setup) that calls `GenerateDeploymentConfig` (DEPLOY, ref = `CONFIG_REPO_SHA` or branch, build number) and writes the returned files where the old script left them: `libs/deployment-config-generator/configs-$BUILD_NUMBER/` and `./values/`. The webhook step (`libs/webhook/scripts/update-pipeline-completion.py:29`) reads `deployment-config-values.json` from the first path; without it `SaveDeploymentDetails` gets no metadata and the new version never becomes active. Exit code 10 on `VALIDATION`, 1 otherwise, so the existing `config-validation` / `config-generation` failure stages keep working.
- `pipeline_ops_v1.proto` copy in devops is kept identical to workflow-ops.
- Other Jenkinsfiles (prod, normal, onboarding) are unchanged in v1 and keep running the local generator; their deployments save no baseline, so the next safe deployment of those versions runs FULL.

## 10. Configuration (workflow-ops)

New env var (`.env.example` and all four `deployment-configs`): `CONFIG_FINGERPRINT_KEY` (secret). No feature flag: smart deployment is always on. The generator additionally needs what Jenkins gives it today (AWS keys, Kubernetes API server/token or in-cluster access, GitHub token, `DEPLOYMENT_MANAMGENT_SERVICE`); the existing workflow-ops equivalents are reused where they exist, the rest added the same way. Python dependencies the generator needs and workflow-ops lacks are added to `pyproject.toml`.

## 11. Testing

- Unit tests (unittest, file paths, existing patterns): fingerprint/normalisation, change detection (each rule row), replica policy (the examples table in the rules doc), impact analyser (every mode row), drain-state handler (pause→scale-down per app, immediate scale-down, failure rollback, restart recovery, message routing), CONFIG_FETCHED plan wiring, NO_CHANGES path, generation API (mode behaviour with the generator mocked at the subprocess boundary), baseline save.
- DMS: unit test for the new RPC (upsert).
- devops: client script test with a mocked stub.
- End-to-end on dev Kubernetes with the owner after deployment.

## 12. Left to the builder

Exact module layout inside `src/libs/config_generator/`, cache size, gRPC deadlines/message limits, log wording, and the precise normalisation field list (verified against generator output).

## 13. Builder's calls (made while writing; check while reading)

1. Fixed-`replicaCount` generator fix (§4.1), applied to the workflow-ops copy only. It also makes hard deployments keep running pods first.
2. Credential-sharing target = `totalDeploymentCount`, not `sharedCount` (§6).
3. No fallback to the existing `deployment_state` `<configVersion>` row (it is lossy and differently shaped). A version without `generated_config` runs FULL once.
4. Excess scale-downs on unpaused apps wait behind that app's queued work (same as Scale App Worker today). No extra timeout: the existing overall deployment timeout bounds it.
5. A failed baseline save does not fail the deployment.
6. No feature flag (owner): always on; FULL is the safe fallback.
7. The Jenkins client keeps the old output paths (§9).

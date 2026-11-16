---
type: runtime-flow
name: Safe deployment - what it does to DSS and to shared credential groups
repo: e5-platform-ss-workflow-ops
branch: release/v1.7.4
commit: eb9acd0
provenance: code
verified: 2026-11-13
prose_reviewed_at_commit: eb9acd0
deployed_in_qa: v1.6.3
deployment_lag: release/v1.7.4 available, v1.6.3 running
---

> **This describes `release/v1.7.4`; QA was last observed running `v1.6.3`.** That
> observation is the qa cluster snapshot of **2026-10-02**, six weeks old, and it predates
> both changes this card is about - PR #94 (2026-10-29) and PR #100 (2026-11-10). So as last
> observed, **QA was not running the DSS-and-credential-group cycling below.** Whether it is
> now cannot be checked from this machine: there is no QA kubeconfig here.

# Safe deployment

A safe deployment is a workflow-ops state machine. This card records the part nobody had
written down: **what it does to dynamic scaling and to shared credential groups**, and
therefore to `cds`. Everything below marked *code* is cited at workflow-ops `release/v1.7.4`;
everything marked *owner* is a rule stated by the platform owner and not yet traced in code.

For the change to *which* components a safe deployment touches, see
[smart-safe-deployment](smart-safe-deployment.md) - that is **in-flight design, not current
behaviour**.

## The state handlers

`src/services/deployment_action/`: `handle_init_state.py`, `handle_config_fetched_state.py`,
`handle_pause_apps_state.py`, `handle_scale_down_state.py`, `handle_jenkins_state.py`,
`handle_new_config_fetched_state.py`, `handle_resume_state.py`,
`handle_task_completion_state.py`.

Today's flow, as the design document describes it: **every** running app is paused, **every**
fb-worker pod is drained and **every** workflow component is scaled down, whatever changed.

## DSS and credential groups are switched off for the duration *(code)*

| When | Site | What happens |
|---|---|---|
| INIT | `handle_init_state.py:150` | `disable_dss_for_deployment(deployment_version_id, record=record)` |
| | `dss_disable.py:69` | each of the deployment's shared credential groups is set **inactive** (`set_shared_credential_group_active_async(group_name, False)`) |
| | `dss_disable.py:94` | the deployment's DSS subscription is disabled |
| Completion | `handle_task_completion_state.py:56`, `:77` | `rollback.enable_dss_after_deploy(...)` restores what INIT recorded |
| | `deployment_rollback.py:330` | the groups are set **active** again |
| | `deployment_rollback.py:362` | DSS is re-enabled for the **new** deployment version |

Two properties worth knowing:

- **INIT's disable is never left in place.** If the deployment succeeded but bookkeeping
  failed, the handler restores DSS and the groups with the id the task already holds, then
  re-raises (`handle_task_completion_state.py:46-57`).
- **With no new version id, the groups are still reactivated** - only the new version's DSS
  cannot be enabled (`:75-76`).

## Why this matters to CDS

deployment-mgmt's `GetSharedCredentialGroupMembers` omits inactive groups, and `cds` reloads
membership every 60s. So once INIT deactivates a group, **CDS stops rationing it within a
minute and resumes after completion** - the "deactivate in deployment-mgmt first" order CDS
documents for its eviction RPC, reached without calling that RPC (which nothing calls; see
cds-eviction-rpc-has-no-caller (archived)).

During that window `max_replicas` keeps whatever CDS last wrote.

## Rules stated by the owner *(owner, 2026-11-11 and 2026-11-13; not yet traced in code)*

- **Raising** a deployment count mid-deployment does not disturb running pods - it only adds
  pods.
- **Lowering** it below the running count must be done by draining pods gracefully; killing a
  worker abruptly can lose its in-flight task and the data with it.
- The replica count DSS had chosen does **not** need preserving across a deployment. Once DSS
  is re-enabled it re-adjusts to the task volume.
- Workflow components do not need apps paused before they restart: **Temporal carries
  in-flight workflow activities** across the restart.
- `task-reader` runs only when DSS is enabled. If DSS is disabled it is not brought up; if it
  is enabled, task-reader is deployed.
- Dynamic scaling comes in two kinds: **task-based** and **credential-based**. The owner
  named the two; mapping them onto the estate is this card's reading, not the owner's words:
  task-based is `dynamic-scaling` sizing to queue depth, credential-based is a shared
  credential group whose ceiling `cds` sets.

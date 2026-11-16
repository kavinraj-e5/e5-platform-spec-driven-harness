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

# Smart Deployment Rules

# Smart Safe Deployment Rules

Smart safe deployment reduces deployment disruption by applying safe-deployment actions only to the components whose configuration changed. For each component, the plan decides whether to pause, drain, gracefully scale down, restart without drain, or leave it unchanged. The existing full safe flow remains the fallback whenever the system cannot safely determine a targeted action.

## 1. Key Terms

| Term | Meaning |
| --- | --- |
| **A** | Live active pod count of an app. This is read from Kubernetes after DSS is disabled and includes Deployments with replicas greater than or equal to 1. |
| **D new** | Number of Deployments the app will have after the deployment. This comes from max in min/max mode, replicaCount in fixed mode, or sharedCount in credential-sharing mode. |
| **scaleDownCount** | `max(0, A − D new)`. This is the number of running pods that Helm would otherwise terminate abruptly. |
| **Full** | The current full safe flow: pause all apps, drain all fb-workers, scale down the workflow plane, deploy, and resume all. |
| **Partial** | A targeted flow that pauses and drains only affected apps. The workflow plane is not touched. |
| **No changes** | Nothing differs from the baseline. Jenkins does not run and nothing is paused. |

## 3. What Restarts a Pod

| Restarts running pods | Does not restart running pods |
| --- | --- |
| Image changes, including registry, repository, tag, and pull policy. | Replica count changes. These are scale events; see the replica rules. |
| Any environment value, including SSM, DynamoDB, and Langfuse values. | Deployment metadata labels, such as deployment-version-id and version. |
| Resources, nodeSelectors, and security contexts. | Service objects. |
| Rename, type change, or configVersion change, because they result in delete and create. | CronJob spec changes, which affect only future Jobs. |
| Chart template edits. | EventBridge schedules and DB-only values, such as user\_config, timeouts, and CCS keys. |

## 4. fb-worker and nonrpa-worker Rules in Partial Mode

In Partial mode, each affected app is evaluated independently. Untouched apps are marked **SKIP** with `paused_by = NOT_PAUSED`. Resume and rollback do not touch skipped apps.

| Change for the app | Pause | Drain or scale down | Resume |
| --- | --- | --- | --- |
| No change | No | No | No |
| Pod template change and `A > 0` | **Yes** | **Drain all pods** | **Yes** |
| Pod template change and `A = 0` | No | No | No |
| Replica-only change with `scaleDownCount = 0` | No | No | No |
| Replica-only change with `scaleDownCount > 0` | No | **Gracefully scale down scaleDownCount pods** | No |
| Pod template change and replica change | **Yes** | **Drain all pods**, covering the count change | **Yes** |
| App added | No | No | No |
| App removed | **Yes** | **Drain all pods** | **No**, because the app no longer exists |
| Worker type change or app rename | Old name: yes; new name: no | Old name: drain all | No |
| Only DB values changed, such as min within A, timeouts, or user\_config | No | No | No |

## 5. Replica Rules

- A replica-only change never needs a pause because no pod template changes and no surviving pod restarts.
- Increases have no impact on running pods. Existing pods continue running and new pods start.
- Only a decrease below the running count matters. If `A > D new`, gracefully scale down `A − D new` pods before Jenkins runs.
- The DSS-chosen running count is not preserved. After DSS is re-enabled for the new version, it adjusts the count based on task volume.
- Always use the live **A**, even if min/max did not change, because A can already be above max.

### Replica Examples

| Example | scaleDownCount | Safe action |
| --- | --- | --- |
| 5/10 → min 7, A=7 | 0 | None; DB only |
| 5/10 → min 9, A=7 | 0 | None; 2 pods start |
| 5/10 → min 3, A=7 | 0 | None; DSS scales down later if load allows |
| 5/10 → max 12, A=7 | 0 | None; idle Deployments added |
| 5/10 → max 8, A=7 | 0 | None; 2 idle Deployments removed |
| 5/10 → max 6, A=7 | 1 | Scale down 1, no pause |
| 5/10 → 2/4, A=7 | 3 | Scale down 3, no pause |
| 5/6 unchanged, A=7 already above max | 1 | Scale down 1, no pause |
| replicaCount 5 → 3 | 2 | Scale down 2, no pause |
| max/replicaCount → 0, app disabled | A | Scale down all, no pause |

**Order requirement:** the scale-down must complete before Jenkins generates the config. This ensures the generator sees `A = D new` and deletes only idle Deployments.

## 6. Other Component Rules

| Component | Change | Safe action |
| --- | --- | --- |
| task-reader | Always changes because deploymentVersionId is in env | Restart, no drain because it is stateless |
| task-reader | Added or removed | No drain. Check the akst rule if the in-memory store toggled. |
| CronJob triggers | Cron expression, suspend, WORKFLOW\_INPUT, trigger added, or trigger removed | No drain |
| EventBridge schedules | Any change | No drain |
| Aggregator | Added | No drain |
| akst, wf-engine, wf-worker, aggregator | Changed | **Full** in v1 |
| akst `inMemoryTaskStoreEnabled` | Flipped by a min/max edit | **Only akst** |

## 7. DSS Rules

| Rule | Detail |
| --- | --- |
| DSS off | DSS is already disabled at INIT for the deployment version and all shared-group members, matching existing behaviour. |
| Wait before reading A | Wait until the version has no PENDING or IN\_PROGRESS `dynamic_scaling_request` and no active ENABLE\_DEPLOYMENT, DISABLE\_DEPLOYMENT, or SCALE\_APP\_WORKER state. Timeout is 300 seconds; after timeout, use **Full**. |
| Success | The new version's subscription is created by DMS `PopulateDssSubscription`, matching existing behaviour. |
| Rollback or failure | DSS is re-enabled for the current version through existing `finalize_rollback`. |
| No changes | No new version is created, so DSS must be re-enabled for the current version through `finalize_rollback`. |

## 8. Plan Rules

| Rule | Detail |
| --- | --- |
| Decide once | The plan is computed at CONFIG\_FETCHED, persisted, and never recomputed after pausing starts. It survives a workflow-ops restart. |
| Pin the config | The config repo commit SHA is pinned in the plan and passed to Jenkins, so branch movement during the flow has no effect. |
| Live state wins | Live Kubernetes count is the source of truth. A mismatch with `fb_worker_pod.replica_count` is logged as drift. |
| No secrets | Baseline and plan store only digests and field names. They never store environment values. |

## 9. Jenkins Guard

In Partial mode only, a Jenkins guard runs immediately before `helm upgrade`. It compares `helm template` with the live manifest.

The deployment must abort with failure stage `smart-plan-guard` if either of the following is true:

- A Deployment outside the allowed set would restart or be deleted. The allowed set includes drained apps, task-reader, CronJobs, and added components.
- An active Deployment would be deleted.

An abort triggers the normal rollback. This safety net catches drift, manual edits, and SSM changes that occur between planning and deployment. The guard is skipped in Full mode.

## 10. Rollback Rules in Partial Mode

| Item | Rollback action |
| --- | --- |
| Paused apps | Resume only apps with `paused_by = WORKFLOW_OPS`. |
| SKIP apps | Never touched. |
| Drained or scaled-down pods | Re-enable using the recorded count, `original_replica_count`. |
| Workflow plane | Scaled back up only if it was scaled down in Full mode. |
| DSS | Re-enabled using the existing flow. |

## 11. Hard vs Smart Safe at a Glance

| Situation | Hard deployment | Smart safe deployment |
| --- | --- | --- |
| Nothing changed | Skip Jenkins optionally | Skip, no pause |
| App pod template change | App pods restart abruptly | Pause, drain, deploy, and resume that app only |
| Max decreases below running pods | Helm kills excess pods abruptly | Graceful scale-down, no pause |
| Min/max increases | Scale up | Scale up, no pause |
| Workflow-plane change | Affected pods restart | Full safe flow |
| Global change | Affected pods restart | Full safe flow |
| Triggers, schedules, or DB-only values | No drain | No drain |
| Guard | No | Yes |

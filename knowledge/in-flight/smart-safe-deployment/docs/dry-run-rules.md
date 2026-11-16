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

# Dry run rules

# Deployment Dry Run – What We Report

## 1. Purpose

A dry run tells a workflow engineer, **before** they deploy, two things:

1. **Impact**: which apps will be paused, which running pods will be restarted or scaled down (out of how many running), and whether workflow services restart.
2. **What changed**: the changes this deployment carries, in the terms they work with: their workflow, their apps, scaling, credential pool (CDS) and schedules.

The impact is reported **once**, not per mode. Engineers already know that Safe deployment stops pods gracefully (after the current task) and Hard deployment stops them abruptly. Based on the report they choose Safe or Hard.

Nothing is deployed, paused or changed by a dry run.

## 2. Audience and language

The reader is a workflow engineer. They know their **workflow**, their **apps**, **min/max pods** and auto-scaling, their **CDS credential pool subscription** and their **schedules**. The platform is a black box to them, like an AWS service.

| We say | Never say | Covers (for the product owner only) |
| --- | --- | --- |
| Workflow services | wf-engine, wf-worker, aggregator, akst, rpa-gateway, task-reader | Everything the platform runs to execute the workflow |
| App | fb-worker, nonrpa-worker, Deployment | An app the engineer built |
| Pods | Deployment index, replica | Running copies of an app |
| Workflow version | Platform variant / platform-core image | The workflow image |
| Credential pool (CDS) | Shared credential group, sharedCount internals | The CDS subscription |
| Schedules | CronJob, EventBridge | When the workflow runs automatically |
| Platform-managed setting | SSM, DynamoDB, chart, Helm | Values the platform supplies, not the engineer |

## 3. Impact rules – apps

| Case (what changed for the app) | Reported impact |
| --- | --- |
| Nothing changed | Not affected |
| App version (image) or app settings changed, app has running pods | **App will be paused.** X of X running pods will be restarted. Comes back with min pods, then auto-scales. |
| App version or settings changed, no running pods | Not paused, no running pods affected. New pods start with the new version. |
| Max pods lowered below the running count | Not paused. **Y of X running pods will be scaled down.** |
| Max pods lowered but still at or above the running count | Not affected (only idle capacity removed). |
| Min pods raised | Not paused. Z more pods will start. |
| Min pods lowered, or max pods raised | Not affected. New limits apply after deployment. |
| Fixed pod count lowered | Not paused. **Y of X running pods will be scaled down.** |
| App version change **and** pod limit change | **App will be paused.** X of X running pods will be restarted. Comes back with the new min. |
| App added | New app. Z pods will start. |
| App removed | **App will be paused and removed.** X of X running pods will be stopped. |
| App moved to another credential pool with different pool credentials | **App will be paused.** X of X running pods will be restarted (credentials change). |
| App moved to another credential pool with the same credentials | Not affected. |

## 4. Impact rules – workflow services

| Case | Reported impact |
| --- | --- |
| Workflow version changed | **Workflow services will restart.** Running workflow steps are retried automatically. Apps are not paused for this. |
| Workflow settings changed (workflow environment settings) | **The affected workflow services will restart.** Running workflow steps are retried automatically. |
| Only timeouts or other stored workflow settings changed | Not affected (recorded only). |
| Auto-scaling or credential pool turned on for the first app, or off for the last app | **Workflow services will restart** (the platform switches its task handling). |
| Only app, scaling, credential pool or schedule changes | Not affected. |

## 5. What changed – business change detection

Values are shown where they are safe to show. Secret values are never shown, only the name of the setting.

| Change | Report message (example) | Values shown |
| --- | --- | --- |
| App image changed | App **Invoice-Bot** version changes from **2.4.1** to **2.5.0** | Old → new version |
| Workflow image changed | Workflow version changes from **1.8.2** to **1.9.0** | Old → new version |
| App environment changed (by the engineer) | App **Invoice-Bot**: settings changed: `API_URL`, `RETRY_LIMIT` | Names only |
| App environment changed (by the platform) | App **Invoice-Bot**: platform-managed settings changed (e.g. a credential was rotated) | Nothing |
| App CPU / memory / placement changed | App **Invoice-Bot**: resources changed | Old → new CPU / memory |
| Workflow settings changed | Workflow settings changed: `WORKER_MODE`, `WORKFLOW_INPUT` | Names only |
| Timeouts / stored settings changed | Task timeout of **Invoice-Bot** changes from **30 min** to **45 min** | Old → new |
| Pod limits changed | App **Invoice-Bot**: min/max pods change from **5/10** to **5/6** | Old → new |
| App added / removed | App **Claims-Bot** added / removed | Name |
| CDS subscription: app joins a new credential pool | App **Invoice-Bot** joins credential pool **pool-b** | Pool name |
| CDS subscription: app leaves a credential pool | App **Invoice-Bot** leaves credential pool **pool-a** | Pool name |
| CDS subscription: pool size changed | Credential pool **pool-a** size changes from **20** to **30** | Old → new |
| Schedule added / removed | Schedule **daily-0600** added / removed | Schedule name, timing |
| Schedule timing changed | Schedule **daily-0600** timing changes from `0 6 * * *` to `0 7 * * *` | Old → new timing |
| Schedule paused / resumed | Schedule **daily-0600** will be paused / resumed | – |
| Schedule input changed | Schedule **daily-0600**: input changed | Nothing (may contain data) |
| Nothing changed | No changes. Nothing will be deployed. | – |

## 6.  report

**Summary:** 1 app paused. 5 of 13 running pods affected (4 restarted, 1 scaled down). Workflow services not affected.

### Impact

| Item | Running pods | Impact |
| --- | --- | --- |
| Workflow services | – | Not affected |
| Invoice-Bot | 4 | App will be paused. 4 of 4 running pods will be restarted. |
| Claims-Bot | 7 | Not paused. 1 of 7 running pods will be scaled down. |
| Reports-Bot | 2 | Not affected |

### What changed

- App **Invoice-Bot** version changes from **2.4.1** to **2.5.0**.
- App **Claims-Bot** min/max pods change from **5/10** to **5/6**.
- Schedule **daily-0600** timing changes from `0 6 * * *` to `0 7 * * *`.

### During the deployment

- Auto-scaling is paused during the deployment.
- Scheduled runs due during the deployment are held and run afterwards.

## 7. What we never report

| Item | Why |
| --- | --- |
| Secret values (tokens, passwords, keys) | Security. Only the setting name is shown. |
| Internal platform component names | Not meaningful to the user |
| Values of schedule input | May contain business data. |

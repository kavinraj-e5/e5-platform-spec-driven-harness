---
type: context
name: Workflows, apps and function blocks - what they are and how they are built
provenance: owner-doc, checked against code where marked
sources: docs/important_context_doc/platform_whole_context.md, Platform Architecture.md (owner)
verified: 2026-11-14
---

# Workflows, apps and function blocks

## The three ideas
- **Function block (FB):** one small business outcome, done by RPA. Examples: write
  coordination notes in an EHR, extract a patient report, download PCR reports. An FB is
  split further into functions, modules and screens, so the parts can be reused across FBs.
- **App:** the RPA automation for one EHR, written in **Robot Framework**. It holds many
  function blocks. It runs separately, as an **fb-worker** pod.
- **Workflow:** the business orchestration. It stitches function blocks together into a
  business flow and holds the business logic. It does no RPA itself: it **delegates** each
  FB to the app, and the app does the automation and returns the result.

The owner's analogy: in a food-ordering app, "add to cart", "choose food", "make payment"
and "download invoice" would each be a function block, and the workflow is the order
journey that calls them.

## What the platform provides for each
- **Workflows: `platform-core`**, the workflow SDK. It is built on the **Temporal Java SDK**
  (verified: platform-core calls Temporal).
  - It provides blocks, each a wrapper around a Temporal activity: start, end, decision,
    transformation, function, intervention, container and wait. The full list, with code
    citations, is in [platform-core](../workflow-development/workflow-library/platform-core.md#block-taxonomy).
  - It also provides enforced configs and state, so workflow data persists.
  - Blocks exist so that each one can be **tested on its own in a sandbox**.
  - Each block **emits events** (wf-events) describing what happened. The **aggregator**
    listens to them, works out what the workflow did, and sends the transaction details on
    to Automation Hub and Neos. Verified: the aggregator targets are AutoHub, FreshDesk,
    Email and Whitelabel, and its store defaults to DynamoDB.
- **Apps: `rf-fb-worker-core`**, the Python base library. It does the technical heavy
  lifting (poll loop, AKST pull, `TaskComplete` reply, Citrix helpers) and gives the app
  team its keywords.
- **The aim of both** is to hide technical complexity, so the workflow and app teams focus
  only on business orchestration and automation (philosophy rule 7).

## How a workflow is put together (the repo chain)
| Repo | Role |
|---|---|
| `e5-platform-core` | the SDK workflows are written with |
| `wf-scaffolding` | adds platform-core as a library and fixes the folder structure a workflow is written in; it is the seed project for a new workflow |
| `app-lib-scaffolding` (AppSDK) | the contract of an app's function blocks; attached to wf-scaffolding as a library |
| `variant-scaffolding` | the **final deployable workflow**: bundles the workflow and is deployed to the environments |

Verified from build files (see `dependencies.md`): variant-scaffolding depends on
e5-platform-core, AppSDK and the workflow, and wf-scaffolding depends on e5-platform-core and
AppSDK.

## How an app is put together
An fb-worker image is `rf-fb-base-image` (the Citrix runtime), plus `rf-fb-worker-core`, plus
the app's own Robot Framework source. One image per app.
- Verified: customer app Dockerfiles build FROM `e5-rf-fb-base:v4.0.0`, and pin
  `e5_rf_fb_worker_core`.
- **On startup** an fb-worker logs in to the EHR with a credential, if the app needs one,
  then processes tasks. It logs out when it is killed (owner statement). Credentials come
  from the Resource Negotiator's pool (see [company and teams](company-and-teams.md)).

---
type: initiative
name: Smart safe deployment (and the dry run)
state: in-flight
provenance: design-doc
verified: 2026-11-13
---

# Smart safe deployment

> **None of this is platform behaviour yet.** As of 2026-11-13 the implementation is
> "implemented and unit-tested, **not committed**; the end-to-end run on dev Kubernetes is
> still pending" (implementation report, §header). For what a safe deployment does today, see
> [safe-deployment](safe-deployment.md).

## Three things that are easy to confuse

| State | What | Where it is |
|---|---|---|
| **Released** | CDS `release/v1.0.2`; the shared-credential-group plumbing in deployment-mgmt, interface and workflow-ops; **CDS-aware safe deployment** - workflow-ops PR #94 "update safe deployment according to cds" (2026-10-29), refined by #100 "keep DSS and CDS groups in step through deploy" (2026-11-10), and deployment-mgmt PR #290 "get the shared group details" (2026-10-29) | release branches - see [safe-deployment](safe-deployment.md) and [cds](../../subsystems/cds.md) |
| **Unreleased** | workflow-manager `feat/cds-safe-deployment` (2026-10-22): a `getCredentialSharing` RPC and a `CredentialSharingResolver`, absent from `release/v1.4.7`. Nothing released calls it - workflow-ops resolves credential sharing itself from workflow-config (`src/libs/config_repo.py:375`) - so if it lands it is a **second implementation** of the same resolution | that branch only |
| **Uncommitted** | Smart safe deployment, below | a local working copy only; planned branch `feat/smart-deployment` in workflow-ops, devops-pipeline and deployment-mgmt |

> **How the first two rows were told apart.** The `feat/cds-safe-deployment` branches are *not*
> ancestors of their release branches, which reads as "unreleased" - and was, in the first
> draft of this card. They were **squash-merged**: the work is in the release, the branch's own
> commits are not. The test that works is whether the branch's *content* is in the release.

## Intent

Safe deployment today pauses every app, drains every fb-worker pod and scales down every
workflow component, whatever changed. Smart safe deployment compares the configuration being
deployed with what is running, and pauses, drains or scales down **only what the change
affects**. Anything it cannot reason about safely falls back to today's full flow.

## Decisions taken with the owner

1. **The baseline is the generated final config** - the values Helm receives - saved per
   deployment version, so platform-injected values (SSM, DynamoDB) are covered.
2. **No secrets at rest.** Credentials and tokens are replaced with keyed HMAC-SHA256
   fingerprints; everything else stays readable so that, for example, a credential sharing
   group change (`groupName`, `sharedCount`, `enabled`) can be reported old-to-new. Hashing
   every value was rejected for exactly that reason.
3. **Config generation moves into workflow-ops as an API.** Jenkins calls it to deploy; safe
   deployment and the dry run call it in a no-writes mode. It is expected to move to its own
   service later. A validation layer runs after generation.
4. **One commit SHA per safe deployment**, resolved once at the start.
5. **Running pod count comes from deployment-mgmt** (`fb_worker_pod.replica_count`), not
   Kubernetes.
6. **The plan decides which apps; the amount is decided per app at the last moment**,
   because pauses take time and DSS may change counts meanwhile.
7. **Pause all, then scale down all** - today's order.
8. **The Deployment pod template is the source of truth for restarts.** Any change to a
   workflow component's template scales down all workflow components one by one in safe
   deployment's order. A replica-count change with no template change never disturbs running
   pods.
9. **No Jenkins guard before `helm upgrade` in v1.**
10. **Helm chart and `--set` changes are not detected**; they go out with a hard deployment.

## The rules, in one table

| Change | Result |
|---|---|
| No baseline, unreadable baseline, unknown schema, generation failed, SHA unresolved, planner error | **FULL** - today's flow, reason recorded |
| An fb app removed, renamed or retyped | **FULL** (validation should already reject it) |
| Services, `userConfig` and global settings identical | **NO_CHANGES** |
| Otherwise | **PARTIAL** |
| PARTIAL: an app's pod template changed | pause if running, drain all its pods |
| PARTIAL: template unchanged, replica settings changed | scale down `max(0, current - target)`, no pause |
| PARTIAL: app added | nothing |
| PARTIAL: a workflow component's template changed | scale down all workflow components, in order |

## The dry run is a separate feature

Owned by another developer. It reuses the same comparison library and **adds** business-change
checks - schedule changes, workflow input changes, and similar - so it is not "safe deployment
without the deploy". It reports impact so a user can choose hard or safe: the user already
knows safe is always graceful, so the report says *"4 pods will be scaled down abruptly"* and
*"app X will be paused"* rather than distinguishing the modes. For now dry run and deployment
run separately and exchange no SHA.

## The documents

Copied unchanged, stamped `status: in-flight`, and excluded from citation checking because
their code references point at the uncommitted working copy:

- [Implementation design](smart-safe-deployment/2026-11-13-smart-safe-deployment-design.md)
- [Implementation report and code walkthrough](smart-safe-deployment/smart-safe-deployment-implementation-report.md)
- [Smart deployment rules](smart-safe-deployment/smart-deployment-rules.md)
- [Dry run rules](smart-safe-deployment/dry-run-rules.md)
- [Deployment pipeline validation reference](smart-safe-deployment/deployment-pipeline-validation-reference-and-configuration-checks.md)

Earlier drafts, superseded by the above, remain in `~/Proj/platform/Understandwebhoook/docs/`:
the proposal, development plan, HLD/LLD, case analysis, the Helm primer and the November 6-12
rules drafts.

## One correction to the design document

It says workflow-ops should branch from `fix/group-count-validation` because that branch
"contains the DSS disable at INIT". **The released `release/v1.7.4` already contains it**, in
a newer form: `handle_init_state.py:150` calls
`disable_dss_for_deployment(deployment_version_id, record=record)`, where the fix branch's
`:129` omits `record`. Branching from the fix branch would pick up the older version.

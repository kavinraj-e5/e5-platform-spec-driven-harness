---
type: context
name: What the subsystems are for, who uses them, and how they fit around a running workflow
provenance: owner-doc and owner statements, checked against code where marked
sources: docs/important_context_doc/subsystem_context.md, platform_whole_context.md, owner statements in session 2026-11-14
verified: 2026-11-14
---

# Why subsystems exist

A deployed workflow runs inside its own namespace: WF Engine, WF Worker, AKST, aggregator
and the fb-workers. That alone cannot be operated. Someone has to:
- deploy and redeploy it
- pause, resume or terminate it
- enable or disable its triggers
- scale its workers
- watch its health and heal it
- record what happened
- tell customers

The **subsystems** are the services that do this for every workflow. They are deployed once
per environment, outside any workflow (workflow-interactions in the catalog). They are the
third capability of the platform: interacting with running workflows and apps.

## Who calls them
- **Neos and Automation Hub** call `interface`, the only front door (GraphQL). Support uses
  Neos, so support also reaches the subsystems only through `interface`.
- **External systems** start workflows through `workflow-api` (REST).
- The **subsystems call each other**, through tasks queued in AKST and through gRPC.

## What each one is for
| Need | Subsystem |
|---|---|
| a front door for users and Neos | `interface` (GraphQL; records each task in DMS and sends it to AKST) |
| persisting state for all subsystems | `deployment-mgmt` (DMS): the central database, served over gRPC |
| lifecycle of running workflows and apps | `workflow-manager` (orchestrator) |
| infrastructure: pods, Jenkins deployments, AWS, trigger schedules | `workflow-ops` (orchestrator; the only service that changes infrastructure) |
| health and self-heal | `monitoring-service` (orchestrator) |
| autoscaling fb-workers from the queued tasks | `dynamic-scaling` (DSS): decides, then sends `ENABLE_DEPLOYMENT` / `DISABLE_DEPLOYMENT` to workflow-manager |
| distributing shared EHR credentials | `cds` (sets `max_replicas` in DMS, which caps DSS); `ccs` holds the credentials; `credential-monitor` watches assignment |
| platform events | `event-store` (ingests and forwards) and `event-processor` (matches events to webhook subscriptions) |
| the HIPAA audit log | `audit-service` |
| customer webhooks for workflow-api | `webhook-service` |

Only three subsystems orchestrate: they hand sub-tasks to other subsystems and resume on the
results. They are workflow-manager, workflow-ops and monitoring-service (owner). Their task
paths are in `../flows/`.

## Where the source documents and the code disagree
Both corrections below are verified in code (`evals/reverse-test-2026-11-14.md`):
- **`subsystem_context.md` draws `event-processor -> webhook-service`.** That edge does not
  exist. There are two separate chains:
  - event-store -> event-processor -> the webhook-dispatcher Lambda
  - workflow-api -> webhook-service
- **DSS is described as "AKST metrics -> db-mgmt".** It also creates the scaling tasks
  itself: it records each scaling request in DMS (`createDssTask`), then sends the
  workflow-manager task to AKST.

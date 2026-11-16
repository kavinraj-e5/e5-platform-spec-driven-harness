---
type: context
name: E5, what it automates, and the teams that build and use the platform
provenance: owner-doc
sources: docs/important_context_doc/platform_whole_context.md (owner, the platform team)
verified: 2026-11-14 (owner statements; nothing here is derivable from code)
---

# E5 and its teams

## What E5 does
E5 automates **post-acute healthcare** work in the US with RPA. People used to process
patient records by hand in each client's EHR. E5 replaces that manual work with automation.

- **Processes automated:** RCD, Authorization, OASIS to iQIES, eligibility, and more.
- **Clients:** home-health and hospice providers, e.g. BrightSpring, Elara, Amedisys.
- **EHRs automated:** Homecare Homebase (HCHB, through Citrix), WellSky, Waystar, and others.
  Each one is an app (see [workflows and apps](workflows-and-apps.md)).

**The goal:** run automation reliably, with clear monitoring, reporting, and a UI (Neos) for
interacting with running workflows. E5 serves clients and also builds automation products.

## The teams

| Team | What it does | What it uses from the platform |
|---|---|---|
| **Platform team** (the users of this harness) | Builds the tools for building automation: the SDKs and frameworks for workflows and apps, and the subsystems that deploy, monitor and interact with running workflows | everything in this knowledge base |
| **DevOps team** | Infrastructure, deployments, pipelines | `devops-pipeline`, `workflow-config` ([platform-delivery](../platform-delivery/README.md)) |
| **Workflow team** | Builds **workflows**: the business orchestration of an automation | the workflow SDK, `platform-core` ([workflow library](../workflow-development/workflow-library/README.md)) |
| **App team** | Builds **function blocks**: the RPA automation itself, in Robot Framework | `rf-fb-worker-core` and its keywords ([app library](../app-development/app-library/README.md)) |
| **Credential management** | Owns the EHR credentials automations log in with. The **Resource Negotiator (RN)** holds a credential pool and exposes an API that distributes credentials to many pods | `ccs`, `cds`, `credential-monitor` ([subsystems](../subsystems/README.md)) |
| **Automation Hub and Neos** | Reporting: per client and workflow, which transactions ran, succeeded, or failed and why. Also workflow interaction: stop or pause a workflow, deploy, enable or disable triggers | the subsystems, through `interface` (GraphQL) |
| **Support** | Monitor runs, respond to alerts, pause workflows, enable or disable triggers. They use Neos, which calls the subsystems; they do not call the subsystem API directly | Neos, then `interface` |

## Why this matters for a platform change
The platform team's users are the other teams. A platform change is judged by what it does
to **their** experience:
- the workflow and app teams must not need to know GraphQL, gRPC, Kafka or SQL
  (philosophy rule 7)
- Neos and support see the platform only through `interface`
- DevOps sees it through the pipeline and config

Name the team a change is for before designing it. That is the UX gate's first question.

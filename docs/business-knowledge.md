# Business knowledge

The detailed pages live in `knowledge/context/` because the harness reads them at the start of
every task. This page is the map. They are written from the platform team's own documents and
checked against code where marked.

| Read | To learn |
|---|---|
| [company and teams](../knowledge/context/company-and-teams.md) | what E5 automates (post-acute healthcare: RCD, authorization, OASIS to iQIES, eligibility), its clients and EHRs, and the seven teams that build or use the platform |
| [workflows and apps](../knowledge/context/workflows-and-apps.md) | function block vs app vs workflow, what the platform provides for each, and the repo chain from SDK to deployable workflow |
| [how a workflow runs](../knowledge/context/how-a-workflow-runs.md) | deploy pipeline, the 12 steps of a run, who owns which data, self-heal |
| [why subsystems exist](../knowledge/context/why-subsystems.md) | what each subsystem is for, who calls it, and where the source documents and the code disagree |

## The ideas to keep in your head
- **The platform's users are other teams** (workflow, app, DevOps, credential management,
  Neos and support). A change is judged by what it does to *their* experience, so a design
  starts by naming the team and freezing the UX.
- **Three capabilities:** build workflows (workflow library), build apps (app library,
  ui-synthesizer), interact with running workflows and apps (subsystems).
- **Seven rules** ([philosophy](../knowledge/constitution/philosophy.md)): event-driven over
  Kafka; one task protocol (v2); AKST is a prioritised mutable queue; roles before services;
  UX first; no shortcuts; hide GraphQL/gRPC/Kafka/SQL from workflow and app teams.
- **Who owns what:** [ownership](../knowledge/constitution/ownership.md) and
  [glossary](../knowledge/constitution/glossary.md) settle the name collisions.

## Then the technical picture
[knowledge/00-overview.md](../knowledge/00-overview.md) is the map of every system, what a kind
of change touches, and how to navigate to the rest.

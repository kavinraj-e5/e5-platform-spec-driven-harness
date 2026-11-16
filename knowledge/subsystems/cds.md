---
type: subsystem
name: Credential Distribution Service (CDS)
repo: e5-ss-credential-distribution-service
branch: release/v1.0.2
commit: 14b6f6f
language: java
build: gradle
deployment: credential-distribution-service-ss
namespace_pattern: platform-subsystem-{env}
etp_roles: []
owns_schema: false
ports: [50051, 9000, 8091]
health: http /actuator/health on the management port (8091 default; deployment configs set 8090)
calls: [deployment-mgmt]
called_by: []
consumes: [the workload topic report from task-reader, Resource Navigator credential events]
provenance: code
verified: 2026-11-13
prose_reviewed_at_commit: 14b6f6f
deployed_in_qa: not-observed
---

> **Whether CDS runs in QA is unknown.** The only qa cluster snapshot is from 2026-10-02 and
> predates CDS; this machine currently has no QA kubeconfig, so it could not be re-read.

# Credential Distribution Service

Sizes fb-worker pods to a **shared credential pool**. One credential backs one pod, so CDS
decides how many pods (`max_replicas`) each application of each *shared credential group*
may run, and writes that number to `deployment-mgmt`. Spring Boot, gRPC client and server,
Kafka **consumer only**. Onboarded into the knowledge base on 2026-11-13; it had no entry
before that.

**Responsibility (what it owns):** the credential *ceiling* for every member of a shared
group, and the record of how it reached it — one plan row per group per planning run.

**Not its responsibility:**

- **Handling tasks.** CDS never polls AKST and serves no task type. It *observes* the
  workload report. It does not fit the subsystem pattern of poller → handler → response
  consumer at all — it is a planner.
- **Choosing the pod count.** It writes the ceiling. `dynamic-scaling` picks the count
  beneath it: `MinimalPodScheduler.findMinimalSchedule(workloads, maxReplicas,
  currentReplicas)` computes `maxExtraPods = max(0, maxReplicas - currentReplicas)`
  (dynamic-scaling `release/v1.3.2` `algorithm/MinimalPodScheduler.java:259`). Lowering the
  ceiling below the running count is safe because DSS drains a pod after its task.
- **Holding credentials.** That is `ccs`. CDS reads CCS pools — group members carry
  `ccs_pool_name` / `ccs_workflow_name` (`src/main/proto/deployment_management.proto:42`) —
  through the Resource Navigator (RN).
- **Deciding that a credential expired.** When one does, `minReplicas` is set to 0
  upstream and RN's count changes; CDS reads both on its next plan (`README.md`).
- **Persistence.** It has no database. The JDBC starter and Postgres driver were removed;
  all state is in memory (`CLAUDE.md`, Tech Stack).

## Entry points

`src/main/java/e5/platform/ss/credentialdistribution/CredentialDistributionService.java:11-12`
— `@SpringBootApplication`, `@EnableScheduling`. Hexagonal layout: `domain/model`,
`application/{ports/in,ports/out,service}`, `infrastructure/adapters/{in,out}`.

Two startup runners keep the pod **not ready** until their load succeeds, retrying every
10s: the credential snapshot (`CredentialAvailabilityStartupRunner`, `@Order(0)`) then the
group membership (`SharedGroupMembersStartupRunner`, `@Order(1)`).

## Inbound

| Mechanism | Site | What arrives |
|---|---|---|
| Kafka | `infrastructure/adapters/in/stream/TaskEventPayloadKafkaAdapter.java:41` — key a topic config key | the the workload topic report task-reader republishes every minute: `CREATED` and `PICKED` tasks per deployment version |
| Kafka | `infrastructure/adapters/in/stream/CredentialEventKafkaAdapter.java:48` — key a topic config key | RN `CREDENTIAL_ALLOCATED` / `RELEASED` / `ACTIVATED` / `DEACTIVATED`; any other type is ignored |
| Schedule | `infrastructure/adapters/in/scheduler/SharedGroupMembersBackgroundJob.java:16` | every 60s: reload group membership from deployment-mgmt |
| gRPC server | `infrastructure/adapters/in/grpc/GrpcCredentialDistributionAdminService.java:15` | `CredentialDistributionAdminService.EvictSharedGroupMembers` (`src/main/proto/credential_distribution.proto:13`) — a one-shot manual override |

The task consumer's group id **must differ from dynamic-scaling's**: both read
the workload topic. The RN consumer's group id is suffixed with `${HOSTNAME}` so every pod sees
every event; RN does not resend.

**`EvictSharedGroupMembers` has no caller anywhere in the indexed estate.** CDS says it
owns the proto and that the interface project copies it; `interface` at `release/v1.7.6`
contains neither the proto nor a call. See
cds-eviction-rpc-has-no-caller (archived).

## Outbound

One target, `deployment-mgmt`, through `@GrpcClient("db-mgmt-service")`
(`infrastructure/adapters/out/grpc/DeploymentManagementGrpcAdapter.java:50`). CDS carries a
trimmed copy of deployment-mgmt's proto with four RPCs
(`src/main/proto/deployment_management.proto:14-18`), all **present** in the server at
deployment-mgmt `release/v1.9.8`:

| RPC | Call site | Use |
|---|---|---|
| `GetAppCredentialInfo` | `DeploymentManagementGrpcAdapter.java:58` | (pool, workflow) pairs for the startup snapshot |
| `GetSharedCredentialGroupMembers` | `:75` | group membership, one row per member application |
| `UpdateFbWorkerPodDetails` | `:101` | writes `max_replicas` — an absolute value; `replica_count` is a delta, so CDS leaves it unset |
| `SavePlanExecution` | `:151` | one `plan_execution` row plus one `replica_decision` row per decision |

Plus one HTTP call: the RN batch availability lookup at startup
(`infrastructure/adapters/out/http/CredentialAvailabilityHttpAdapter.java`), URL from
`CREDENTIAL_AVAILABILITY_API_URL`. RN events always win over snapshot data.

## How a plan is made

`CredentialDistributionPlannerService.planAsync()` (`application/service/CredentialDistributionPlannerService.java:129`)
runs one plan at a time; requests arriving during a plan collapse into one follow-up.
**There is no timer.** A plan is triggered by a task report, by every applied RN event, by
every successful membership refresh, and by an eviction that removed something.

Per group:

1. Group credential count = the smaller of the members' `total_count` and the pool's
   `totalResourceCount`. Skipped if the pool total is missing, or not above the sum of the
   minimums.
2. Only `CREATED` tasks are demand — a `PICKED` task already holds a pod. A task belongs
   to an application only when its virtual queue is one of that member's VQ names.
3. `CREATED` tasks across the group are ordered by deadline, then priority (1 highest),
   then created time. A past deadline stays eligible. Each grant spends one credential RN
   reports free.
4. `desired = max(minReplicas, picked + granted)`, written as `max_replicas`.

The group **waits** until every member has reported in the current minute-round. A member
that misses `MISSED_UPDATES_BEFORE_PLAN` rounds (default 3) stops being waited on and is
held at its minimum.

## Configuration

Config **keys** only. Every value is injected per environment and is deliberately not
recorded here. a topic config key, a topic config key,
`spring.kafka.consumer.group-id`, `grpc.client.db-mgmt-service.address`,
`app.planner.missed-updates-before-plan`, `app.jobs.shared-group-members`,
`app.startup-sequence.enabled`.

> **The key a topic config key is a trap.** dynamic-scaling has a key of
> the same name bound to the Task Manager *completion* topic; in CDS it is bound to
> the workload topic. The name is a leftover from when task-manager produced the report — the
> producer is now task-reader. See
> kafka-config-keys-are-repo-local (archived).

## Hazards worth knowing before editing

- **Copied DTOs, no shared artifact.** `AkstWorkloadRequest` / `AkstWorkload` / `MetaInfo`
  are copied from dynamic-scaling's `ScalingEstimatorRequest`, and `CredentialStateEvent` /
  `CredentialPoolData` from DSS's DTOs, each with an instruction to "keep fields in sync"
  (`CLAUDE.md`). Nothing enforces it.
- **Two independent views of one pool.** dynamic-scaling keeps its own RN cache and uses
  `totalResourceCount` only (dynamic-scaling `release/v1.3.2`
  `consumer/CredentialEventConsumer.java:38-39`); CDS uses total and available. They can
  disagree transiently; CDS's ceiling is the one that binds.
- **One replica, by necessity.** Every cache is per pod (`deployment-configs/qa-config.yaml:6`
  sets `replicaCount: 1`). Scaling CDS out would give each replica a different plan.
- **Ship deployment-mgmt first.** An older deployment-mgmt answers `UNIMPLEMENTED`, and CDS
  stays not-ready retrying.
- **Java 25, not 21.** `build.gradle:15` sets the toolchain to 25; the repo's own CLAUDE.md
  still says 21. The build file is what compiles.

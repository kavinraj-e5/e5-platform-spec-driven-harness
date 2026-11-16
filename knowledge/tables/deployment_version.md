---
type: table
name: deployment_version
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.DeploymentVersionEntity
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/DeploymentVersionEntity.java:18
pk: deployment_version_id
created_by: pre-Flyway baseline
provenance: code
verified: 2026-10-02
---

# `deployment_version`

**The hub of the whole schema.** A `deployment_version_id` is the platform's universal
correlation key: it appears in request messages of ~30 of DMS's 93 rpcs and is the FK target of
six other tables. Understand this table and the topology falls out.

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md).
**Created by:** pre-Flyway baseline; migrations only alter it and its join table.

## Columns

| Column | Type | Notes |
|---|---|---|
| `deployment_version_id` | uuid | **PK**, `@GeneratedValue` (`:20-23`) |
| `deployment_config_id` | uuid | FK → `deployment_config.config_id` (`:25-27`) |
| `deployment_id` | uuid | FK → `deployment` (`:29-31`) |
| `workflow_variant_id` | uuid | FK → `workflow_variant.variant_id` (`:33-35`) |
| `workflow_sku_id` | uuid | FK → `workflow_sku` (`:37-39`) |
| `customer_id` | uuid | FK → `customer` (`:41-43`) |
| `active` | boolean | the only non-FK business column (`:57`) |
| `created_at`, `updated_at` | timestamp | `BaseEntity` |

**All five `@ManyToOne`s default to EAGER** (no `fetch` attribute, `:25-43`) — loading one
`deployment_version` loads config, deployment, variant, sku and customer in the same query plan.

## Relations

| Direction | Target | Mapping |
|---|---|---|
| ← | `deployment_state` | `@OneToMany(mappedBy="deploymentVersion")` (`:45-47`) |
| ↔ | `application_worker` | `@ManyToMany` **EAGER** via join table `deployment_version_application_worker` (`:49-55`) |
| ← | `workflow_run`, `schedule`, `runtime_config`, `workflow_manager_state`, `fb_worker_pod` | each holds a `deployment_version_id` FK |
| ← | `scheduler_task` | holds `deployment_version_id` but **declares no FK** (`SchedulerTaskEntity.java:31-32`) |

## Join table `deployment_version_application_worker`

PK is `(deployment_version_id, app_worker_id)` — but only since `V1_2025_11_25_001`, which
dropped the previous surrogate `mappingid` and the old PK. `V1_2025_09_20_001` had earlier
dropped a `status` column, moving per-app status to
[`deployment_app_status`](#) (`DeploymentAppStatusEntity.java:15`).

## Who reads / writes

Written by `SaveDeploymentDetails` (workflow-ops) and `CreateDeploymentVersion`
(devops-pipeline). Read by essentially every topology rpc —
`GetDeploymentDetailsByDeploymentVersionId`, `GetAllActiveDeployments`,
`GetDeploymentDetailsWithId`, `GetAppStatus`, `validateAppStatus`, `GetFbWorkerPodDetails`,
`GetScheduleTriggers`, `GetDeployedVersionDetails`. The widest query is
`DeploymentDetailsRepositoryImpl`, which joins 10 tables around this one.

## Gotchas

- `active` has **no partial unique index**, so nothing in the schema prevents two active
  versions for the same `deployment_id`. `GetActiveDeploymentsDetailsByDeploymentVersionId`
  filters in SQL, not by constraint.
- `GetDeploymentDetailsByResourceName` reaches this table via a 7-way join that starts at
  [`credential_assignment_registry`](credential_assignment_registry.md) and matches
  `deployment_state.value = car.kube_namespace` with `deployment_state.key = 'kubeNamespace'`
  (`DeploymentStateRepositoryImpl.java:123-139`) — a **string join on an untyped key/value
  table**, not a FK.

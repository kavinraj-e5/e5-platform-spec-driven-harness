---
type: table
name: credential_assignment_registry
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.CredentialAssignmentRegistry
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/CredentialAssignmentRegistry.java:16
pk: lock_id
created_by: pre-Flyway baseline
provenance: code
verified: 2026-10-02
---

# `credential_assignment_registry`

**The ledger of which `fb-worker` pod currently holds which external-system credential lock.**
One row per lock, keyed by the **lock handle itself**, not a surrogate id.

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md).
**Sole writer:** [credential-monitor](../subsystems/credential-monitor.md).
**Note:** the credential *distribution* and the actual *unlock* happen elsewhere (CCS + an SQS
consumer); this table only records intent and observation.

## Columns

| Column | Type | Notes |
|---|---|---|
| `lock_id` | **varchar(50)** | **PK, application-assigned** (`@Id @Generated`, `updatable = false`, `nullable = false`) — the only non-uuid PK in the DMS schema (`:19-22`) |
| `pod_name` | varchar(60) | NOT NULL (`:24-25`) |
| `kube_namespace` | varchar(60) | (`:27-28`) |
| `requester_id` | varchar(50) | `updatable = false`; DMS substitutes the literal `"NOT_DEFINED"` when absent (`DeploymentManagementServiceImpl.java` `convertToPodCredentialsInfo`) (`:30-31`) |
| `fetched_time` | **varchar(40)** | ⚠ an ISO-8601 instant **stored as text**; widened from an unknown smaller length by `V1_2025_11_25_002` (`:33-34`) |
| `resource_name` | varchar(100) | added `V1_2025_11_24_004` (`:36-37`) |
| `status` | varchar | no `@Column` annotation; values are the string literals `ACTIVE` / `RELEASED` / `RELEASE_REQUESTED` (`:39`) |
| `created_at`, `updated_at` | timestamp | `BaseEntity` |

**No foreign keys.** `kube_namespace` is the join key to the rest of the topology, matched as a
**string against `deployment_state.value` where `deployment_state.key = 'kubeNamespace'`**
(`DeploymentStateRepositoryImpl.java:129-139`).

## Migrations

| Migration | Change |
|---|---|
| `V1_2025_11_24_004` | `+ resource_name VARCHAR(100)` |
| `V1_2025_11_25_002` | `ALTER COLUMN fetched_time TYPE VARCHAR(40)` |

## RPCs — 3, implemented inline

Unusually, these three are **not** routed through a use case; they are written directly in the
gRPC impl (`DeploymentManagementServiceImpl.java:264-344`) with hand-rolled validation
(`validateCredentialInfoRequest`, `validateReleaseStatusRequest`,
`validateFetchActiveCredentialsRequest`).

| rpc | Effect |
|---|---|
| `addAssignedCredentialToStore` | INSERT with `status = 'ACTIVE'` (`CredentialRepositoryImpl.java:59`) |
| `updateReleaseStatusToStore(podName, lockId, ReleaseType)` | HQL `UPDATE … SET p.status = :status` (`:84-106`) — **status only**, nothing else is touched |
| `fetchActiveCredentialsFromStore(podName)` | HQL `FROM CredentialAssignmentRegistry p WHERE p.podName = :podName AND p.status = :status('ACTIVE')` (`:37-40`) |

`GetDeploymentDetailsByResourceName` (called by workflow-manager) also reads this table, taking
`ORDER BY car.fetched_time DESC LIMIT 1` on `status = 'ACTIVE'` — **a lexicographic sort on a
text timestamp**, which is only correct because the values are zero-padded ISO-8601.

## Lifecycle

```
worker fetches credential
  → Kafka FETCHED event
  → credential-monitor CCSCredentialMonitor
  → addAssignedCredentialToStore           → status = ACTIVE
worker releases
  → Kafka RELEASED event                    → status = RELEASED
pod dies / restarts (BackOff|Killing|SuccessfulDelete|Pulled|Pulling)
  → KubernetesPodsMonitor
  → fetchActiveCredentialsFromStore(pod)
  → keep rows where fetched_time < k8s eventTime
  → SQS {"type":"POD_UNHEALTHY","lockId":"a,b,c"}   ← fires FIRST
  → updateReleaseStatusToStore(RELEASE_REQUESTED)   ← then records intent
```

## Gotchas

- **A multi-lock reclamation writes `"a,b,c"` into the single `lock_id` field** of the
  `CredentialReleasedEvent` (`KubernetesPodsMonitor.java:188-192`), so the HQL update matches
  **no row** when more than one lock is reclaimed at once. The SQS message, which does the real
  work, is unaffected — the ledger simply drifts.
- The SQS send happens **before** the DMS write, so a DMS failure leaves a released lock still
  marked `ACTIVE`.
- There is **no TTL or lease expiry anywhere**; "staleness" is purely
  `fetched_time < pod event time`.
- `status` has no CHECK constraint and no enum type — any string is accepted.

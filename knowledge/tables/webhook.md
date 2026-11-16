---
type: table
name: webhooks
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.WebhookEntity
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/WebhookEntity.java:17
pk: id
created_by: pre-Flyway baseline
provenance: code
verified: 2026-10-02
---

# `webhooks`

**The webhook *registry*, not the webhook *delivery queue*.** The physical table name is
plural — `webhooks` — which is why a card named `webhook` is easy to mis-resolve.

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md).
**Not here:** delivery attempts, retries, backoff, circuit-breaker state and suspension all
live in **webhook-service's own schema** — [`destinations`](destinations.md) and
[`webhook_queue`](webhook_queue.md). DMS has no column named `webhook_delivery_suspended` and
never did — see
webhook-delivery-suspended-is-not-a-dms-column (archived).

## Columns

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, `@GeneratedValue`, not updatable (`:20-23`) |
| `webhook_name` | varchar(50) | (`:25-26`) |
| `secure_key` | varchar | the shared secret (`:28-29`) |
| `url` | varchar | **UNIQUE** — declared twice, at the `@Table` level (`:17`) and the column level (`:31`) |
| `auth_type` | varchar | plain String, **not** an enum (`:34-35`) |
| `is_enabled` | boolean | (`:37-38`) |
| `root_user` | boolean | added `V1_2025_12_22_001`, DEFAULT `false` **NULL-able**; the same migration back-filled every existing row to `true` |
| `max_retry_count` | int | **advisory only** — DMS performs no delivery, so nothing in this repo reads it |
| `status` | pg enum | `WebhookStatus`, `@Type("pgsql_enum")` (`:46-49`) — **note there is no `@TypeDef` on this class**, unlike `WorkflowRunEntity.java:22` |
| `created_at`, `updated_at` | timestamp | `BaseEntity` |

**Relations:** `@ManyToMany(cascade = PERSIST, fetch = EAGER)` → [`events`](event.md) via join
table **`webhook_events`** (`webhook_id`, `event_id`) (`:51-57`). That join table has **no
Flyway migration** — it exists only because Hibernate maps it.
Separately, [`webhook_subscription`](webhook_subscription.md) links a webhook to a `deployment`
via `subscription_id`.

## The `root_user` / `webhook_subscription` split

`root_user = true` means "this webhook receives events from **all** deployments"; `false` means
"only the deployments listed in `webhook_subscription`". The back-fill in
`V1_2025_12_22_001` set every pre-existing webhook to `true` for backward compatibility, so
**any webhook created before 2025-12-22 is a firehose unless explicitly changed**.

## RPCs

Registry: `RegisterWebhook`, `UpdateWebhook`, `UpdateWebhookStatusById`, `removeWebhook`,
`FindWebhookById`, `GetAllWebhooks`.
Event-type binding (writes `webhook_events`): `SubscribeEventType`, `UnSubscribeEventType`.
Deployment binding (writes `webhook_subscription`): `SubscribeWebhook`, `UnSubscribeWebhook`.
Lookup for dispatch: `FindWebhooksByEventType`, `FindWebhooksByEventId`,
`FindWebhooksByEventTypeAndDeploymentVersionId`, `FindWebhooksByEventTypeAndWorkflowDetails`.
Full table in
[deployment-mgmt-grpc-api](../subsystems/deployment-mgmt-grpc-api.md#6-webhooks--14-rpcs--webhooks-webhook_events-webhook_subscription).
event-processor's vendored subset of these rpcs is catalogued in
[deployment-mgmt-webhook-rpcs](../contracts/deployment-mgmt-webhook-rpcs.md).

## Who reads / writes

| Service | RPCs |
|---|---|
| interface `v1.6.8` | `RegisterWebhook`, `UpdateWebhook`, `FindWebhookById`, `GetAllWebhooks`, `SubscribeEventType`, `UnSubscribeEventType`, `SubscribeWebhook`, `UnSubscribeWebhook` |
| event-processor | `FindWebhooksByEventType`, `FindWebhooksByEventTypeAndDeploymentVersionId`, `FindWebhooksByEventTypeAndWorkflowDetails`, `UpdateWebhookStatusById` |
| devops-pipeline | `SubscribeWebhookByClient` — ⚠ **this rpc does not exist at DMS v1.5.0** |

## Gotchas

- `UnSubscribeWebhook` does not delete: it sets `webhook_subscription.status = false`.
- `removeWebhook` and `FindWebhooksByEventId` have **no call site at any pinned commit** — a
  registered webhook can be disabled but never deleted through the API.
- `max_retry_count` living here while the retry *engine* lives in webhook-service means the two
  can disagree silently.

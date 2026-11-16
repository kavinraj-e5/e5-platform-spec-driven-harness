---
type: table
name: webhook_subscription
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.WebhookSubscriptionEntity
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/WebhookSubscriptionEntity.java:15
pk: (webhook_id, subscription_id)
created_by: V1_2025_12_22_001__create_webhook_deployment_subscription.sql
provenance: code
verified: 2026-10-02
---

# `webhook_subscription`

**The junction that scopes a webhook to specific deployments** rather than to the whole
platform. The migration file is named `create_webhook_deployment_subscription` but the table it
creates is `webhook_subscription` — a naming mismatch worth knowing when grepping.

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md).

## Columns

| Column | Type | Notes |
|---|---|---|
| `webhook_id` | uuid NOT NULL | **PK part 1**; FK → [`webhooks`](webhook.md) in the entity (`:27-33`) |
| `subscription_id` | uuid NOT NULL | **PK part 2**; the entity maps it `@ManyToOne` to **`DeploymentEntity`**, not to `subscription` (`:35-41`) |
| `status` | boolean DEFAULT true | soft-delete flag (`:43-44`) |
| `created_at`, `updated_at` | timestamp | DEFAULT `CURRENT_TIMESTAMP` |

**PK:** `webhook_subscription_pkey PRIMARY KEY (webhook_id, subscription_id)`, mapped with
`@IdClass(WebhookSubscriptionEntity.WebhookSubscriptionId.class)` (`:16`, inner class `:52-55`)
— the only `@IdClass` composite key in the DMS schema.
**Index:** `idx_webhook_subscription_subscription_id`.
**⚠ The SQL declares no foreign keys at all**; both FKs exist only as Hibernate `@JoinColumn`s.

## The column name lies about its target

The column is called `subscription_id` and the platform *has* a `subscription` table, but the
entity joins it to **`deployment`** (`:41`). The migration's own comment says
*"Junction table to manage webhook subscriptions to specific deployments"*. So
`webhook_subscription.subscription_id` is a **`deployment_id`**. Because the SQL declares no FK,
nothing in the database catches a value that is actually a `subscription_id`.

## Relationship to `webhooks.root_user`

The same migration added `webhooks.root_user BOOLEAN DEFAULT false NULL` and then
`UPDATE webhooks SET root_user = true` for every existing row. Semantics:

| `root_user` | Meaning |
|---|---|
| `true` | webhook receives events from **all** deployments; `webhook_subscription` is ignored |
| `false` | webhook receives events only from the deployments listed here with `status = true` |

**Every webhook that existed before 2025-12-22 is `root_user = true`.**

## RPCs

Written by `SubscribeWebhook` and `UnSubscribeWebhook` (the latter sets `status = false`;
it does not delete). Read by
`FindWebhooksByEventTypeAndDeploymentVersionId` and
`FindWebhooksByEventTypeAndWorkflowDetails`, both called by event-processor.

Both writers are called by [interface](../subsystems/interface.md) `release/v1.6.8`
(`WebhookServiceImpl.java:138` and `:202`). ⚠ devops-pipeline `release/v4.0.8` instead calls
**`SubscribeWebhookByClient`** (`libs/webhook/scripts/subscribe-webhook-by-client.py:48`), an
rpc that **does not exist at DMS v1.5.0** — it only appears at v1.8.2. So the pipeline's
client-scoped subscription path is broken against the pinned server. See
task-route-absent-in-pinned-dms-commit (archived).

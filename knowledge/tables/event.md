---
type: table
name: events
owner: deployment-mgmt
schema: public
entity: com.e5.deploymentservices.data.entity.EventEntity
entity_file: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/entity/EventEntity.java:17
pk: id
created_by: pre-Flyway baseline
provenance: code
verified: 2026-10-02
---

# `events`

**The catalogue of subscribable event *types*** — not a log of event *occurrences*. Physical
name is plural `events`; the per-occurrence delivery status lives in the separate, oddly-named
`tbl_event_status`.

**Owner:** [deployment-mgmt](../subsystems/deployment-mgmt.md).

## Columns

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | **PK**, `@GeneratedValue`, not updatable (`:20-23`) |
| `event_name` | varchar(255) | (`:25-26`) |
| `event_type` | varchar(255) | what `FindWebhooksByEventType` matches on (`:28-29`) |
| `status` | varchar(15) + pg enum | `EventStatus`, `@Enumerated(STRING)` **and** `@Type("pgsql_enum")` with `length = 15` — the three annotations describe three different storage shapes (`:34-37`) |
| `created_at`, `updated_at` | timestamp | `BaseEntity` |

`equals`/`hashCode` are overridden on `id` only (`:39-50`), which matters because the entity
participates in an EAGER `@ManyToMany` `Set`.

**Relations:** inverse side of the `webhook_events` join
(`@ManyToMany(mappedBy = "events")`, `:31-32`); the owning side with
`cascade = PERSIST` is [`webhooks`](webhook.md) (`WebhookEntity.java:51-57`).

## The companion table `tbl_event_status`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `event_id` | uuid | **no FK declared** |
| `event_name` | varchar | denormalised copy |
| `webhook_id` | uuid | **no FK declared** |
| `status` | enum `EventStatusType` | |
| `event` | jsonb | the full payload |

`EventStatusEntity.java:16` — the `tbl_` prefix is unique in this schema; nothing else uses it.
Neither of its two logical FKs is enforced, so orphan status rows are possible by construction.

## RPCs

`SaveEvent`, `FindEventsByIds`, `GetAllEvents`, `UpdateEvent`, `removeEvent` →`events`.
`SaveEventStatus`, `GetEventStatusForScheduler`, `UpdateEventStatusById`, `GetEventStatusById`
→ `tbl_event_status`.

## Who reads / writes

interface reads via `GetAllEvents` and `FindEventsByIds`, and writes the join through
`SubscribeEventType`. event-processor owns the `tbl_event_status` lifecycle
(`SaveEventStatus`, `GetEventStatusForScheduler`, `UpdateEventStatusById`).
`SaveEvent`, `UpdateEvent`, `removeEvent` and `GetEventStatusById` have **no call site anywhere** —
event types are seeded out of band.

## Gotchas

- `cascade = PERSIST` on the owning side means registering a webhook with an unknown event type
  **silently creates the event type** rather than failing.
- `@Type("pgsql_enum")` on `EventEntity.status` without a `@TypeDef` on the class relies on a
  `@TypeDef` being registered elsewhere; `WorkflowRunEntity` and `ScheduleEntity` declare theirs
  locally, `EventEntity` and `WebhookEntity` do not.

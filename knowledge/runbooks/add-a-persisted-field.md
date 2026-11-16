---
type: runbook
name: Add a persisted field or table
applies_to: [deployment-mgmt]
triggers: ["store a new attribute", "add a column", "add a table", "persist X"]
provenance: code
verified: 2026-10-03
---

# Add a persisted field or table

**It lands in deployment-mgmt. Always.** It is the only schema owner among the subsystems —
interface, workflow-manager and workflow-ops hold no tables of their own. If you are reaching
for a table in one of those, the design is wrong, not the rule.

## Steps

1. **Flyway migration** under `src/main/resources/db/migration/V1/<year>/<month>/`.
2. **JPA entity** plus its `data/repository` impl.
3. **Register the entity.** `HibernateUtil` uses a hardcoded entity array — a new entity that is
   not added there is silently absent at runtime.
4. **Add or extend an rpc** in `deployment_management.proto` plus the service impl, and slot it
   into the right domain group (the proto is organised by domain, ~122 rpcs).
5. **Regenerate stubs in every consumer** before they can call it. This is why DMS leads the
   merge order.
6. **Write the table card** under `knowledge/tables/` so the next person can find it.
7. **Declare it** in the release document under `contracts_added`.

## Traps specific to this service

- **DMS cannot provision its own schema.** Flyway's baseline is literally `select 1;` with
  `baselineOnMigrate(true)`, and `hbm2ddl` is never set. **26 of 44 tables are created by nothing
  in the repo** — including every core table. Your migration may be the only thing that creates
  your table, or the table may already exist from outside. Check before assuming.
- **A native query hardcodes a `dev.` schema prefix** (`GetConfigValue`) while every deployment
  config sets `schema.name: public`. Do not copy that pattern.
- **State tables are named `<service>_state`** (`workflow_ops_state`, `workflow_manager_state`).
  DMS owns the schema; the named service owns the behaviour. Both change.
- The gRPC **health check is a constant** — DMS reports SERVING with the database down. Do not
  rely on it to tell you your migration landed.

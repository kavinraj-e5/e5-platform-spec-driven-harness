---
type: table
name: state-service per-workflow schema
owner: none — physical store is the shared state-service RDS; workflow-ops is the only platform writer
database: e5_{env}_platform (Postgres)
schema_pattern: "{client[0:32]}_{workflowCode[0:10]}_{variantCode[0:10]}_{sha256(client_workflow_variant)[0:10]}"
accessed_by: [workflow-ops]
provenance: code
verified: 2026-10-02
---

# state-service per-workflow schema

Not a table owned by any control-plane service — this is the per-tenant, per-workflow, per-variant Postgres **schema** that holds a running workflow's state tables. It is listed here because [workflow-ops](../subsystems/workflow-ops.md) is the only platform component that reads and writes it directly with SQL, and because the naming rule is derived in code and recorded nowhere else.

## Schema name derivation

`src/services/state_management/state_utils.py:153-170`:

```python
schema  = f"{client_name}_{workflow_name}_{variant_name}"
hash_str = sha256(schema).hexdigest()
workflow_code = "-".join(part[0:4] for part in workflow_name.split("-"))
variant_code  = "-".join(part[0:4] for part in variant_name.split("-"))
return f"{client_name[0:32]}_{workflow_code[0:10]}_{variant_code[0:10]}_{hash_str[0:10]}"
```

The truncation exists because Postgres caps identifiers at 63 characters (comment at `:132-133`). `workflow_name` here is the **display name**, resolved first via deployment-mgmt `getDisplayNameByWorkflowName` (`src/services/state_management/get_state_list.py:35`) — passing the raw workflow name yields a different, wrong schema.

## Tables

The schema's tables *are* the workflow's states — one table per state, created by the workflow runtime, not by workflow-ops. Three names are reserved infrastructure and are filtered out of every state listing (`get_state_list.py:69`):

| reserved table | excluded from |
|---|---|
| [`e5_task`](e5-task.md) | `GetStateList`, export, import |
| `e5_converter` | `GetStateList`, export, import |
| `e5_token` | `GetStateList`, export, import |

This is the same schema that AKST's [`e5_task`](e5-task.md) table lives in — `${dbconfig.schemaName}` on the AKST side and `_rds_schema_name(...)` here are two independent derivations of one identifier, and workflow-ops is the only component that computes it from first principles rather than reading it from config.

Everything else in the schema is treated as a user state table and is eligible for `EXPORT_STATE` / `IMPORT_STATE`.

## Credentials

Not a config key — derived at call time. Endpoint comes from SSM `/platform/workflow/{env}` → `stateservice.rds.endpoint`; the per-client username is `e5_{env}_{client}` with `-`→`_`; the password is **recomputed** as `pbkdf2_hmac("sha512", r_str, r_slt, r_itr, dklen=16).hex()` from SSM `/platform/state-service/{env}/{client}` (`state_utils.py:28`, `:125-137`). `iterations` must exceed 10000 or the derivation raises (`:97-98`). A connection string is then built per request (`get_state_list.py:110`) — there is no shared pool across tenants.

`DATABASE_URL`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_PRE_PING` are read by `src/libs/database/db.py:30-33`, but **no module imports `db.py`** — only `connection.DatabaseManager` is used, always with an explicitly built URL. `DATABASE_URL` is therefore dead config and correctly absent from `deployment-configs/*`.

## Companion S3 layout

Export/import stage through `s3://e5-{env}-state-service/{client}/{workflow}/{variant}/{download|upload}` (`state_utils.py:36-40`).

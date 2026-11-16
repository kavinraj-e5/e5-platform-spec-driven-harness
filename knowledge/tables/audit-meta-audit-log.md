---
type: table
name: audit.meta_audit_log
schema: audit
database: audit-service Postgres (DB_NAME)
owner: audit-service
migration: src/main/resources/db/migration/V1__init.sql:240
entity: com/healthcare/audit/domain/entity/MetaAuditLogEntity.java
status: implemented but never populated at v1.1.1
provenance: code
verified: 2026-10-02
---

# audit.meta_audit_log

The "**who looked at the audit log**" trail required for HIPAA accounting-of-disclosures, owned by [audit-service](../reporting/audit-service.md). The table, the DB-level immutability trigger, the entity, the repository and the AOP aspect all exist — and the table is **always empty**, because the `@MetaAudited` annotation that drives the aspect is applied to no method in the codebase.

## Fields (`V1__init.sql:240-254`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `timestamp` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | when the access happened |
| `actor_id`, `actor_type` | VARCHAR(200/50) NOT NULL | who read |
| `action` | VARCHAR(100) NOT NULL | `<DeclaringType>.<method>` built by `MetaAuditAspect:84-86` |
| `resource` | VARCHAR(500) NOT NULL | what was read |
| `query_parameters` | JSONB | the filter used |
| `result_count` | INTEGER | rows returned |
| `client_ip`, `user_agent` | VARCHAR(45) / TEXT | caller identity |
| `trace_id` | VARCHAR(64) | correlation to [audit.events](audit-events.md) |
| `duration_ms` | INTEGER | read latency |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | — |

**Immutability is enforced in the database**, not the app: trigger `no_update_meta_audit` fires `BEFORE UPDATE OR DELETE` and raises `'Meta audit records cannot be modified or deleted'` (`V1__init.sql:259-268`). Note this also means **nothing can ever purge this table**, including a future retention job.

**FKs:** none.

## Who reads/writes it
| Operation | Code | Reality |
|---|---|---|
| insert | `MetaAuditAspect.auditAccess:46` → `metaAuditRepository.save:73` | the aspect advises `@MetaAudited` methods; **`grep -rn "@MetaAudited"` over `src/main/java` returns no usage** |
| read | `MetaAuditRepository` | no production caller |

Root cause: there is no query API to annotate (`SecurityConfig.java:12-18` states the service exposes none), so the aspect has nothing to attach to. The compliance claim at `README.md:45` ("Meta-Audit | Tracks who accessed audit logs") is therefore not satisfied in this commit — see ../../disagreements/audit-readme-claims-query-api-rbac-metaaudit.md (archived).

Indexes: `idx_meta_audit_timestamp`, `idx_meta_audit_actor`, `idx_meta_audit_action`, `idx_meta_audit_trace WHERE trace_id IS NOT NULL` (`V1__init.sql:271-274`).

**Consumers:** none.

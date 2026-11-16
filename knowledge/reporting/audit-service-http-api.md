---
type: api
name: Audit Service HTTP surface
protocol: http
owner: audit-service
repo_path: platform-whole-context/subsystem-workflow-interaction-support/e5-platform-ss-audit-service
base_path: /api/v1, /actuator
port: 8080
auth: spring-security filter chain, permitAll on health/metrics, denyAll elsewhere
provenance: code
verified: 2026-10-02
---

# audit-service HTTP surface

Health and metrics only. The audit **query** API described in `README.md:32` does not exist in this commit — `SecurityConfig.java:40` ends the chain with `anyRequest().denyAll()` and there is exactly one business-free controller. Owned by [audit-service](audit-service.md).

## Operations

### Liveness
| Operation | Path | Touches |
|---|---|---|
| `health` (`HealthController.java:31`) | `GET /api/v1/health` | nothing — returns 200, or 503 on internal error (`:40-42`) |
| actuator health | `GET /actuator/health`, `/health/liveness`, `/health/readiness` (`application.yml:57-68`) | `audit.events`/`archive_log` via the auto-configured `db` indicator; `kafka` indicator (`KafkaHealthIndicator.java:17`); `s3` indicator **only when `aws.s3.enabled=true`** (`S3HealthIndicator.java:18`); `secretsManager` indicator only when `aws.secretsmanager.enabled=true` (`SecretsManagerHealthIndicator.java:17`) |
| actuator info / metrics / prometheus | `GET /actuator/{info,metrics,prometheus}` | Micrometer only: `audit.events.{received,processed.consumer,failed,deserialization.failed,cloudevents.validation.failed,duplicates,missing.trace}`, `audit.events.processing.time`, `audit.archive.{jobs.started,jobs.completed,jobs.failed,partitions.completed,partitions.failed,events.total}` (`CloudEventsAuditConsumer.java:59-81`, `AuditMetricsImpl.java:24`, `ArchiveService.java:85-160`) |

### Explicitly absent
No `GET /api/v1/audit-events` or equivalent. `AuditEventResponse`, `PagedResponse` and `AuditEventResponseMapper` exist but are referenced by **no** production class, and `audit.query.default-page-size`/`max-page-size` are read by no code. Likewise `@MetaAudited` (`annotation/MetaAudited.java`) is applied to no method, so the "who read the audit log" trail is never written. See ../../disagreements/audit-readme-claims-query-api-rbac-metaaudit.md (archived).

## Security
`SecurityConfig.java:26-56`: CSRF off, stateless sessions, `permitAll` on `/actuator/health/**`, `/actuator/info`, `/actuator/prometheus`, `/api/v1/health`; `denyAll` on everything else. No roles or JWT decoder are configured — there is no RBAC despite `README.md:44`. `management.endpoint.health.show-details: when_authorized` (`application.yml:61`) means details are hidden from these anonymous callers.

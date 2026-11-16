---
type: api
name: Webhook Service HTTP surface
protocol: http
owner: webhook-service
repo_path: platform-whole-context/subsystem-workflow-interaction-support/webhook-service
base_path: /actuator
port: 8083
auth: none (no spring-boot-starter-security in pom.xml)
provenance: code
verified: 2026-10-02
---

# webhook-service HTTP surface

**There is no business HTTP API.** The repo contains zero `@RestController`/`@Controller` classes; port 8083 (`application.yml:58`) serves only Spring Boot actuator. Everything functional arrives over Kafka — see [webhook-request message](../contracts/webhook-request-message.md). Owned by [webhook-service](webhook-service.md).

## Operations

### Observability
| Operation | Path | Touches |
|---|---|---|
| health | `GET /actuator/health` (+`/liveness`, `/readiness`; `application.yml:66-71`) | `webhook_queue` counts via `WebhookHealthIndicator.java:36` → `WebhookQueueService.getQueueStats`; plus auto-configured `db`, `redis`, `kafka` indicators (`application.yml:72-78`) and the custom `KafkaHealthIndicator` / `RedisHealthIndicator` |
| info | `GET /actuator/info` | nothing |
| metrics | `GET /actuator/metrics` | in-memory Micrometer registry |
| prometheus | `GET /actuator/prometheus` | counters `webhook.ingestion.pipeline.result`, `webhook.queue.{polled,delivered,retried,failed,circuit_open_skipped,stuck_recovered}`, gauge `webhook.queue.active_deliveries`, `webhook.circuit.{opened,closed}` (`WebhookQueuePoller.java:112-122`, `WebhookIngestionService.java:80-93`, `CircuitBreakerService.java:46-47`) |

### Explicitly absent
No destination CRUD, no manual replay trigger, no circuit-breaker control endpoints, no DLQ browse/requeue. `CircuitBreakerService.forceOpen/forceHalfOpen/resetCircuit/deleteCircuit` (`:199,:219,:166,:184`) are Java methods only — `docs/FEATURES.md:80` calls them an "API", which is wrong; see ../../disagreements/webhook-features-doc-claims-missing-api.md (archived). The only *runtime* control plane is a CloudEvent of type `com.e5platform.workflow.webhook.availability` on the webhook-request topic (`WebhookIngestionService.java:51,172-180`).

## Security
No security starter, no filter chain, and `management.endpoint.health.show-details: always` (`application.yml:69`) — datasource URL/Redis/Kafka cluster detail is returned to any caller that can reach 8083. `OAUTH2_ISSUER_URI`/`OAUTH2_AUDIENCE` appear in `deployment-configs/*-config.yaml:253-257` but no code reads them.

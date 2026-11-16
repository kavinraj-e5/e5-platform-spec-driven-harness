---
type: subsystem
name: Workflow API
repo: e5-platform-ss-workflow-api
repo_path: platform-whole-context/subsystem-workflow-interaction-support/e5-platform-ss-workflow-api
branch: release/v1.5.0
commit: bb91723
language: java
build: maven
deployment: workflow-api-pl-subsystem
namespace_pattern: platform-subsystem-{env}
etp_roles: [task-initiator]
owns_schema: true
ports: [8081]
health: http /app/health
calls: [platform-core, webhook-service, audit-service, neos]
called_by: [external-partners, neos]
provenance: code
verified: 2026-10-04
deployed_in_qa: v1.4.2
deployment_lag: release/v1.5.0 available, v1.4.2 running
prose_reviewed_at_commit: bb91723
---

# Workflow API

The **REST** front door for partner/EHR submissions — distinct from [interface](interface.md),
which is the **GraphQL** front door for Neos/Automation Hub. The two do not talk to each other
and share no schema.

**Responsibility (what it owns):** Accepting a partner document/claim submission over HTTP,
validating it against a JSON Schema, extracting and transforming it, persisting the task in
**its own 16-table Postgres schema**, then submitting it to the platform as a GraphQL mutation.
It also owns the blob/file-object service (S3 upload + presigned-URL download) and a retry queue.

**Not its responsibility:** It is **not** a gRPC client of [deployment-mgmt](deployment-mgmt.md).
`grep` for `grpc|proto|stub` over `src/main/java` and `pom.xml` returns only CloudEvents doc
comments — there is no `.proto`, no gRPC dependency, no `ManagedChannel`. Its local
`getTaskStatus(…)` method (`service/core/SubmissionService`) is its own, **not** DMS's
`GetTaskStatus` rpc. Any dependency graph claiming workflow-api → deployment-mgmt is wrong.

**Entry points:** `WorkflowApiApplication.java:7-11` — `@SpringBootApplication` +
`@EnableScheduling` (`:8`). Spring Boot 3.2.0, Java 21, Maven, artifactId
`e5-new-workflow-api`.

**Inbound:**
- **10 code-declared REST handlers** across 7 `@RestController`s, including a **catch-all
  `POST /**` and `GET /**`** that routes by `category.full_path` — see
  [workflow-api-rest-api](workflow-api-rest-api.md).
- Kafka: **1** `@KafkaListener` on `${a topic config key}`
  (`inbound/consumer/StatusUpdateConsumer.java:41-46`), group
  `${spring.kafka.consumer.group-id}`, concurrency 3, `AckMode.RECORD`.
- 3 `@Scheduled` jobs: `RetryQueueProcessor.java:52` and `:66`, `BufferFlushScheduler.java:41`.
- Actuator: `/actuator/{health,info,metrics,prometheus}` plus `/actuator/health/{liveness,readiness}`.

**Outbound:**
- HTTP POST → Platform GraphQL `SubmitWorkflow` mutation
  (`integration/platform/PlatformGraphQLClient.java:100-104`), OAuth2 `client_credentials` M2M
  (`PlatformOAuth2TokenService.java:61`), wrapped by Resilience4j in
  `ResilientPlatformClient.java:34-54`
- HTTP GET → NEOS sequence-ID generator, static `x-api-key` header (`NeosClient.java:29-44`)
- Kafka produce → a topic config key (`AuditEventProducer.java:74`),
  a topic config key (`DlqEventProducer.java:248`), `webhook-service.topic`
  (`WebhookRequestProducer.java:115,185`) — all CloudEvents-encoded
- AWS → S3 (`S3Client`, `S3Presigner`, CRT async + `S3TransferManager`,
  `integration/s3/S3ClientConfig.java:41,53,71,93`), Secrets Manager
  (`AwsSecretsManagerConfig.java:23`)
- **No gRPC. No AKST virtual queues.**

**Data owned:** **16 tables**, its own schema, Flyway `V1`–`V11`, Hibernate `ddl-auto: validate`.
13 have JPA entities: `clients`, `category`, `workflows`, `workflow_client_configs`, `tasks`,
`task_status_history`, `retry_config`, `retry_queue_entry`, `state_order_buffer`, `file_objects`,
`file_download_log`, `partners`, `client_partner_relation`.
3 have migrations but **no entity and no repository**: `dead_letter_queue`
(`V1__init.sql:313`), `file_allowed_content_types` (`:398`), `shedlock` (`:432`).
ShedLock's table exists but **no `@SchedulerLock` annotation exists anywhere** — with
`replicaCount: 1` the three `@Scheduled` jobs are unguarded by design.

**Config keys:** ~140 keys across `application.yml` (250 lines) and `application-local.yml`.
Names only.
- **OAuth2 / Auth0:** `spring.security.oauth2.resourceserver.jwt.issuer-uri` ←`AUTH0_ISSUER_URI`,
  `….jwt.audiences` ←`AUTH0_AUDIENCE`, `….jwt.jwk-set-uri` ←`AUTH0_JWKS_URI` (**blank committed
  default**, `application-local.yml:43`), `spring.security.oauth2.client.registration.platform.{client-id,client-secret,scope,authorization-grant-type}`,
  `spring.security.oauth2.client.provider.platform.token-uri`, `security.jwt.client-id-claim`,
  `security.jwt.roles-claim`, `app.jwt.decoder`, `app.jwt.expected-audience`,
  `app.jwt.public-key-pem`, `aws.secretsmanager.public-key-secret-name`,
  `workflow.security.local-dev-bypass-jwt`, `workflow.file.client-token.{enabled,audience}`.
- **S3 presigned URLs:** `file-service.s3.bucket` (used as a **prefix**, not a bucket name —
  `S3FileService.java:165-179`), `file-service.s3.region`, `file-service.s3.presigned-url-ttl`,
  `file-service.s3.endpoint-override` (**bound, never applied to any builder**),
  `file-service.download.{streaming-threshold-bytes,max-file-size-bytes,stream-buffer-size}`.
- **Bucket4j:** `workflow.rate-limit.enabled`, `workflow.rate-limit.default-requests-per-second`,
  `workflow.rate-limit.default-burst-size` — all three **hardcoded literals** in
  `application.yml:163-165`, not env-injected.
- **Resilience4j:** **zero `resilience4j.*` YAML keys and zero annotations.** Configured
  programmatically in `infrastructure/config/ResilienceConfig.java` under the single instance
  name `"platformClient"` (`:27`): CircuitBreaker (`:44-78`), Bulkhead (`:98-120`), TimeLimiter
  (`:138-158`). Keys: `platform.circuit-breaker.{sliding-window-size,minimum-number-of-calls,failure-rate-threshold,wait-duration-in-open-state,permitted-number-of-calls-in-half-open-state,slow-call-duration-threshold,slow-call-rate-threshold}`,
  `platform.bulkhead.{max-concurrent-calls,max-wait-duration}`, `platform.read-timeout`.
  **`platform.retry.{max-attempts,wait-duration,multiplier}` are declared and set but
  `PlatformConfig.getRetry()` is never called** — no Resilience4j `Retry` exists.
- **Env-injected with NO default** (36 keys — unset ⇒ startup failure): `spring.datasource.{url,username,password}`,
  `spring.kafka.bootstrap-servers`, `kafka.topics.{workflow-updates,dlq,audit-events}`,
  `webhook-service.topic`, `platform.graphql-url`, `platform.oauth2.*`, `aws.region`,
  `neos.{base-url,api-key}`, `file-service.*`, `retry-processor.*`, `app.version`.
- **Env-injected with a BLANK committed default:** `workflow.platform.status-action-configs`
  (`application.yml:150`) and `spring.security.oauth2.resourceserver.jwt.jwk-set-uri`
  (`application-local.yml:43`).
- **Dead config** (committed, read by nothing): `audit.cloudevents.enabled`,
  `security.jwt.roles-claim`, `workflow.webhook.m2m.*`, `workflow.platform.timeout-ms`,
  `retry-processor.batch-size`, `retry-processor.processor-id`, `platform.retry.*`,
  `webhook-service.destination-id-prefix`, `workflow.webhook.secret`,
  `file-service.s3.endpoint-override`, `file-service.s3.bucket-prefix`.
- **Deployment configs carry blank values only.** In all five
  `deployment-configs/{dev,qa,test,demo,prod}-config.yaml` every `env` entry has an empty
  `value:` except `SERVER_PORT`, two OAuth2 literals, and `${AUTH0_*}` pass-throughs; the
  env-var **name lists are byte-identical across all five environments**, and `image.tag` is
  identical in all five. Values are injected by the deploy pipeline and are never recorded here.

**ETP role:** Task Initiator only, and over **HTTP/GraphQL**, not over AKST. It mints its own
`taskId` in its own `tasks` table and submits via the Platform GraphQL mutation; it never
dispatches to a virtual queue and never plays Router, Queue, Worker or Reader.

**Failure modes:**
- **`/app/health` returns 401, not 200.** `JwtAuthenticationFilter` is a plain `@Component`
  (`:59`) with `@Order(HIGHEST_PRECEDENCE + 50)` (`:60`), so Spring Boot auto-registers it into
  the servlet chain *ahead of* `FilterChainProxy`. Its `shouldSkip` allowlist (`:171-179`) omits
  `/app/health` (it lists an unmapped `/health` instead), while `SecurityConfig.java:76`
  permitAll's it. The k8s/Docker health probe path and the security policy disagree →
  workflow-api-health-path-not-in-jwt-filter-allowlist (archived).
- **Scope checking is substring containment.** `JwtAuthenticationFilter.validateScope` uses
  `scopes.contains(requiredScope)` (`:536`), so a token with `workflow:readonly` satisfies a
  `workflow:read` requirement. `ClientTokenValidator.hasRequiredScope` (`:186-193`) does the
  same check correctly with a whitespace split — the two disagree in the same codebase.
- **`POST /v2/tasks` has no `@PreAuthorize`** — `TaskQueryController.java:25` carries
  `TODO: Add security configuration later`; only `.anyRequest().authenticated()` applies.
- **`/v1/**` is `permitAll()` at the Spring Security layer** (`SecurityConfig.java:85`); the
  file endpoints' only gate is the imperative `ClientTokenValidator`, which is disabled
  wholesale when `workflow.file.client-token.enabled` is false (then returns 503).
- **Rate limiting does not cover the file endpoints.** `RateLimitFilter.java:61-66` lets a
  request through un-limited when `WorkflowSecurityContext` is absent, and
  `JwtAuthenticationFilter` skips `/v1/**`, `/v2/internal/**`, `/v2/core/**`, `/actuator/**`.
  Buckets are an in-process `ConcurrentHashMap` (`RateLimitService.java:31`) — **no Redis**, so
  limits are per-pod.
- **`app.jwt.decoder=jwks` (the documented local default) cannot start the app.**
  `CustomTokenJwtDecoder` is an unconditional `@Component` whose constructor requires
  `TokenValidationService`, which is `@ConditionalOnExpression` on `app.jwt.decoder == 'custom'`
  AND `local-dev-bypass-jwt == false`. The `@Primary` `SecurityConfig.jwtDecoder()` bean is dead
  code — every consumer uses `@Qualifier("customJwtDecoder")`.
- **OAuth2 audience is hardcoded.** `OAuth2ClientConfig.authorizedClientManager` (`:89-114`)
  injects `@Value("${platform.audience}")` then shadows it with `final String aud = "/api";`
  (`:93`), which is what is actually sent (`:99`); the real logic is commented out at `:94`.
- Kafka consumer uses `StringDeserializer` for key **and** value
  (`KafkaConsumerConfig.java:52-53`), contradicting `spring.kafka.consumer.value-deserializer:
  JsonDeserializer` in `application.yml:47` (that key only affects the autoconfigured factory).
- CORS allows origin pattern `*` **with** `setAllowCredentials(true)` (`SecurityConfig.java:183,198`).

**Code-level landmarks:**
- `infrastructure/security/SecurityConfig.java:65-108` — the whole filter chain and the
  permitAll/authenticated split
- `infrastructure/filter/JwtAuthenticationFilter.java:171-179` (skip list), `:385-542`
  (dynamic-path authz, client/category lookup, scope mapping)
- `inbound/controller/SubmissionController.java:66,124` — the catch-all `POST /**` / `GET /**`
- `integration/s3/S3FileService.java:239-259` — `generatePresignedUrl`
- `infrastructure/config/ResilienceConfig.java:27` — the single `"platformClient"` instance name
- `infrastructure/filter/RateLimitFilter.java:61-66` — the context-absent bypass

**Known divergences:**
workflow-api-health-path-not-in-jwt-filter-allowlist (archived) ·
workflow-api-docs-describe-nonexistent-file-endpoint (archived) ·
workflow-api-scope-check-is-substring-match (archived)

**Relevant runbooks:** none yet.

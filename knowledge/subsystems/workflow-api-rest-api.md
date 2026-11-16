---
type: api
protocol: rest
endpoint: http://workflow-api-pl-subsystem:8081
owner: workflow-api
schema_dir: src/main/java/com/company/workflow/inbound/controller
endpoint_count: 10
controller_count: 7
preauthorize_count: 3
provenance: code
verified: 2026-10-02
---

# Workflow API REST API

Exposed by [workflow-api](workflow-api.md) at `release/v1.4.4` (`bb91723`) on
port `8081` (`server.port`, `Dockerfile:45`, `deployment-configs/*:230-231`).

**Exact count: 10 code-declared handler methods across 7 `@RestController` classes.** There are
no `@DeleteMapping` and no `@PatchMapping` anywhere, and exactly **3** `@PreAuthorize`
annotations in the whole repo.

## Authorization model — read this before the table

Three *independent, non-equivalent* gates exist and different endpoints are covered by
different subsets:

| Gate | Where | Applies to |
|---|---|---|
| **G1** Spring Security matchers | `infrastructure/security/SecurityConfig.java:72-98` | all requests |
| **G2** `@PreAuthorize` method security | enabled by `@EnableMethodSecurity(prePostEnabled=true)` `SecurityConfig.java:46` | 3 handlers |
| **G3** `JwtAuthenticationFilter` — imperative client/category/scope authz | `infrastructure/filter/JwtAuthenticationFilter.java:385-542`, `@Order(HIGHEST_PRECEDENCE+50)` `:60` | everything **not** in its skip list `:171-179` |

**G3's skip list** is `/actuator/`, `/v2/internal/`, `/v2/core/`, `/v1/`, `/health`,
`/v3/api-docs`, `/swagger-ui`. Note `/health` is **not a mapped path** and `/app/health` —
which *is* mapped and *is* the configured probe — is **absent**, so the probe path is subject
to G3 and returns 401. See
workflow-api-health-path-not-in-jwt-filter-allowlist (archived).

**G1 matchers, in declaration order:**

| Rule | Line | Paths |
|---|---|---|
| `permitAll` | `:74-82` | `/actuator/health/**`, `/app/health`, `/actuator/info`, `/actuator/prometheus`, `/v3/api-docs/**`, `/swagger-ui/**`, `/swagger-ui.html` |
| `permitAll` | `:85` | `/v1/**` — comment: "use custom ClientTokenValidator, not Spring Security OAuth2" |
| *(commented out)* | `:87` | `/v2/webhooks/**` — the matcher the docs still claim exists |
| `authenticated` | `:90` | `/v2/internal/**` |
| `hasAuthority("SCOPE_file:read")` | `:93` | `/v2/core/**` — **orphaned: no controller maps this prefix** |
| `authenticated` | `:98` | `.anyRequest()` |

**JWT:** OAuth2 resource server, `jwtAuthenticationConverter` at `SecurityConfig.java:162-172`
with `setAuthoritiesClaimName("scope")` and `setAuthorityPrefix("SCOPE_")`. No `ROLE_` mapping
is implemented despite the javadoc at `:158`. Two decoder beans compete: `@Primary
SecurityConfig.jwtDecoder()` (`:117-119`, Nimbus/JWKS/OIDC-discovery) is **dead code** — every
consumer injects `@Qualifier("customJwtDecoder")` → `CustomTokenJwtDecoder` →
`TokenValidationService` (RSA public key from inline PEM or Secrets Manager, jjwt, issuer +
audience required). Principal resolution order
(`JwtAuthenticationFilter.extractExternalClientId:477-500`):
`${security.jwt.client-id-claim}` → `azp` → `sub` split on `@clients`.

## Endpoints (10)

| # | Method | Path | Handler file:line | Auth |
|---|---|---|---|---|
| 1 | POST | `/**` (catch-all; matched against `category.full_path`) | `SubmissionController.java:66` (class `:48`, `@Order(LOWEST_PRECEDENCE)` `:50`) | G1 `.anyRequest().authenticated()` + **G3**: client from JWT claim → category lookup (`:429`) → scope **`workflow:submit`** (`:505-513`) → active `workflow_client_config` (`:441`) |
| 2 | GET | `/**` (catch-all; last segment = taskId/entityId, parsed `:332-354`) | `SubmissionController.java:124` | same as #1 but scope **`workflow:read`** (`:508`) |
| 3 | POST | `/v2/webhooks/platform` | `StatusUpdateController.java:61` (class `@RequestMapping("/v2/webhooks")` `:42`) | **G2 `@PreAuthorize("hasAuthority('SCOPE_platform:webhook')")` `:62`** + G1 `.anyRequest().authenticated()` (its own matcher is commented out at `SecurityConfig.java:87`) + **G3 also runs** (not skipped, `:171-179`) |
| 4 | POST | `/v2/tasks` | `TaskQueryController.java:42` (class `@RequestMapping("/v2/tasks")` `:29`) | **No `@PreAuthorize`** — `TODO: Add security configuration later` at `:25`. G1 authenticated + G3, which for a "predetermined controller" path demands either `clients.is_admin = true` (`:288-291`) or a successful dynamic-path/category match (`:229`) |
| 5 | POST | `/v2/webhook/availability?destination=…` | `WebhookAvailabilityController.java:43` (class `@RequestMapping("/v2/webhook")` `:31`, `@Validated` `:29`) | No `@PreAuthorize`. **Admin-only, enforced imperatively**: G3 `buildMinimalContext` requires `Boolean.TRUE.equals(client.getIsAdmin())` (`:288-291`); controller then calls `WorkflowSecurityContext.requireContext()` (`:59`) |
| 6 | POST | `/v2/internal/file-objects` | `InternalFileController.java:49` (class `@RequestMapping` `:38`) | **G2 `@PreAuthorize("hasAuthority('SCOPE_internal:file:write')")` `:50`** + G1 authenticated (`SecurityConfig.java:90`). **G3 skipped** (`:173`) |
| 7 | GET | `/v2/internal/file-objects/{objectId}` | `InternalFileController.java:78` | **G2 `@PreAuthorize("hasAuthority('SCOPE_internal:file:read')")` `:79`** + G1 authenticated. G3 skipped |
| 8 | PUT | `/v1/file-object` (multipart `file`) | `FileController.java:106` (class `@RequestMapping("/v1")` `:70`, `@Transactional` `:107`) | **G1 `permitAll`** (`SecurityConfig.java:85`), **G3 skipped** (`:175`). Auth is fully manual: `resolveFileAuthentication` `:132` → `:322-367`, scope **`file:write`** (`:90`) via `ClientTokenValidator.validateAndExtract` (`:84`). Plus `DomainRoutingFilter` (`:30`, order `HIGHEST_PRECEDENCE+10`) **404s** unless `Host`/`X-Forwarded-Host` starts with `routing.blob-domain-prefix` (`:39-42,54-61`) |
| 9 | GET | `/v1/file-object/{objectId}` | `FileController.java:246` | same as #8 but scope **`file:read`** (`:89`, used `:274`); access control `FileAccessControlService.canAccess:40` + `isWithinRateLimit:90`; partner mode keyed on header **`X-Sub-Tenant-ID`** (`:91,330`). Returns **302 + `Location`** when the file's `DeliveryMethod == PRESIGNED_URL` (`:289-294`) |
| 10 | GET | `/app/health` | `AppHealthController.java:15` (delegates to `HealthEndpoint.health()` `:16`) | G1 `permitAll` (`SecurityConfig.java:76`) — **but G3 is not skipped for this path, so an unauthenticated probe gets 401** |

**Not endpoints:** `@RestControllerAdvice GlobalExceptionHandler`
(`infrastructure/exception/GlobalExceptionHandler.java:28`) and four local `@ExceptionHandler`
methods in `FileController` (`:390,400,410,421`).

## Framework-provided endpoints (not code-declared)

Actuator, base path `/actuator` (`management.endpoints.web.base-path`), exposure list
`health,info,metrics,prometheus` (`management.endpoints.web.exposure.include`):
`/actuator/health`, `/actuator/health/liveness`, `/actuator/health/readiness`
(`management.endpoint.health.probes.enabled`), `/actuator/info`, `/actuator/metrics`,
`/actuator/prometheus`. Custom indicator `platform` →
`health/PlatformHealthIndicator.java:18` (key `platform.health-check-url`, **blank default**, so
it reports UNKNOWN unless set). springdoc: `/v3/api-docs/**`, `/swagger-ui/**`.
Docker `HEALTHCHECK` polls `/actuator/health` every 30 s (`Dockerfile:48-49`).

## Rate limiting (Bucket4j 8.7.0)

`RateLimitFilter` `infrastructure/filter/RateLimitFilter.java:34`,
`@Order(HIGHEST_PRECEDENCE + 100)` `:32`, `doFilterInternal` `:45`.
Bucket key = `"workflow-client:" + clientConfig.getId()` or `"unknown:" + externalClientId`
(`WorkflowSecurityContext.java:147-152`). `Bandwidth.builder().capacity(burstSize)
.refillGreedy(rps, Duration.ofSeconds(1))` (`RateLimitService.java:130-133`), stored in an
**in-process `ConcurrentHashMap`** (`:31`) — limits are per-pod, there is no Redis.
Response headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`,
`Retry-After` (`:36-39,103-116`); 429 body built at `:126-138`.
Skipped prefixes: `/actuator`, `/swagger`, `/v3/api-docs`, `/v2/admin` (`:84-89`).
**Bypass:** if `WorkflowSecurityContext` is absent the request passes un-limited (`:61-66`) —
which, combined with G3's skip list, means **endpoints 6–10 are effectively unlimited**.
Keys: `workflow.rate-limit.enabled`, `workflow.rate-limit.default-requests-per-second`,
`workflow.rate-limit.default-burst-size` — all three hardcoded literals in
`application.yml:163-165`, **not** env-injected.
A separate, unrelated per-file quota (`FileObject.maxDownloadsPerHour`) is enforced by
`FileAccessControlService.isWithinRateLimit:90` and is **not** Bucket4j.

## Scopes referenced anywhere in the codebase

`workflow:submit` · `workflow:read` · `platform:webhook` · `internal:file:write` ·
`internal:file:read` · `file:write` · `file:read` · `platform:submit,platform:read` (the
outbound M2M scope in `deployment-configs/*:187`).

⚠ `JwtAuthenticationFilter.validateScope` matches by **substring containment**
(`scopes.contains(requiredScope)`, `:536`), so `workflow:readonly` satisfies `workflow:read`.
`ClientTokenValidator.hasRequiredScope` (`:186-193`) does the same job correctly with a
whitespace split. See
workflow-api-scope-check-is-substring-match (archived).

## CORS

`SecurityConfig.corsConfigurationSource:181-204` — allowed origin **patterns `*`** (`:183`)
together with `setAllowCredentials(true)` (`:198`); methods GET/POST/PUT/PATCH/DELETE/OPTIONS
(`:184`); allowed headers include `X-Sub-Tenant-ID` (`:191`); exposed headers include the four
rate-limit headers (`:192-197`); `maxAge` 3600 (`:199`).

## Endpoints the docs claim that do not exist

`GET /v2/core/file-object/{objectId}` — asserted at `README.md:221,260` and
`docs/TSD.md:358,388,467`; no controller maps `/v2/core`. See
workflow-api-docs-describe-nonexistent-file-endpoint (archived).

## Real endpoints missing from every doc

`POST /v2/tasks`, `POST /v2/webhook/availability`, `PUT /v1/file-object` appear in neither
`README.md:162-243` nor `docs/TSD.md:383-396`.

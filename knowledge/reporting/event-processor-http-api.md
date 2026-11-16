---
type: api
name: Event Processor HTTP API
protocol: http
owner: event-processor
repo_path: platform-whole-context/subsystem-workflow-interaction-support/e5-platform-ss-event-processor
base_path: /api/v1/event
port: 8080
auth: none (no spring-security on the classpath)
provenance: code
verified: 2026-10-02
---

# event-processor HTTP API

Two undocumented POST endpoints that **replay Kafka message handling over HTTP**. They exist for manual testing but are deployed unconditionally. Owned by [event-processor](event-processor.md).

## Operations

### Event replay
| Operation | Path | Body | Touches |
|---|---|---|---|
| `processEvents` (`EventProcessorController.java:46`) | `POST /api/v1/event/processor` | `EventRequestDTO` (CloudEvent fields, `EventRequestDTO.java:17-35`) | Calls `KafkaListenerService.webhookListener` with a synthetic `ConsumerRecord` whose topic is the literal `"my-topic"` (`:49-56`) → deployment-mgmt `findWebhooksByEventTypeAndDeploymentVersionId` + `saveEventStatus`, then produces to a topic config key |
| `processActionEvents` (`:68`) | `POST /api/v1/event/action` | raw JSON string | Calls `KafkaListenerService.actionListener` with topic `"my-topic"` (`:71-77`) → deployment-mgmt `updateWebhookStatusById` and/or `updateEventStatusById` |

### Not present
No GET/list/status endpoints, no webhook CRUD (that is deployment-mgmt), no actuator (no actuator dependency in `build.gradle:21-56`), no OpenAPI security scheme — `springdoc-openapi-ui` is on the classpath (`build.gradle:26`) so `/swagger-ui.html` advertises both endpoints.

## Response and error semantics
Both methods return `void` → HTTP 200 with an empty body on success. `GlobalExceptionHandler` also returns `void` for `StatusRuntimeException` (`:22`) and `Exception` (`:40`), logging only — so **a failed call is indistinguishable from a successful one**. The processor path can additionally throw `RuntimeException` out of `EventProcessorServiceImpl.java:88`, which the handler converts to another silent 200.

## Risk
Anyone with pod/network reach can drive real webhook dispatch and mutate `event_status`/`webhook` rows through deployment-mgmt with no authentication and no audit record. See ../../disagreements/event-processor-unauthenticated-http-backdoor.md (archived). Note the committed `deployment-configs/*.yaml` expose only port 50051 in the k8s Service, so reachability today is pod-local/cluster-internal rather than via the Service.

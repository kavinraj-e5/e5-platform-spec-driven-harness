---
type: contract
name: DeploymentManagementService — webhook/event RPC subset
kind: grpc-proto
owner: deployment-mgmt
source: platform-whole-context/subsystem-workflow-interaction-support/e5-platform-ss-event-processor/src/main/proto/deployment_mgmt_service.proto
proto_package: com.e5.platform.ss.proto
consumers: [event-processor]
provenance: code
verified: 2026-10-02
---

# DeploymentManagementService — webhook/event RPC subset

The **vendored copy** of deployment-mgmt's proto that [event-processor](../reporting/event-processor.md) compiles against. The authoritative definition lives in [deployment-mgmt](../subsystems/deployment-mgmt.md); this file declares 13 RPCs and event-processor calls 7 of them. A drift between the two copies is silent — there is no shared artefact.

## Operations declared here (`deployment_mgmt_service.proto:136-159`)
### Webhook registry
| RPC | Used by event-processor | Touches |
|---|---|---|
| `SubscribeWebhook(Webhook)→Webhook` (`:107`) | no | DMS `webhooks` |
| `FindWebhooksByEventType(EventType)→Webhooks` (`:108`) | `GrpcServiceImpl.java:92` — **dead**, `getWebhooksByEventType` has no caller | DMS `webhooks` |
| `FindWebhookById(WebhookId)→Webhook` (`:109`) | no | DMS `webhooks` |
| `UpdateWebhookStatusById(Webhook)→Webhook` (`:110`) | `GrpcServiceImpl.java:118` ← action listener | DMS `webhooks.isEnabled` |
| `FindWebhooksByEventTypeAndDeploymentVersionId(...)→Webhooks` (`:123`) | `GrpcServiceImpl.java:34` — the primary resolution path | DMS `webhooks` + subscription join |
| `FindWebhooksByEventTypeAndWorkflowDetails(...)→Webhooks` (`:126`) | `GrpcServiceImpl.java:69` — fallback for deployment events with no `deploymentVersionId` | DMS `webhooks` + workflow/variant/client lookup |

### Event status
| RPC | Used by event-processor | Touches |
|---|---|---|
| `SaveEventStatus(EventStatus)→EventStatus` (`:113`) | `GrpcServiceImpl.java:112`, status `IN_PROGRESS` (`Constants.java:91`) | DMS `tbl_event_status` |
| `GetEventStatusForScheduler(SchedulerRequest)→EventWebhookStatusList` (declared in DMS `deployment_management.proto:2372`) | **No longer called** at event-processor `release/v1.2.1`: the cron sweep (`SchedulerService.java:106`) now calls `getFailedEventsWebhooksList(NewSchedulerRequest)` (`GrpcServiceImpl.java:132`), an RPC that **no DMS release up to v1.9.8 declares** - it exists only on DMS feature branches (`feat/add-event-usecases-for-event-processor`). Verified 2026-11-14 | DMS `tbl_event_status` ⨝ `webhooks` |
| `UpdateEventStatusById(EventStatus)→EventStatus` (`:115`) | `GrpcServiceImpl.java:125` ← action listener | DMS `tbl_event_status.status` |
| `GetEventStatusById(EventStatusId)→EventStatus` (`:116`) | no | DMS `tbl_event_status` |

### Event catalogue
| RPC | Used by event-processor | Touches |
|---|---|---|
| `SaveEvent(Event)→Event` (`:119`) · `GetAllEvents(Empty)→Events` (`:120`) · `FindEventsByIds(EventIds)→Events` (`:121`) | none | DMS `events` |

## Fields that matter downstream
`Webhook` (`:69-77`) carries `id, webhook_name, secure_key, url, auth_type, events[], isEnabled` — `secure_key` and `url` are copied straight into the Kafka dispatch record ([webhook-dispatch-dto](webhook-dispatch-dto.md)). `EventWebhookStatus` (`:43-53`) is `EventStatus` plus `secure_key, url, auth_type`, which is what lets the cron sweep rebuild a dispatch without a second lookup. `EventStatus.event` (`:39`) is "Store JSON as a string" — the entire CloudEvent, re-parsed at `SchedulerService.java:171`.

## Transport caveats
`ManagedChannelBuilder...usePlaintext()` (`ManagedChannelConfig.java:28-30`): no TLS, no auth, no per-call deadline. Blocking stubs only, so a slow DMS blocks a Kafka listener thread until the TCP/keepalive layer gives up. `GrpcServiceImpl` replaces every status with `DatabaseDataRetrievalException("...is empty...")` (`:46`, `:80`), losing the original gRPC `Status`.

---
type: contract
name: PlatformInterface (proto)
fqn: com.e5.platform.ss.proto.PlatformInterface
java_package: com.e5.management.proto
owner: unowned
source: e5-deployment-mgmt-service/src/main/proto/deployment_management.proto:318-320
transport: grpc
rpc_count: 1
provenance: code
verified: 2026-10-02
---

# Contract: `PlatformInterface`

A one-rpc service **declared inside [deployment-mgmt](../subsystems/deployment-mgmt.md)'s proto
but not served by deployment-mgmt.**

```
service PlatformInterface {
  rpc emitEvent(CloudEvent) returns (Empty);
}
```

`deployment_management.proto:318-320`.

## Why it is here and what it means

- **DMS does not implement it.** `Main.java:39-45` registers exactly three services:
  `DeploymentManagementServiceImpl`, `ProtoReflectionService`, `HealthServiceImpl`. There is no
  `PlatformInterfaceImpl` anywhere in `src/main/java`. A call to
  `/com.e5.platform.ss.proto.PlatformInterface/emitEvent` against DMS returns **UNIMPLEMENTED**.
- **It is a client-side stub generator.** Its practical effect is that every repo compiling
  DMS's proto gets a `PlatformInterfaceGrpc` stub in `com.e5.management.proto`, usable to call
  whichever service does host event emission. credential-monitor and monitoring-service instead
  call `EventStoreService.sendEvent(PlatformEvent)` from their own `monitor.proto`.
- Co-locating a service you do not serve in the proto you do serve is why the proto
  **over-reports coupling**: a dependency graph built from `.proto` files will draw an
  `emitEvent` edge into DMS that does not exist.

## `CloudEvent` message

`deployment_management.proto:238-247` — DMS's own hand-rolled CloudEvents shape, distinct from
the `io.cloudevents` library types that [workflow-api](../subsystems/workflow-api.md) and
webhook-service use on Kafka. The two are **not interchangeable**: one is a protobuf message,
the other a JSON/structured CloudEvent with `ce_` Kafka headers.

## Resolution

Either implement it in DMS or move the declaration to the repo that serves it. Until then, treat
`PlatformInterface` in DMS's proto as a client artifact only, and do not derive an edge from it.
See dms-declares-two-unserved-proto-services (archived).

---
type: contract
name: MonitoringService (proto)
fqn: com.e5.platform.ss.proto.MonitoringService
java_package: com.e5.management.proto
owner: credential-monitor
source: platform-whole-context/subsystem-workflow-interaction-support/e5-platform-ss-credential-monitor/src/main/proto/monitor.proto:43-45
transport: grpc
rpc_count: 1
provenance: code
verified: 2026-10-02
---

# Contract: `MonitoringService`

**The same service name is declared twice in the platform, with two different rpc sets, by two
different repos.** Resolving which one a caller means requires knowing the repo, not the name.

| Declaration | rpcs | Served? |
|---|---|---|
| `e5-deployment-mgmt-service/src/main/proto/deployment_management.proto:315-316` | **0 — empty block** | no |
| `e5-platform-ss-credential-monitor/src/main/proto/monitor.proto:43-45` | **1** | **yes** |
| `e5-monitoring-service/src/main/proto/mgmt.proto` | (its own copy) | yes — see [monitoring-service-grpc-api](../subsystems/monitoring-service-grpc-api.md) |

## The served contract — credential-monitor

```
service MonitoringService {
  rpc reInitiateUpdateWfStatusJob(Empty) returns (Empty);
}
```

- **Declaration:** `monitor.proto:43-45`
- **Implementation:** `MonitoringServiceImpl.java:54` (`extends
  MonitoringServiceGrpc.MonitoringServiceImplBase`), method at `:315-322`
- **Registration:** `GrpcServer.java:24` — the **only** service registered on the server
- **Port:** 50051, **hardcoded** at `GrpcServer.java:23` (not config-driven)
- **No health service and no reflection service are registered**, so the gRPC health protocol is
  unavailable and `grpcurl` cannot introspect this server.

**Behaviour:** cancels the `UPDATE_WF_STATUS` scheduled job and reschedules it with delay 0. It
takes no parameters, returns nothing, and is **not idempotent** — each call tears down and
restarts a Kafka Streams topology whose `application.id` is
`kafka.stream[streamName=updateWfStatus].groupId`.

**Caller:** workflow-ops, as `client.call("reInitiateUpdateWfStatusJob", ...)`.

## Why this matters

[credential-monitor](../subsystems/credential-monitor.md)'s entire inbound API is this one rpc.
Everything the service actually *does* — ledger mirroring and lock reclamation — is driven by
Kafka Streams and a Kubernetes watch, not by this contract. Anyone reading only the proto will
conclude the service is a no-op.

## The empty declaration in DMS

`MonitoringService` in DMS's proto has **zero rpcs** and is **not registered in
`Main.java:39-45`**. It generates an empty `MonitoringServiceGrpc` class in
`com.e5.management.proto` for every consumer that compiles DMS's proto — including a service-name
collision with the real one above, since both resolve to
`com.e5.platform.ss.proto.MonitoringService` on the wire. See
dms-declares-two-unserved-proto-services (archived).

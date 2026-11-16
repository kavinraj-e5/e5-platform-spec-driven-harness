---
type: disagreement
severity: high
subject: the health check reports HEALTHY when its own Kafka lag probe fails
sources:
  - code: e5-monitoring-service src/main/java/com/e5/service/monitors/HealthMonitor.java:172,229,232
  - code: e5-monitoring-service src/main/java/com/e5/service/MonitoringServiceImpl.java:381,382,395
commit_read: release/v1.5.9 / ac057d4
confirmed_in_deployed: release/v1.5.6
deployed_tag: v1.5.6-RELEASE-MONITOR-02-07-2026-003
verified: 2026-10-04
---

# The health check fails open when its lag probe throws

One fault, in the one service whose entire job is detecting unhealthy deployments.
A second fault previously recorded here **has been fixed upstream** — see the correction
at the bottom.

## A lag probe that throws is read as "no lag"

```java
// HealthMonitor.java @ release/v1.5.9
172:  List<ConsumerTopicInfo> laggedTopics = null;     // initialised null
      ...
223:      laggedTopics = compareConsumerState(consumerInfo, currentConsumerInfo);
229:  } catch (Exception e) {                          // swallows everything
232:  return laggedTopics;                             // returns null on any failure
```

```java
// MonitoringServiceImpl.java @ release/v1.5.9
381:  List<ConsumerTopicInfo> consumerTopics = (List<ConsumerTopicInfo>) HealthAction
            .handleHealthTask(HealthCheckComponent.a topic env var, deploymentVersionId);
382:  if (consumerTopics != null && !consumerTopics.isEmpty()) {
383:      restartServerList = consumerTopics.stream().map(ConsumerTopicInfo::getServerName).toList();
      }                                                // null -> list stays EMPTY
      ...
395:  if (isKafkaHealthy && isTopicExist && restartServerList.isEmpty()
          && isTemporalHealthy && isPostgresHealthy && isServicesHealthy) {
          message = "The Deployment is Healthy"
```

**`null` and "no lag" are the same value to the caller.** Any failure inside the lag probe —
broker unreachable, auth failure, timeout, deserialisation error — leaves `restartServerList`
empty, which the verdict reads as the healthy case. The check fails *open*, in the direction
that hides outages.

The same pattern repeats in the recheck path at `:461-463`.

## Why this is confirmed, not merely plausible

The earlier version of this card could only say the fault existed at an indexed commit two
minor versions behind QA. It is now read at `release/v1.5.9` (latest) **and** at
`release/v1.5.6` (deployed): `HealthMonitor` is unchanged between them at the relevant lines.
The fault is in the running code.

## Root cause

The status enum is binary (`{HEALTHY, UNHEALTHY}`) and cannot express "the check did not run",
so a probe has nowhere to report its own failure and the null collapses into success. See
monitoring-health-status-enum-cannot-express-check-failure (archived).

## Resolution

1. `probeKafkaLag` must distinguish "probe failed" from "no lag" — return a result type or
   re-throw; never a bare `null` that collapses into the success path.
2. Give the status enum a third state so a failed check is reportable as such.

---

## CORRECTED 2026-10-04 — fault 2 was fixed upstream and is withdrawn

This card previously carried a second fault, rated **critical** on the pair:

> **Pod health is computed, logged, and thrown away.** `isServicesHealthy` is assigned at
> `MonitoringServiceImpl.java:361` and does not appear in the verdict at `:365`.

That was true at `release/v1.4.4` / `57a18e2`. It is **not** true of anything shipped.
At `release/v1.5.9:395` the verdict reads:

```java
if (isKafkaHealthy && isTopicExist && restartServerList.isEmpty()
    && isTemporalHealthy && isPostgresHealthy && isServicesHealthy) {
```

Both `isServicesHealthy` and a new `isTopicExist` are now in the condition. The fix is present
in the **deployed** `release/v1.5.6` as well, so it was shipped before this card was written.

The card was read from `v1.4.4` because the registry had been seeded by reading whichever
branch each local working copy happened to be sitting on, rather than the release branch the
platform actually ships. The original card's own caveat — *"re-check against the deployed tag
before raising it as an incident"* — was the right instinct and is what caught this.

Severity drops **critical → high**: the compounding argument depended on fault 2 removing the
pod check that would have caught what fault 1 hides. Fault 1 alone still reports a deployment
healthy when its Kafka probe is broken, which is serious, but the pod sweep now runs and counts.

See indexed-checkouts-are-stale (archived).

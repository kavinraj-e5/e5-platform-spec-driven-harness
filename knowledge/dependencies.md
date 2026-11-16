# Dependencies (generated)

Extracted from build files (`graph/build-facts.jsonl`, `tools/build_graph.py`). Only internal E5 artifacts are listed. Repos with no extracted build facts are absent; absent does **not** mean no dependencies.

## What each repo depends on

| repo | publishes | depends on (artifact version) |
|---|---|---|
| `akst-service` | `e5-akst-service` 1.3.5 | `StateService` 1.2.9, `e5-task-sdk-java` 0.7.4 |
| `app-sdk` | `AppSDK` 1.10.5 | `e5-platform-core` 1.10.0 |
| `audit-service` | — | `e5-notification-hub-java-sdk` 0.1.4 |
| `credential-distribution-service` | `e5-ss-credential-distribution-service` 1.0.1 | — |
| `credential-monitor` | `e5-platform-ss-credential-monitor` 1.1.1 | `e5-task-sdk-java` 0.5.2 |
| `deployment-mgmt` | `e5-deployment-mgmt-service` 1.9.8 | — |
| `devops-pipeline` | `e5_platform_devops_sdk` 1.0 | — |
| `dynamic-scaling` | `e5-ss-dynamic-scaling-service` 1.3.2 | `e5-task-sdk-java` 0.7.4 |
| `event-processor` | `e5-platform-ss-event-processor` 1.2.1 | — |
| `event-store` | `e5-platform-ss-event-store` 1.1.4 | — |
| `eventstore-client-sdk` | `EventStoreSDK` 0.1.0 | `e5-common-utils` 0.2.5, `e5-common-utils` 0.2.6 |
| `interface` | — | `e5-task-sdk-java` 0.7.4 |
| `monitoring-service` | `e5-monitoring-service` 1.5.9 | `e5-task-sdk-java` 0.7.4 |
| `non-rpa-worker-core` | `nonrpa.worker.core` 0.3.3 | `e5-task-sdk-java` 0.6.2 |
| `platform-core` | `e5-platform-core` 1.10.0 | `StateService` 1.2.9, `e5-notification-hub-java-sdk` 0.2.2, `e5-task-sdk-java` 0.7.6 |
| `rf-fb-worker-core` | `e5-rf-fb-worker-core` 1.1.12 | `e5_task_sdk_python` 1.0.9 |
| `state-service` | `StateService` 1.2.8 | — |
| `task-reader` | `e5-task-reader` 1.0.5 | `e5-task-sdk-java` 0.6.2 |
| `task-sdk-java` | `e5-task-sdk-java` 0.7.6 | `e5-common-utils` 0.2.17 |
| `task-sdk-python` | `e5-task-sdk-python` 1.0.2 | — |
| `variant-scaffolding` | `ClientSampleworkflow` 1.33.8 | `AppSDK` 1.10.5, `SampleWorkflow` 1.11.5, `e5-platform-core` 1.10.0 |
| `wf-scaffolding` | `SampleWorkflow` 1.11.5 | `AppSDK` 1.10.5, `e5-platform-core` 1.10.0 |
| `workflow-manager` | `e5-workflow-manager-service` 1.4.7 | `e5-task-sdk-java` 0.7.4 |
| `workflow-ops` | `e5-platform-ss-workflow-ops` 1.7.4 | — |

## Who depends on each internal artifact

| artifact | used by (repo version) | versions in use |
|---|---|---|
| `AppSDK` | `variant-scaffolding` 1.10.5, `wf-scaffolding` 1.10.5 | 1 |
| `SampleWorkflow` | `variant-scaffolding` 1.11.5 | 1 |
| `StateService` | `akst-service` 1.2.9, `platform-core` 1.2.9 | 1 |
| `e5-common-utils` | `eventstore-client-sdk` 0.2.5, `eventstore-client-sdk` 0.2.6, `task-sdk-java` 0.2.17 | 3 (skew) |
| `e5-notification-hub-java-sdk` | `audit-service` 0.1.4, `platform-core` 0.2.2 | 2 (skew) |
| `e5-platform-core` | `app-sdk` 1.10.0, `variant-scaffolding` 1.10.0, `wf-scaffolding` 1.10.0 | 1 |
| `e5-task-sdk-java` | `akst-service` 0.7.4, `credential-monitor` 0.5.2, `dynamic-scaling` 0.7.4, `interface` 0.7.4, `monitoring-service` 0.7.4, `non-rpa-worker-core` 0.6.2, `platform-core` 0.7.6, `task-reader` 0.6.2, `workflow-manager` 0.7.4 | 4 (skew) |
| `e5_task_sdk_python` | `rf-fb-worker-core` 1.0.9 | 1 |

## Behind the newest release in their own major line

From `graph/library-versions.json` (JFrog). A release outside every consumer's line is listed after this table for the owner to confirm, and is never the target.

**Platform repos (17):**

| library | repo | pins | newest in line |
|---|---|---|---|
| `AppSDK` | `variant-scaffolding` | 1.10.5 | 1.18.3 |
| `AppSDK` | `wf-scaffolding` | 1.10.5 | 1.18.3 |
| `StateService` | `akst-service` | 1.2.9 | 1.3.0 |
| `StateService` | `platform-core` | 1.2.9 | 1.3.0 |
| `e5-common-utils` | `eventstore-client-sdk` | 0.2.6 | 0.2.146 |
| `e5-common-utils` | `eventstore-client-sdk` | 0.2.5 | 0.2.146 |
| `e5-common-utils` | `task-sdk-java` | 0.2.17 | 0.2.146 |
| `e5-notification-hub-java-sdk` | `audit-service` | 0.1.4 | 0.2.101 |
| `e5-notification-hub-java-sdk` | `platform-core` | 0.2.2 | 0.2.101 |
| `e5-task-sdk-java` | `akst-service` | 0.7.4 | 0.7.6 |
| `e5-task-sdk-java` | `credential-monitor` | 0.5.2 | 0.7.6 |
| `e5-task-sdk-java` | `dynamic-scaling` | 0.7.4 | 0.7.6 |
| `e5-task-sdk-java` | `interface` | 0.7.4 | 0.7.6 |
| `e5-task-sdk-java` | `monitoring-service` | 0.7.4 | 0.7.6 |
| `e5-task-sdk-java` | `non-rpa-worker-core` | 0.6.2 | 0.7.6 |
| `e5-task-sdk-java` | `task-reader` | 0.6.2 | 0.7.6 |
| `e5-task-sdk-java` | `workflow-manager` | 0.7.4 | 0.7.6 |

**Consumer repos outside the platform (41)** - customer workflows and apps, from `graph/consumer-build-facts.jsonl`:

| library | repo | pins | newest in line |
|---|---|---|---|
| `e5-platform-core` | `2.0-hchb-availity-auth` | 1.6.6 | 1.10.0 |
| `e5-platform-core` | `2.0-hchb-availity-auth` | 1.6.6 | 1.10.0 |
| `e5-platform-core` | `2.0-hchb-medicare-eligibility-alert-adjudication` | 1.3.5 | 1.10.0 |
| `e5-platform-core` | `2.0-hchb-medicare-eligibility-alert-adjudication` | 1.3.5 | 1.10.0 |
| `e5-platform-core` | `2.0-hchb-notification` | 1.5.14 | 1.10.0 |
| `e5-platform-core` | `2.0-hchb-notification` | 1.5.14 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-cashposting` | 1.5.10 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-cashposting` | 1.5.10 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-claims` | 1.2.2 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-claims` | 1.2.2 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-denials` | 1.5.14 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-denials` | 1.5.14 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-noa` | 1.5.3 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-noa` | 1.5.3 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-noe` | 1.5.14 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-noe` | 1.5.14 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-notr` | 1.5.14 | 1.10.0 |
| `e5-platform-core` | `2.0-wellskyrcm-notr` | 1.5.14 | 1.10.0 |
| `e5-platform-core` | `e5-fidelis-app-sdk` | 1.7.3 | 1.10.0 |
| `e5-platform-core` | `e5-fidelis-app-sdk` | 1.7.3 | 1.10.0 |
| `e5-platform-core` | `e5-waystar-app-sdk` | 0.9.0 | 0.10.5 |
| `e5-platform-core` | `e5-waystar-app-sdk` | 0.9.0 | 0.10.5 |
| `e5-platform-core` | `e5-wellsky-app-sdk` | 0.9.0 | 0.10.5 |
| `e5-platform-core` | `e5-wellsky-app-sdk` | 0.9.0 | 0.10.5 |
| `e5-platform-core` | `hchb-medicare-eligibility-alert-adjudication-base-variant` | 1.8.0.2 | 1.10.0 |
| `e5-platform-core` | `wellsky-cashposting-base-variant` | 1.2.2 | 1.10.0 |
| `e5-platform-core` | `wellsky-claims-base-variant` | 1.8.14 | 1.10.0 |
| `e5-platform-core` | `wellsky-noa-base-variant` | 1.8.14 | 1.10.0 |
| `e5-platform-core` | `wellsky-noe-base-variant` | 1.8.0.2 | 1.10.0 |
| `e5-platform-core` | `wellsky-notr-base-variant` | 1.8.0.2 | 1.10.0 |
| `e5_rf_fb_worker_core` | `e5-rf-fb-worker-scaffolding` | 0.0.1 | 0.6.4 |
| `e5_rf_fb_worker_core` | `e5-rf-reg-fb-worker` | 0.3.0 | 0.6.4 |
| `e5_rf_fb_worker_core` | `rf-fidelis-fb-worker` | 1.0.18 | 1.1.22 |
| `e5_rf_fb_worker_core` | `rf-hchb-fb-worker` | 1.0.14 | 1.1.22 |
| `e5_rf_fb_worker_core` | `rf-moveit-api-fb-worker` | 1.0.6 | 1.1.22 |
| `e5_rf_fb_worker_core` | `rf-optum-fb-worker` | 1.1.8 | 1.1.22 |
| `e5_rf_fb_worker_core` | `rf-palmetto-fb-worker` | 1.0.14 | 1.1.22 |
| `nonrpa.worker.core` | `e5-non-rpa-worker-java-scaffolding` | 0.3.1 | 0.3.3 |
| `nonrpa.worker.core` | `e5-non-rpa-worker-java-scaffolding` | 0.3.1 | 0.3.3 |
| `nonrpa.worker.core` | `non-rpa-hchb-fb-worker` | 0.2.3 | 0.3.3 |
| `nonrpa.worker.core` | `non-rpa-hchb-fb-worker` | 0.2.3 | 0.3.3 |

**Last deployed release outside every consumer's line (confirm whether real):** `AppSDK` 100.1.2.2.4, `SampleWorkflow` 100.1.2.2.8, `StateService` 100.7, `e5-common-utils` 11.2.3.1, `e5-platform-core` 100.0.0.1, `e5-task-sdk-java` 111.5.0, `e5_rf_fb_worker_core` 2.0.0

## Built FROM the fb-worker base image (`e5-rf-fb-base`)

| app repo | tag |
|---|---|
| `rf-fidelis-fb-worker` | v4.0.0 |
| `rf-hchb-fb-worker` | v4.0.0 |
| `rf-moveit-api-fb-worker` | v4.0.0 |
| `rf-optum-fb-worker` | v4.0.0 |
| `rf-palmetto-fb-worker` | v4.0.0 |

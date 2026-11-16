---
type: design-doc
status: in-flight
provenance: design-doc
subject: smart safe deployment
source: ~/Proj/platform/Understandwebhoook/docs/smart_deployment/ (copied 2026-11-13)
state: not committed to any repository; see ../smart-safe-deployment.md
---

> **In-flight design, not platform behaviour.** Copied unchanged from the working
> documents of the smart-safe-deployment effort. Its code references point at an
> uncommitted working copy and are not checked. For what safe deployment does today
> see [safe-deployment](../../../flows/safe-deployment.md).

# Deployment Pipeline Validation Reference and Configuration Checks

# Deployment Pipeline: Validation Reference

**Scope:** `deployment/Jenkinsfile` (non-prod), plus the scripts and libraries it calls.
**Source of truth:** `Jenkinsfile`, `bash/*.sh`, `libs/insert-workflow-api-details/`, `libs/deployment-config-generator/`.
**Status of this page:** Generated from a read-only review of the code. No pipeline code was changed.

---

## 1. Purpose

The pipeline deploys a workflow variant and its FB workers. It checks the deployment request and the workflow config before anything is provisioned or deployed. Any failed check stops the build, and the failing stage is recorded in the build description as `failure-stage: <name>`.

---

## 2. Pipeline flow and where each validation runs

| # | Stage | Validation performed |
| --- | --- | --- |
| 0 | Build parameters (Jenkins) | Input format checks (§3) |
| 1 | Checkout Config repo | Branch exists and credentials work |
| 2 | Insert Workflow API Details | Schema file completeness, partner config, destination config (§4) |
| 3 | Generate Config Files | Config merge, timezone, platform-core gating, timeouts, replicas, per-service rules (§5, §6) |
| 4 | Provisioning Resources | Not covered here (resource creation) |
| 5 | Deploy Variant & FB workers | Not covered here |
| 6 | Webhook Trigger | `catchError`, so a failure marks the stage failed but the build continues |

The whole pipeline has a **30-minute timeout** (`options.timeout`).

---

## 3. Stage 0: Jenkins parameter validation

These checks run at build time through the `validatingString` and `choice` parameters.

| Parameter | Rule | Default | Error shown |
| --- | --- | --- | --- |
| `WORKFLOW_NAME` | Regex `^[a-zA-Z0-9-]+$` | `wellsky-claims` | "Workflow name can not have spaces or special characters, use hyphen" |
| `CLIENT_NAME` | Regex `^[a-zA-Z-0-9]+$` | `wellsky-saunders` | "Client name can not have spaces or special characters, use hyphen" |
| `VARIANT_NAME` | Regex `^[a-zA-Z-0-9]+$` | `homehealth-claims` | "Variant name can not have spaces or special characters, use hyphen" |
| `CONFIG_VERSION` | Regex `^[a-zA-Z0-9]*$` | `v1` | "Config verion supports only alpha numeric characters and "."" |
| `CONFIG_REPO_BRANCH` | Regex `^[^ @$*&^(){}\\[\\]+~]+$` (no space, `@ $ * & ^ ( ) { } [ ] + ~`) | `main` | "Branch name can not have spaces or special characters, use hyphen" |
| `APP_TYPE` | Must be one of `WEB`, `DATADOG_LOGS` | none | Not applicable |
| `ENVIRONMENT` | Must be one of `DEV`, `QA`, `TEST` | none | Not applicable |
| `CLUSTER` | Must be `non-prod` | none | Not applicable |

**Normalisation:** `WORKFLOW_NAME`, `CLIENT_NAME`, `VARIANT_NAME`, `ENVIRONMENT`, `CONFIG_REPO_BRANCH`, `APP_TYPE` and `CLUSTER` are lower-cased before use.

**Known inconsistency:** The `CONFIG_VERSION` error message says `"."` is allowed, but the regex does not allow it. A value such as `v1.2` is rejected.

---

## 4. Stage 2: Workflow API details validation

This stage uses `libs/insert-workflow-api-details`. The config is read from `config-repo/{env}/{workflow}/{variant}/{client}/{config_version}/`.

### 4.1 Config repo layout

```
{environment}/
  {workflow_name}/
    InputValidation.schema.json
    InputTransformation.schema.json
    StatusExtractionConfig.json
    StatusActionConfig.json
    OrderingConfig.json
    WorkflowDestinationConfig.json
    NeosContextConfig.json            (optional)
    {variant_name}/{client_name}/{config_version}/
      config.yaml
      <client-level overrides, optional>
      ClientDestinationConfig.json
```

### 4.2 Rules

| # | Check | Rule | Result on failure |
| --- | --- | --- | --- |
| 1 | Workflow schema files | If any of the six workflow-level files exists, all six must exist. | Fail: "Incomplete workflow-level schema files" |
| 2 | Client files without workflow files | Client-level schema files exist but no workflow-level file does. | Fail: "Client-level schema files found but workflow-level schema files are missing" |
| 3 | No workflow files at all | None of the six exist. | Skip: the whole insertion is skipped and the build continues |
| 4 | JSON validity | Every schema file with content must be valid JSON. An empty or missing file counts as `None`. | Fail: `JSONDecodeError` |
| 5 | `NeosContextConfig.json` | Optional, but if present it must be a **JSON array**. | Fail: "must be a JSON array of rules" |
| 6 | Workflow display name | `WorkflowDestinationConfig.json` must exist and have a non-empty `name`. | Fail |
| 7 | Partner keys | `sub-tenant-id` and `partner-name` in `ClientDestinationConfig.json` must be present together or both absent. | Fail: "Both 'sub-tenant-id' and 'partner-name' must be present together…" |
| 8 | Partner values | When both keys are present, neither may be null, empty or whitespace. | Fail |
| 9 | `functionMode` | Must be `TASK_EVENTS` or `DOMAIN_EVENTS`. The default is `TASK_EVENTS`. | Fail: "Invalid functionMode" |
| 10 | Script arguments | Five arguments are required: workflow, client, variant, version, environment. | Exit 1 with a usage message |
| 11 | Client auth token | Token generation or insertion failure. | **Warning only.** The deployment continues. |

**Destination priority:** The client-level destination takes priority over the workflow-level destination. If neither exists, category and client-config insertion are skipped.

Any other exception in this stage is re-raised. The Jenkins stage then fails with `failure-stage: workflow-api-insertion`.

---

## 5. Stage 3: Config generation validation (`deployment-config-generator`)

### 5.1 Config merge and basic checks

Config is merged in this order, with later files overriding earlier ones. Each directory is searched for `common-config.yaml`, `workflow-common-config.yaml`, `variant-common-config.yaml`, `tenant-common-config.yaml` and `config.yaml`.

1. `config-repo/`
2. `{env}/`
3. `{workflow}/`
4. `{variant}/`
5. `{tenant}/`
6. `{config_version}/`

| Check | Rule | On failure |
| --- | --- | --- |
| Timezone | `timezone` (default `UTC`) must be in `pytz.all_timezones`. | `ConfigValidationError`: "Provided timezone X is invalid" |
| `fb_worker` | Must not be null. | "FB worker(s) are mandatory!" |
| Tenant lookup | `DEPLOYMENT_MANAMGENT_SERVICE` must be set and the client must resolve over gRPC. | Exception |
| Empty YAML | An empty file is logged and skipped. | Not an error |

### 5.2 Platform-core version gating

This is the version check you asked about. The logic is in `check_if_priorityQ_enabled_and_scheduleType`.

**How it works**

1. All images are collected from the config: the workflow image `e5-{workflow}-{variant}:{tag}`, plus one image per FB worker.
2. FB workers whose name contains `forcura`, `aiworkflowservice` or `documentcomprehensionai` are skipped.
3. Each image is looked up in the **image metadata DynamoDB table** (`priorityQ.image_metadata_table`).
4. Each image's `platform_core` is compared against the thresholds below with `>=`.

**Image metadata validation**

| Check | Rule | On failure |
| --- | --- | --- |
| Image metadata exists | Every image tag must have a row in the metadata table. | Fail: "The following image(s) are not available in the image metadata table… Please add the missing image metadata entry before retrying" |
| Semantic version | `platform_core` must parse as a version. | The loop stops, and priority and feature flags stay disabled for the remaining images. |
| Missing `platform_core` | Treated as `0.0.0`. | Features are disabled. |

**Thresholds** (all come from SSM under `/platform/workflow/{env}`)

| Feature flag | Compared image | SSM key | Default |
| --- | --- | --- | --- |
| Priority queue / V2 variant (variant image) | VARIANT | `priorityQ.supported_platform_variant_version` | none; the whole check is skipped if this or the FB key is unset |
| Priority queue (FB worker image) | FB\_WORKER | `priorityQ.supported_platform_fbworker_version` | none |
| Priority queue (NonRPA FB image) | NONRPA\_FB\_WORKER | `nonRpaPlatformConfig.supported_platform_fbworker_version` | `0.0.1` |
| EventBridge schedule | VARIANT | `triggerConfig.basePlatformVariantVersion` | required when priorityQ is configured |
| App config (timeout validation) | VARIANT | `appConfig.supported_platform_core_version` | required when priorityQ is configured |
| Email aggregator | VARIANT | `emailAggregatorConfig.platformCoreVariantVersion` | optional; the feature is off if unset |
| White label | VARIANT | `whiteLabel.platformCoreVariantVersion` | optional; the feature is off if unset |
| Dynamic scaling (variant) | VARIANT | `dss.supported_platform_variant_version` | `1.8.2` |
| Dynamic scaling (FB worker) | FB\_WORKER | `dss.supported_platform_fbworker_version` | `1.1.3` |
| NonRPA support | VARIANT | `nonRpaPlatformConfig.supported_platform_variant_version` | `1.7.8` |
| UIA / interventions | VARIANT | `dss.uia_intervention_platform_core_version` | `1.9.0` |

**Rules that cause hard failures**

| Rule | Condition | Message |
| --- | --- | --- |
| NonRPA support | An FB worker has `workerType: NonRPAWorker` and the variant `platform_core` is below the NonRPA threshold. | "NonRPA worker type is not supported by the current platform core version" |
| UIA / interventions | `wf_worker` contains `userInitiatedActions` or `wfInterventions`, and the variant `platform_core` is missing, unparseable or below `1.9.0`. | "userInitiatedActions/wfInterventions require platform\_core version \>= 1.9.0 (found: X). Upgrade the variant image, or remove…" |
| Email aggregator | Email outbound `configs` are set but `platform_core` is below the email threshold. | "Email specific configurations are not supported for the current platform core version…" |
| White label | White label `configs` contain anything other than `referenceId` but `platform_core` is below the white-label threshold. | "WhiteLabel specific configurations are not supported… Only 'referenceId' is allowed" |

**Rules that silently change behaviour (no error)**

- `priority_enabled` is true only if **all** images meet their thresholds.
- Dynamic scaling is enabled only if both the variant and at least one FB worker meet their thresholds.
- Dynamic scaling is also evaluated per worker (`dynamicScalingEnabledPerWorker`).

**Implicit requirement.** If `priorityQ` thresholds are set but `triggerConfig.basePlatformVariantVersion` or `appConfig.supported_platform_core_version` is missing in SSM, `version.parse(None)` raises an unhandled error. That ends in exit code 1 and a generic "Config generation failed".

### 5.3 Worker timeout validation (`validate_worker_timeouts`)

This runs only when `appConfigSupport` is true. That is when the variant `platform_core` is at least `appConfig.supported_platform_core_version`. Otherwise the log shows "Skipping app-level timeout validation because appConfigSupport is disabled".

| # | Field | Rule | Error |
| --- | --- | --- | --- |
| 1 | `variant_configs` | Must be a dict. | "variant\_configs is not a dictionary" |
| 2 | `workflow` | Must exist and be a dict. | "workflow\_section is not a dictionary" |
| 3 | `workflow.wf_worker` | Must be defined. | "wf\_worker\_envs is not a dictionary" |
| 4 | `wf_worker` → `functionBlockOptions` | Entry must exist and its `value` must be a list. | "wf\_worker.functionBlockOptions is missing or invalid for queueTimeoutInMinutes validation" |
| 5 | `functionBlockOptions.queueTimeoutInMinutes` | Must exist. | "queueTimeoutInMinutes is required under wf\_worker.functionBlockOptions" |
| 6 | `queueTimeoutInMinutes` value | Must not be null or empty. | "queueTimeoutInMinutes must have a non-empty value…" |
| 7 | `fb_worker` | Must be a non-empty list. | "fb\_worker section is missing or invalid for executionTimeoutInMinutes validation" |
| 8 | Each `fb_worker` entry | Must be a dict. | "Invalid fb\_worker entry; expected dict" |
| 9 | `fb_worker[].executionTimeoutInMinutes` | Required for **each** app, and must not be null or empty. | "executionTimeoutInMinutes is required at app level for fb\_worker '\<name\>'" |

These errors are wrapped as `ConfigValidationError`, which gives exit code 10.

---

## 6. Stage 3: Per-service validation

### 6.1 FB worker (`fb_worker.py`)

| Field | Rule | Error |
| --- | --- | --- |
| `name` | Required. | "app name is missing!" |
| `workerType` | Required, and must be `Robotframework` or `NonRPAWorker`. | "worker\_type is missing for app X" / "Unknown workerType: X" |
| Dynamic scaling on: `minReplicas`, `maxReplicas` | Both required. | "minReplicas and maxReplicas are required for fb-worker 'X' when dynamic scaling is enabled." |
| Dynamic scaling on: `replicaCount` | Must **not** be set. | "replicaCount must not be set… Use minReplicas/maxReplicas instead." |
| Dynamic scaling on: values | Both must be non-negative. | "minReplicas (a) and maxReplicas (b) must be non-negative…" |
| Dynamic scaling on: ordering | `minReplicas` must be no greater than `maxReplicas`. | "minReplicas (a) cannot exceed maxReplicas (b)…" |
| Dynamic scaling off, NonRPA | `minReplicas`/`maxReplicas` not allowed; use `replicaCount`. | "NonRPAWorker 'X' does not support minReplicas/maxReplicas. Use replicaCount instead." |
| Dynamic scaling off, other workers | `minReplicas`/`maxReplicas` not allowed; use `replicaCount`. | "minReplicas/maxReplicas are not supported for fb-worker 'X' with the current platform versions. Use replicaCount instead." |

**Dynamic scaling is always off for NonRPA workers.** `replicaCount` is read with a hard key lookup, so a missing value raises `KeyError`.

### 6.2 AI workers (`resources.py`, `workflow_services.py`, `ai_fb_worker.py`, `langfuse_env.py`)

| Rule | Error |
| --- | --- |
| A variant may contain **only one** primary AI worker: `aiworkflowservice` or `prebillaiworkflowservice`. | "Variant has conflicting primary AI workflow workers… Only one … is allowed per variant." |
| An AI app needs a primary AI worker in the variant to set the temporal namespace suffix. | "ai\_temporal\_ns\_suffix is not set in options for app 'X'…" |
| SSM parameter `/platform/{env}/{app}` must exist. | "Parameter … not found for app X" |
| `clientConfig` must be a valid JSON object. | "clientConfig in … is not valid JSON" / "must be an object" |
| Exactly one client-config row per tenant. | "Expected one client config for 'T' in \<table\>, found N" |
| `langfuse_configs` must be valid JSON and an object. | "Invalid langfuse\_configs JSON…" / "must be a JSON object" |
| Allowed `langfuse_configs` keys: `OCR_LANGFUSE_PUBLIC_KEY`, `OCR_LANGFUSE_SECRET_KEY`, `OCR_LANGFUSE_HOST`, `AGENT_LANGFUSE_PUBLIC_KEY`, `AGENT_LANGFUSE_SECRET_KEY`, `AGENT_LANGFUSE_HOST`. | "Unsupported langfuse\_configs keys…" |
| Each value must be a non-empty scalar (not null, dict or list, and not blank). | "…must be a string, got X" / "…is empty" |

### 6.3 Workflow worker (`worker.py`, `utils.py`)

| Rule | Error |
| --- | --- |
| `wfInterventions.timeoutInMinutes` is required when `wfInterventions` is configured. | "timeoutInMinutes is required when wfInterventions is configured" |
| Each intervention `target` must be exactly `AutoHub` or `Neos` (case-sensitive). | "Target should be AutoHub or Neos and it is case sensitive" |
| Each `targetConfig[].name` must be exactly `AutoHub` or `Neos` (case-sensitive). | "AutoHub and Neos should be in case sensitive" |

### 6.4 Aggregator (`aggregator.py`)

| Rule | Error |
| --- | --- |
| `emailProviderType` must be `Element5` or `Customer`. The default is `Element5`, and the check applies only when email is supported. | "Invalid emailProviderType 'X'. Must be 'Element5' or 'Customer'." |
| Email and white-label feature gates (see §5.2). | See §5.2 |

### 6.5 Trigger / schedule (`trigger.py`, `workflow_services.py`)

These checks apply when `event_type != ONBOARDING`. They run on the response from the Deployment Management Service `CreateDeploymentVersion` call.

| Rule | Error |
| --- | --- |
| `deploymentVersionId` must be returned. | "Deployment version ID not found in schedule details" |
| `deploymentId` must be returned. | "Deployment ID not found in schedule details" |
| `scheduleInfo` must not be null. | "Schedule info not found for trigger: X" |
| Each trigger needs a `scheduleId`. | "Schedule ID not found for trigger: X" |
| Each trigger needs a `scheduleExpression`. | "Schedule expression not found for trigger: X" |
| A trigger with no matching `scheduleName` | Silently skipped. |

---

## 7. Failure behaviour

| Exception type | Examples | Exit code | Log prefix |
| --- | --- | --- | --- |
| `ConfigValidationError` | Timezone, timeouts, replica rules, "is missing", "does not support" | **10** | `Config validation failed:` |
| Any other exception | Image metadata missing, NonRPA unsupported, UIA version, AI worker conflicts, DB or gRPC errors | **1** | `Config generation failed:` |

- `main.py` converts an error from service generation into `ConfigValidationError` (exit 10) when its message contains one of these markers: `minReplicas`, `maxReplicas`, `replicaCount`, `dynamic scaling`, `workerType`, `is missing`, `does not support`, `are not supported`, `cannot exceed`, `non-negative`, `required for fb-worker`, `Unknown workerType`.
- Messages that match no marker (for example image metadata, UIA version and AI conflicts) exit with code 1 even though they are config problems.
- Jenkins treats both exit codes the same: the build fails and the description is set to `failure-stage: config-generation`. The exit code only helps when reading logs.

---

## 8. Examples

**Passing **`config.yaml` fragment

```yaml
timezone: America/Los_Angeles
workflow:
  wf_worker:
    - name: functionBlockOptions
      value:
        - { name: queueTimeoutInMinutes, value: 30 }
fb_worker:
  - name: hchb
    workerType: Robotframework
    executionTimeoutInMinutes: 60
    minReplicas: 1
    maxReplicas: 3
    image: [{ value: "1.2.0" }]
```

This passes only if the images' `platform_core` meets the dynamic-scaling thresholds (variant 1.8.2 or higher, FB worker 1.1.3 or higher).

**Failure examples**

| Config problem | Result |
| --- | --- |
| `timezone: Mars/Base` | `Config validation failed: Provided timezone Mars/Base is invalid` (exit 10) |
| `fb_worker` entry with no `executionTimeoutInMinutes` (appConfig supported) | `executionTimeoutInMinutes is required at app level for fb_worker 'hchb'` (exit 10) |
| `minReplicas: 5`, `maxReplicas: 2` with dynamic scaling on | `minReplicas (5) cannot exceed maxReplicas (2)…` (exit 10) |
| `minReplicas`/`maxReplicas` set but the images are below the dynamic-scaling thresholds | `minReplicas/maxReplicas are not supported for fb-worker 'X' with the current platform versions. Use replicaCount instead.` (exit 10) |
| `userInitiatedActions` set, variant `platform_core: 1.5.0` | `userInitiatedActions/wfInterventions require platform_core version >= 1.9.0 (found: 1.5.0)…` (exit 1) |
| Image tag not in the metadata table | `The following image(s) are not available in the image metadata table…` (exit 1) |
| `ClientDestinationConfig.json` has `partner-name` but no `sub-tenant-id` | `Both 'sub-tenant-id' and 'partner-name' must be present together…` (stage `workflow-api-insertion`) |
| Only some of the six workflow schema files exist | `Incomplete workflow-level schema files…` (stage `workflow-api-insertion`) |
| Build parameter `WORKFLOW_NAME = "my workflow"` | Rejected at build time by the Jenkins regex |

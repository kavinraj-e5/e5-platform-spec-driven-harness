---
type: disagreement
severity: high
subject: GetConfigValue hardcodes the dev schema in a native query while every deployment-config sets the schema to public
sources:
  - code: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/data/repository/DeploymentStateRepositoryImpl.java:22-25
  - config: e5-deployment-mgmt-service/deployment-configs/dev-config.yaml:18-19 (db.connection.schema.name = public)
  - config: e5-deployment-mgmt-service/deployment-configs/qa-config.yaml:18-19
  - config: e5-deployment-mgmt-service/deployment-configs/test-config.yaml:18-19
  - config: e5-deployment-mgmt-service/deployment-configs/prod-config.yaml:18-19
  - code: e5-deployment-mgmt-service/src/main/java/com/e5/deploymentservices/utils/HibernateUtil.java:45 (hibernate.default_schema from db.connection.schema.name, fallback "public")
  - code: e5-deployment-mgmt-service/src/main/resources/hibernate.properties (committed hibernate.default_schema=dev)
verified: 2026-10-02
---

# `GetConfigValue` queries a schema that no environment uses

## The code

`DeploymentStateRepositoryImpl.getDeploymentConfigValue`, lines 22-25:

```java
String sql = "SELECT dv.deployment_version_id, CAST(ds.value->'infra'->>:deploymentConfigKey AS TEXT) AS kubenameSpace " +
        "FROM dev.deployment_state ds " +
        "JOIN dev.deployment_version dv ON dv.deployment_version_id = ds.deployment_version_id " +
        "WHERE dv.deployment_version_id = :deploymentVersionId";
```

The schema name `dev` is a **string literal inside a native query**, so
`hibernate.default_schema` cannot rewrite it.

## The configuration

`HibernateUtil.java:45` sets `hibernate.default_schema` from
`EnvUtil.getEnv("db.connection.schema.name", "public")`. All four committed
deployment-configs set it to the same value:

| Environment | `db.connection.schema.name` |
|---|---|
| dev | `public` |
| qa | `public` |
| test | `public` |
| prod | `public` |

So in **every** environment, including dev, every JPA mapping resolves to `public.*` while this
one query resolves to `dev.*`. Unless a schema literally named `dev` exists *and* is kept in
sync with `public`, `GetConfigValue` fails with `relation "dev.deployment_state" does not exist`.

## Blast radius

`GetConfigValue` is one of the more widely called rpcs in the platform — four consumers at
their pinned commits:

| Caller | Call site |
|---|---|
| monitoring-service `v1.4.4` | `MonitoringClientImpl.java:280` |
| [credential-monitor](../subsystems/credential-monitor.md) `v1.0.1` | `MonitoringClientImpl.java:200` |
| workflow-ops `v1.6.3` | `client.call("GetConfigValue", …)` |
| workflow-manager (newer checkout) | `WorkflowManagerTaskHandler.java:230` |

credential-monitor's use is load-bearing: it fetches the `dbconfig` key
(`TaskHandler.java:29,136-138`) to build the Postgres connection for its health probe. If
`GetConfigValue` fails there, the probe reports unhealthy for a reason that has nothing to do
with the database it is probing.

## The adjacent smell that explains it

`src/main/resources/hibernate.properties` — a file that is **read by nothing at runtime**, since
`HibernateUtil.java:41-49` builds every property from env vars — commits
`hibernate.default_schema=dev`. Somebody's local setup used a `dev` schema, that assumption got
baked into a native query, and the unused properties file is the fossil record.
That same file also commits live credentials — see
[dms-committed-database-credentials](dms-committed-database-credentials.md).

## Why this is not caught

- It is the **only** hardcoded schema prefix in the repo: a grep for `\bdev\.` over
  `src/main/java` returns exactly these two lines.
- The other 10 native queries in DMS (`WorkflowManagerStateRepositoryImpl.java:83,177,221`,
  (`DeploymentDetailsRepositoryImpl.java` no longer shows it at `release/v1.9.8`), `DeploymentStateRepositoryImpl.java:141`,
  `WorkflowRunRepositoryImpl.java:390,441`, `DependencyRepsitoryImpl.java:33,99`) are all
  correctly unqualified — including the *other* query in the very same file, at `:123-139`.
- There is no integration test in the repo.

## Resolution

Drop the `dev.` prefix. The query is already parameterised; an unqualified name resolves through
`hibernate.default_schema`, which is exactly what every other native query in the codebase
relies on. Then delete `src/main/resources/hibernate.properties`.

---
type: subsystem
name: <Human Name>
repo: <github repo name>
repo_path: <path relative to Understandwebhoook/>
branch: <branch read>
commit: <short sha>
language: <java|python|typescript>
build: <gradle|maven|poetry>
deployment: <k8s deployment name, e.g. interface-ss-pl-subsystem>
namespace_pattern: <platform-subsystem-{env} | {client}-{workflow}-{variant}-{env}>
etp_roles: [<task-initiator|task-router|task-queue|task-worker|task-reader>]
owns_schema: <true|false>
ports: [<port>]
health: <grpc|http path|none>
calls: [<service-id>]
called_by: [<service-id>]
provenance: code            # code | doc | code+doc | inferred
verified: <YYYY-MM-DD>
---

# <Name>

<One sentence. The first clause must disambiguate it from any similarly-named service.>

**Responsibility (what it owns):** <what this service and only this service is responsible for>

**Not its responsibility:** <the adjacent thing people wrongly assume it does>

**Entry points:** <main class / module, file:line>

**Inbound:** <REST/GraphQL/gRPC/Kafka/AKST-VQ — name each operation and topic>

**Outbound:** <gRPC RPCs called, Kafka topics produced, VQs dispatched to — with file:line>

**Data owned:** <tables, or "none — state lives in [X]">

**Config keys:** <names only, never values; note which are env-injected and blank in committed config>

**ETP role:** <which transport-task-protocol role(s) it plays, and in which deploy mode>

**Failure modes:** <what breaks, and how it is observed>

**Code-level landmarks:** <3-6 file:line pointers a developer needs to find first>

**Known divergences:** <doc-vs-code conflicts found; link to ../../disagreements/<id>.md>

**Relevant runbooks:** <links>

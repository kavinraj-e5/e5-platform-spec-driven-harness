---
type: runbook
name: Add a GraphQL query or mutation
applies_to: [interface, deployment-mgmt]
triggers: ["expose an API", "add a mutation", "let Neos do X", "new endpoint"]
provenance: code
verified: 2026-10-03
---

# Add a GraphQL query or mutation

Interface is the **only** front door for external and UI-driven actions. Neos never calls a
subsystem directly.

## First decide: read or act?

**A read** is a resolver plus one or more gRPC calls. Synchronous, done.

**An action** is asynchronous by construction and is a different job — follow
[add-a-lifecycle-action](./add-a-lifecycle-action.md) instead. 11 of 25 existing endpoints are
async.

## Steps for a read

1. **SDL** — add the field under `type Query` in `src/main/resources/graphql/query.graphqls`.
   Root types live in exactly two files; the rest are type definitions.
2. **Resolver** — `@QueryMapping` in the matching controller.
   **Verify the SDL field name and the method name match.** Three resolvers in this service are
   annotated but absent from the SDL and are therefore unreachable
   (`eventTypes`, `unSubscribeEventTypes`, `removeWebhook`), and the Support APIs page documents
   one of them as a working endpoint.
3. **gRPC call** to deployment-mgmt or workflow-ops. Regenerate stubs if the rpc is new.
4. **Return type** — if you reuse an existing DTO, check the SDL type name actually matches the
   Java type; two existing fields declare a type whose name differs from the DTO returned.
5. Declare under `contracts_added` as `graphql: Query.<name>`.

## Traps

- **Queries are not guaranteed read-only here.** `exportState` is declared a Query and writes a
  database row and produces to Kafka. Do not copy that; if yours has side effects, make it a
  mutation.
- **The shared proto over-reports coupling.** deployment-mgmt's proto declares ~122 rpcs;
  interface calls a fraction. Derive impact from call sites, never from proto membership.
- `buf breaking` on any proto change, and run `e5 impact` before you start.

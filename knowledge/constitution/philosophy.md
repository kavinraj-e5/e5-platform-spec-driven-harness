---
type: constitution
layer: L0
name: Platform Philosophy
authority: stated by platform owner; non-negotiable
verified: 2026-10-02
---

# Platform Philosophy

These are the platform's non-negotiables. A design that violates one of these is wrong even
if it works. Cite the specific rule when justifying or rejecting a service placement.

**1. Inter-subsystem communication is event-driven, over Kafka.**
Subsystems do not call each other synchronously to get work done. A synchronous gRPC call is
acceptable for a *read* or a *validation guard*; the work itself is dispatched as a task.

**2. All task traffic follows the transport task protocol, currently v2.**
Every system — subsystems, platform-core services, apps — understands the same envelope.
See [transport-task-protocol-v2](../contracts/transport-task-protocol-v2.md).
A new kind of work is a new *task type*, not a new bespoke channel.

**3. AKST is a mutable prioritized queue supporting priority and deadline.**
Deadline is **respected, not enforced** — the platform is deliberately **not preemptive**,
because most tasks are non-idempotent. Two consequences the design accepts on purpose:
*priority inversion* (a high-priority task may wait behind a running low-priority one) and
*starvation risk* under earliest-deadline-first.

**4. Roles are defined by the protocol, not by service names.**
Task Initiator · Task Router · Task Queue · Task Worker · Task Reader. One deployable can
play different roles in different deploy modes — **role is configuration, not identity.**
Before adding a service, ask which existing role the work belongs to.

**5. User experience is frozen before design, and design before code.**
Define what the user provides, what they get back, and how they interact — *then* analyse
which service owns the functionality. UX is a first-class concern for the platform, the SDK
and every subsystem, not just the UI.

**6. No shortcuts. Even when it is harder, we do it cleanly.**
Do not overload an existing enum or column to avoid a migration. Do not add a field to a
payload that the consumer must guess the meaning of. Difficulty is not a justification.

**7. The platform hides technical complexity from the workflow and app teams.**
They write business orchestration and automation. They should not need to know GraphQL,
gRPC, Kafka or DB queries to do it. An API that leaks platform mechanics onto them is a
design failure.

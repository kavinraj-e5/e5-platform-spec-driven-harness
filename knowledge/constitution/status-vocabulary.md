---
type: constitution
layer: L0
name: Status Vocabulary
authority: authored here — four incompatible dialects exist, none documented
verified: 2026-10-02
---

# Status Vocabulary

Four dialects coexist in Confluence and none is written down. This file defines the one the
harness emits. Existing pages are not retrofitted; new documents use this.

## Design-document status (the harness emits these)

| Value | Meaning |
|---|---|
| `DRAFT` | Being written. Not ready for review. |
| `IN REVIEW` | Circulated; awaiting sign-off. |
| `DESIGN` | Agreed design, implementation not started. |
| `PLANNED <version>` | Scheduled into a release. Version suffix required. |
| `IMPLEMENTED <version>` | Shipped. Version suffix required. |
| `SUPERSEDED` | Replaced. Must link the replacement. |
| `REJECTED` | Decided against. Must state why — this is the most useful status to keep. |

## Observed in the existing corpus — recognise, do not emit

`PLANNED` / `PLANNED 1.0.0` / `PLANNED - 1.3.0` · `DESIGN` · `IN REVIEW` · `COMPLETED` ·
`Proposed` (ADR pages) · `Draft` (newer specs) · `Not started` / `In progress` / `completed`
(DACI pages) · `Approved` / `Under Discussion` (decision logs).

`WIP` is **never a status value** in the corpus — it only ever appears as a title suffix.
`APPROVED` and `IMPLEMENTED` never appear as statuses today.

## Release-table status

From the richest existing release page: `Release Status ∈ {New version, Existing version}`.
Keep these two exactly — they are already in use and understood.

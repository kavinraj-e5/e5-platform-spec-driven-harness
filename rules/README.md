# `rules/` — semgrep rules that re-check what the knowledge base asserts

A disagreement card says *"the code does X"*. That is a claim with a shelf life: someone
fixes X and the card quietly becomes a lie, or someone copies X into a second repo and
the card quietly becomes incomplete. 69 cards, each verified by hand on one date.

A rule here encodes one card's claim so the claim is re-checked every time the mirrors
move, instead of decaying until a human re-reads it.

```
./e5 patterns            re-check every rule against every mirror
./e5 patterns --rule ID  one of them
```

## Each rule carries its contract

```yaml
metadata:
  e5-card: disagreements/event-store-grpc-swallows-errors.md
  e5-expect: present          # the defect should still be there
  e5-at:                      # and exactly here
    - event-store src/main/java/.../PlatformEventStoreImpl.java
```

`tools/patterns.py` compares what semgrep finds against that, and reports one of four
verdicts:

| verdict | meaning | what to do |
|---|---|---|
| `CONFIRMED` | found, where the card says | nothing. The card is still true. |
| `FIXED` | the rule matches nowhere | **re-read and retire the card.** Someone fixed it. |
| `SPREAD` | found somewhere the card does not name | the defect was copied. Widen the card, or write a new one. |
| `MOVED` | found in the named repo, different file | the code was refactored; re-anchor the card. |

`FIXED` is the verdict that justifies the whole directory. Nothing else in this harness
notices when a defect goes away — `./e5 cite drift` notices when a cited *line* changes,
which is not the same question and routinely misses a fix three files away.

## Why semgrep and not the extract/ front ends

`extract/` answers *"what does this repo declare"* and its output is facts. These rules
answer *"does this shape still occur"* and their output is a verdict on a card. The
difference that matters in practice is who writes them: a rule here is **declarative
YAML with a test case next to it**, so a platform engineer who would not touch
`extract/parse/java.py` can add one, review one, or argue with one.

It is also cross-language for free — the same runner reads Java, Python and YAML rules —
and semgrep's matching is syntactic, so `catch (Exception e) { log.error(...); }` does
not match a comment that mentions it. The whole class of defect `extract/` was built to
remove does not come back here.

## Installing semgrep: use pipx, NOT pip

```
pipx install semgrep
```

`pip install --user semgrep` **breaks this repository's console.** Measured 2026-10-17:
it upgrades `starlette` past what the pinned `fastapi` supports, and
`console/test_app.py` then fails at collection with
`TypeError: Router.__init__() got an unexpected keyword argument`. semgrep is a CLI, so
it has no business sharing a site-packages with anything. `tools/patterns.py` invokes
the binary and never imports the module, which is why nothing here depends on its
Python environment at all.

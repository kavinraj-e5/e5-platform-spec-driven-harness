# Porting `tools/extractors.py` to `extract/`

**Status 2026-10-17: all 11 extractors ported.** `tools/extractors.py` is now the
registry and the `(repo_id, root)` signature `tools/sync.py` calls, and nothing else —
190 lines, down from 440, with no regex left in it. Every port is signed off here against
`python3 -m extract.cli diff`, which runs the parser and the regex it replaces side by
side over all 23 bare mirrors.

The rule: **a port does not land until every difference is explained.** A parser rewrite
cannot be byte-identical — that is the reason to do it — so the safety net is a
difference report with three categories:

| | meaning | what it must be |
|---|---|---|
| `ONLY_OLD` | the regex found it, the parser did not | a regex false positive (say why), or a parser gap (a bug — fix it) |
| `ONLY_NEW` | the parser found it, the regex did not | a blind spot closed (say why), or a parser false positive (a bug — fix it) |
| `MOVED` | same fact, different line | a line number is evidence a reader checks; a silent shift is not acceptable either |
| `PROPS` | same fact, same line, different payload | added for `routing`, whose facts are keyed on `repo:path:line` — every label and destination lives in props, so a location-only diff reported "identical" while the payload changed completely |

Once an extractor is switched over, `tools/extractors.py` delegates to it and `diff`
compares the parser with itself. It says so rather than printing "identical", because a
vacuous comparison that looks like a passing check is the same defect this document
exists to prevent.

---

## Ported

### `db_bindings` — identical (24 facts)
`DBAccessType\.([A-Z][A-Z0-9_]+)` → `JavaFile.field_reads()` filtered to the
`DBAccessType` receiver. Same output, and now immune to the receiver appearing in a
comment.

### `worker_roles` — identical (6 facts)
Was: a whole-file `WORKER_MODE` text gate, then `case\s+"?([A-Z][A-Z0-9_]{2,})"?` anywhere
in that file, plus a hand-kept deny-list (`TRUE`, `FALSE`, `NULL`, `DEFAULT`) to suppress
labels from *unrelated* switches in the same class.

Now: the file must contain `"WORKER_MODE"` **as a string literal**, and the modes must be
**switch labels**. The deny-list is gone because the thing it suppressed can no longer be
reached.

### `handlers` — 2 fewer, both regex false positives (69 → 32 after component globs)

`(?:class|interface)\s+(\w*(?:TaskHandler|Aggregator|…)\w*)` → class and interface
**declarations** only.

| dropped | why it was never a handler |
|---|---|
| `E5Aggregator` @ `annotation/model/E5Aggregator.java:13` | it is `public @interface E5Aggregator` — a Java **annotation type**. The regex matched the token `interface` inside `@interface`. |
| `AutoHubAggregatorHandlerTest` @ `…Test.java:12` | **every line of that file is commented out.** The regex matched `//class AutoHubAggregatorHandlerTest {`. |

Two bugs this found in the *first* port, both caught by the diff rather than by review:

- including `enum` reported `AggregatorTargetType` — an enum of target types — as a
  handler. Restricted to class/interface.
- the regex ran on `.py` as well, and the first port read only Java, losing
  devops-pipeline's real `WorkflowAggregator` class. `PyFile.classes()` was added for it.

The original's filter `nm in seen or nm.startswith(("I","Base","Abstract")) and len(nm) < 6`
relied on `and` binding tighter than `or`; the port states the condition instead of
inheriting it by precedence.

### `rf_keywords` — 18 MORE, 27% of that component's public surface (47 → 65)

`^def\s+([a-z][a-z0-9_]*)\s*\(` with `re.M` → `PyFile.functions()`, public and
module-level.

The name class `[a-z][a-z0-9_]*` ends the match at the **first uppercase letter**, so
`\s*\(` never matches and the whole `def` is skipped. The casualties were not incidental:

```
e5_FTPS_authentication     e5_FTPS_change_remote_directory  e5_FTPS_close_connection
e5_FTPS_create_folder      e5_FTPS_download_file            e5_FTPS_move_file_to_folder
e5_FTPS_upload_file        e5_SFTP_authentication           e5_SFTP_change_remote_directory
e5_SFTP_close_connection   e5_SFTP_create_folder            e5_SFTP_download_file
e5_SFTP_is_path_available  e5_SFTP_list_directory           e5_SFTP_move_file_to_folder
e5_SFTP_upload_file        E5_Yield                         executeTearDown
```

**The entire FTPS and SFTP keyword families** — the RPA worker's whole secure
file-transfer capability — were absent from its declared surface. "Can an automation do
SFTP?" was unanswerable, and looked answered.

`ast` also fixes the other direction: `top_level` excludes methods inside classes and
nested helpers, one of which (`execute_akst_task`) the regex was reporting as a public
keyword.

An unparseable file now emits `RpaKeywordParseError` rather than nothing, because a file
on unsupported syntax and a file with no keywords must not look the same.

### `sdk_imports` — 155 line numbers corrected (303 facts, 169 after globs)

`^\s*(?:import|from)\s+(...)` with `re.M`: `^` matches at a line start and then `\s*`
**consumes the preceding blank line**, so `m.start()` lands on the blank line and
`t[:m.start()].count("\n") + 1` is one too small. Every import preceded by a blank line
was cited one line early — 155 of 303.

```
eventstore-client-sdk/src/main/java/e5/eventstoresdk/services/EventStoreClient.java
  1: package e5.eventstoresdk.services;
  2:                                      <- the regex reported this line
  3: import com.e5.common.utils.kafka.impl.KafkaOutboundEndpoint;   <- the actual import
```

This is the most quietly damaging class of defect in the set, because
`./e5 cite check` verifies line numbers and `./e5 cite drift` anchors on line *content* —
a fact pinned one line off is re-verified against the wrong line forever.

### `routing` — 3 MORE, 6 destinations corrected (28 → 31)

The load-bearing port. Once queue and topic values stopped being extracted, the **task
name** is the only thing left that joins two repositories from source alone, and this is
the extractor that reads it.

**1. Two arms were unreachable.** `case\s+([A-Za-z0-9_"\', ]+?)\s*->` has no `.` in its
character class, so

```java
case InterfaceConstants.WORKFLOW_RESOURCE_PROVISIONING -> {
```

matched nothing, in **both** routing classes. The route had to be curated by hand in
`knowledge/_reference/joins/task-names.yaml` with the note *"extractor-invisible —
constant case label, not a literal"*, so the tenth task would not silently vanish. The
parser resolves the constant through a repo-wide `final String` table and extracts both
arms; `./e5 check`'s routing gate now reports **10 task names fully routed** rather than
9 plus a hand-written row. The curated row stays — it is now corroboration rather than
the only evidence — and its note has been corrected.

**2. A second undefaulted routing switch was being suppressed.** The old risk check
asked whether the word `default` appeared anywhere in the **file**:

```
TaskManagerHelperImpl.java:124   switch (taskName) {          <- no default arm
TaskManagerHelperImpl.java:284       default -> {             <- a different switch, 160 lines later
```

so one unrelated switch silenced the finding for the routing switch beside it. The
authored `_PIPELINES` rule in `kb_core.py` has always said *"two unsynchronised routing
switches, **neither** with a default arm"* — the hand-written warning was right and the
extractor was reporting one. It now reports both, per switch, with the switch's line.

**3. Six destinations stopped bleeding in from the next arm.** The old version read a
400-character text window after the case label, which runs straight into the following
arms:

```
case LOCAL:   old sets = "processTaskResult = localTaskProcessor.apply(event); break;
                          case FORWARD: taskEventForwarder.forwardToTopic(…"
              new sets = "processTaskResult = localTaskProcessor.apply(event); break;"
```

An arm that set no destination reported its neighbour's, and nothing distinguished the
two. An arm's calls now belong to that arm.

Also additive: every rule carries `selector` and `dispatch_method`, so a reader can see
*which* switch in *which* method a route came from without opening the file.

`sets_vq` is and remains a Java **field name** (`workflowManagerVQ`), never a queue name
— the value that field holds arrives from config at deploy time and is deliberately not
read. `tools/check_no_injected_values.py` passes on all 8 fact layers.

One bug this found in my own first port, caught by the diff: `args[1].split(".", 1)[1]`
turned `Task.EXPORT_STATE.name()` into `EXPORT_STATE.name()`, breaking the cross-repo
join on 18 facts. The port now takes the segment after the holder.

### `events` — 8 fewer, 1 more (24 → 16)

The 8 are not a parser improvement. They are **the deploy-time-value rule applied to a
kind that had escaped it.** The old extractor matched two families and called both
`EventType`:

| | what it actually is |
|---|---|
| `e5.platform.event.*` | a CloudEvents **type**, written into the envelope |
| `e5.audit.*` / `e5.webhook.*` | a **Kafka topic name** |

All 50 occurrences of the second family across four repos were checked. Every one is a
`${ENV_VAR:default}` Spring default, a Java field the Spring binder overwrites from that
same key, a Javadoc comment, or a test assertion — eleven of them inside **commented-out**
test code in `WebhookRequestProducerTest.java`. The qa cluster runs
`e5_qa_audit_events`, `e5_qa_audit_events_dlq`, `e5_qa_webhook_requests` and
`e5_qa_webhook_requests_dlq`, so **not one source literal names a topic that runs** —
the identical finding that deleted `extract_kafka` and `extract_virtual_queues`,
surviving under a different `kind`.

`e5.platform.event.*` passes the same test: it is written in code, read from no config
key anywhere in the estate, and `eventstore-client-sdk` pins
`e5.platform.event.customevent` in a JSON Schema `enum`. It is kept.

Three consequences beyond the extractor:

- **`check_no_injected_values.py` would not have caught this**, and now would. Its
  carve-out said "dot-delimited code literals are payload contracts" — too wide, because
  a topic name is dot-delimited too. The four measured defaults are listed as fiction,
  and `name` was removed from the field exemption list: the gate's rule is that no fact
  may be *identified* by an injected value, and `name` is the identity. Measured: 0 of
  3,858 facts trip on it, so closing the hole costs nothing.
- **Four curated `carries:` rows in `_reference/joins/task-flows.yaml` named the topic**
  rather than the payload, and are corrected to the envelope — which travels with the
  message and is the same on every deploy.
- The three `_reference/contracts/e5-*.md` cards were checked and are already right:
  their front matter says `topic_default:` alongside `config_key:` and `env_var:`.

The 1 added fact is the JSON Schema enum; the old suffix list had no `.json`. JSON is
read through the YAML front end, which is a superset of it and carries line marks.

Four further differences, all parser work:

- **4 facts were cited in a Javadoc comment** (`* - e5.webhook.requests - Webhook
  delivery requests`). A comment is not a string constant in any of the three front ends.
- **2 moved out of an embedded JSON blob.** `"{\"eventtype\":\"…notstarted\",…}"` is
  one Java string whose *value contains* the type; the assertion three lines later holds
  the type itself. The anchored match takes the latter.
- **A prefix is marked as one.** `setEventtype("e5.platform.event.workflow." + status)`
  holds a fragment, not a type.
- **Production beats a fixture for the citation**, and the second production site is
  kept in `also_at` rather than discarded. The old version took whichever file
  `os.walk` reached first: `eventstore-client-sdk`'s type was cited in
  `EventStoreClientTest.java` while the JSON Schema that defines it went unread.

### `config` — 1,836 occurrences → 400 facts, and 3 keys nobody could see (+ `SsmPath` deleted)

Three separate things, and only one of them is a parser swap.

**1. A quoted key was invisible.** `(\s*)-?\s*name:\s*([A-Za-z0-9_.\-]+)\s*$` has no
`"` in its character class, so

```yaml
  - name: "client_id"
```

matched nothing — in all five of workflow-api's environment files. The three keys lost
this way were `client_id`, `client_secret` and `audience`, so *"does workflow-api take an
OAuth client secret from configuration?"* answered **no**, confidently. Every one of the
old extractor's 397 keys is still found; these 3 are added.

**2. The structure was being guessed.** In these files `name` also names the service, the
image and a `secretKeyRef`, so the old version peeked at the NEXT LINE for `value:` —
plus a second guess, `[A-Z][A-Z0-9_]{2,}`, which quietly made the *naming convention*
part of the definition of an environment variable. The composer answers the real
question: does this key sit at `...env[i].name`. `has_value_binding` was `True` on all
1,836 occurrences, so it carried no information; it is replaced by `binding`, which
distinguishes a literal from a `valueFrom` **secret reference**.

**3. The duplication was not harmless.** One fact per key *per environment file* meant
audit-service declared the same 232 keys five times. The v3 plan's own worked example
found `platform_impact` citing `ConfigKey credential-monitor:taskManager @
dev-config.yaml:15` **four times** as the evidence for one Component — noise presented as
proof. Measured before collapsing: every one of the 397 keys appears in every
environment file of its repo, so the duplication said nothing. Now one fact per key,
with `declared_in` listing the files, and a new **`ConfigKeyGap`** finding when that list
is short. It fires 0 times today; a key added to dev and forgotten in prod is an outage,
and nothing here could see it before. `ConfigParseError` is new for the same reason: the
line-regex read a broken file one line at a time and reported whatever it recognised.

**`SsmPath` is deleted, not ported.** Its three facts were not SSM paths. A regex scanned
deployment-config text for any `/a/b/c` shape and found a **Jenkins job path sitting in a
`value:`** — a deploy-time value, the one thing the facts must not carry. The real SSM
usage was never looked at: 22 parameter-store call sites across `devops-pipeline` and
`workflow-ops`, and in **every one the parameter name is a runtime variable** composed
per environment (`devops-pipeline/.../utils.py:54` says so in its own docstring). There
is no template in source to extract. The console's setup page now says that in a
sentence instead of showing three wrong rows.

### `proto` — 1,431 line numbers corrected, 44 enums added, types resolved

The only front end here that **follows `import`**, which is the whole reason to use the
compiler rather than a parser: `rpc Delete(DeploymentVersionId) returns (Empty)` says
nothing about *which* `Empty`, and `deployment_management.proto` declares
`com.e5.platform.ss.proto.Empty` while importing `google/protobuf/empty.proto`. A short
name cannot join two repositories; a fully-qualified one can, and that join is the point
of extracting protos at all. 42 of 42 protos compile.

| difference | count | why |
|---|---|---|
| `MOVED` | **1,431** | two defects, both citation-destroying |
| `ONLY_NEW` `ProtoEnum` | 44 | the kind did not exist |
| `ONLY_NEW`/`ONLY_OLD` `ProtoMessage` | 12 / 12 | nested messages were hoisted to top level under their bare name |
| `PROPS` | 21 | field types are now fully qualified; `map<k, v>` restored |

**Defect 1 — every rpc cited its service's line.** `deployment_management.proto` declares
`service DeploymentManagementService` three times (it is a concatenated fork — see
`disagreements/dms-proto-forks-disagree-on-service-identity.md`), and **all 126 rpcs of
one of them cited line 1909**.

**Defect 2 — line 1909 is blank.** `^\s*service` under `re.M` consumes the preceding
newline, exactly as `^\s*import` did in `sdk_imports`. 874 messages and 44 services were
each cited one line early. Verified against source for every delta bucket:

```
     1908: }
old> 1909:                      <- what 126 rpcs cited
     1910: service DeploymentManagementService{
NEW> 1911:   rpc getYetToStartWorkflows (YetToStartWorkflowsRequest) returns (WorkflowRuns);
```

**Nested messages were hoisted.** `Service.Image`, `Kafka.Topic`, `StateService.S3` and
`StateService.RDS` were reported as top-level `Image`, `Topic`, `S3`, `RDS` — peers of
the messages that contain them, with a different wire identity than the one they have.

**A map field kept its map type.** protoc lowers `map<string, string>` to
`repeated GetDeploymentConfigResponse.ConfigValuesEntry`; reporting that would name a
type the `.proto` does not declare, so it is folded back.

`ProtoParseError` is new, for the same reason `ConfigParseError` is: an extractor that
goes quiet when the compiler fails is worse than one that never used the compiler.

**One bug this found in the diff harness itself.** `_key` was
`(kind, name, path, line)` with no `repo`, so three repositories that each ship
`src/main/proto/task.proto` collided in the comparison dict and two of the three were
silently dropped. Fixing it surfaced that **task-reader's `isTaskAvailable` takes
`VirtualQueue` where platform-core's and akst-service's take `TaskAvailableRequest`** —
a fork disagreement the comparison had been hiding.

### `graphql` — 119 line numbers corrected, `scalar` added

The same two defects as `proto`, in a different language. `^\s*(type|input|…)` under
`re.M` ate the preceding blank line (88 types), and **every root field was cited at its
`type Query {` line** — so `Query.deployment` and `Query.deployments` pointed at the
same place. A root field is the platform's HTTP-facing contract; every Neos call enters
through one.

`scalar JSON` was invisible because the kind list was
`(type|input|interface|enum|union)` — and "arbitrary payload" is exactly the thing a
reader asks about. `field_count` and structured `args` are new; the regex stored the
argument list as the raw text between parentheses.

Also fixed by construction, though this schema happens not to exercise them: the old
version read a root type's body **one line at a time** with
`(\w+)\s*(\([^)]*\))?\s*:\s*(.+)`, which cannot close `[^)]*` on a multi-line
argument list and silently skips a `"""description"""` block. 10 of 10 documents parse.

### `migrations` — a hybrid, and the ratio is itself a fact

sqlglot is right for the 80% and cannot do the rest. Measured on this estate:
**46 statements fall back to sqlglot's opaque `Command`**, 11 of them `CREATE TABLE`s in
deployment-mgmt — every table whose DDL was generated by pgAdmin, so it carries
`COLLATE pg_catalog."default"` and `TABLESPACE pg_default`. One file does not parse at
all. A pure-sqlglot port would have lost eleven tables.

So each statement is tried against the parser and, when the parser yields `Command` or
nothing, against the regex it replaces — on **that statement's text alone**, which is
the thing the old extractor could not do because it ran over the whole file at once.
Every fact records `parsed_by`, and each file emits a **`SqlParseGap`** carrying the
count. A degradation nobody counts is a degradation nobody fixes: when a sqlglot release
widens its PostgreSQL support, `./e5 estate diff` shows the number move, and that is the
signal to delete the fallback.

What the comparison found:

- **4 `DROP COLUMN` changes were missing** — from my own first port, not from the regex.
  sqlglot puts the dropped column under `Drop.args["tables"]` and leaves `.name` empty.
  The diff is the only reason this was caught.
- **2 columns the regex could never see.** `ALTER TABLE t ADD COLUMN a, ADD COLUMN b;`
  — the pattern needed the literal `ALTER TABLE` prefix per match, so it read the first
  and stopped. `fb_worker_pod.max_replicas` and `schedule.end_date` were invisible.
- **One phantom table with an empty name**, matched inside
  `EXECUTE format('CREATE TABLE audit.%I PARTITION OF audit.events', p)` in a PL/pgSQL
  body. Single-quoted literals are now blanked for the fallback, the same way comments
  always were — a table name is never inside a string.
- `MOVED` is **0**: both paths agree on every line number.

The statement splitter is dollar-quote aware, because a `DO $$ BEGIN … ; … END $$;`
block contains semicolons that do not end the statement.

---

## Not yet ported

Nothing. The table below is kept for the record of what each needed.

| extractor | front end | note |
|---|---|---|
| all eleven | see `tools/requirements-extract.txt` | tree-sitter (Java), stdlib `ast` (Python), PyYAML composer (YAML/JSON), `grpc_tools.protoc` (proto), graphql-core (SDL), sqlglot **+ a counted fallback** (SQL) |

## The score

Across eleven ports, the differential harness found, in production facts:

| | |
|---|---|
| line numbers corrected | **1,860** — `sdk_imports` 155, `proto` 1,431, `graphql` 119, plus `routing` and `config` |
| facts that pointed at something that does not exist | 3 — an `@interface`, a fully commented-out file, a table matched inside a PL/pgSQL string |
| facts that were a deploy-time value | 11 — 8 Kafka topic defaults filed as `EventType`, 3 `SsmPath` rows that were a Jenkins job path |
| contract surface that was invisible | 18 RPA keywords (27% of that component's), 44 proto enums, 1 GraphQL scalar, 3 quoted config keys, 2 arms of the routing switch, 2 columns, 1 JSON-Schema event type |
| noise removed | 1,436 duplicate `ConfigKey` occurrences |
| platform defects surfaced | a second undefaulted routing switch; a fork disagreement on `isTaskAvailable`'s request type |

Five of those came from bugs in my own ports, caught by the diff before they landed:
enums counted as handlers, Python classes missed, `Task.X.name()` read as
`X.name()`, `DROP COLUMN` read from the wrong AST field, and a comparison key with no
`repo` in it that was silently dropping two of every three `task.proto`.

## Running it

```
python3 -m extract.cli diff                    all ported extractors, all mirrors
python3 -m extract.cli diff rf_keywords        one of them
python3 -m extract.cli run  handlers           facts to stdout as jsonl
python3 -m extract.cli parse interface src/main/java/.../TaskManagerProtocolBuilder.java
```

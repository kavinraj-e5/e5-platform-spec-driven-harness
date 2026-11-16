"""Tests for the parser-backed extraction package.

Parser tests use literal fragments, so a failure means the front end broke rather than
that a repo moved. The tests marked WAS A REGEX DEFECT each encode something the regex
version got wrong and the differential harness found - those are the reason the package
exists, and they must keep failing on a regression.
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

KB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KB))
sys.path.insert(0, str(KB / "tools"))

from extract.core import DirRepo, fact, is_test            # noqa: E402
from extract.parse.java import (JavaFile, ANNOTATION, CONSTANT, CALL_ARG,  # noqa: E402
                                LOCAL_VAR, SWITCH_LABEL)
from extract.parse.pyfile import PyFile                    # noqa: E402
from extract.parse.yamlfile import YamlFile                # noqa: E402
from extract.declared import (PORTED, handlers, rf_keywords, routing,  # noqa: E402
                              events, config, proto, graphql, migrations)


def _repo(tmp_path, files: dict) -> DirRepo:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return DirRepo(id="t", root=tmp_path)


# ------------------------------------------------------------------ the fact schema
def test_a_fact_is_shape_compatible_with_the_regex_extractors():
    """Every port is signed off by DIFFING against the old output, and a diff is only
    meaningful if the shape is identical."""
    import extractors as OLD
    a = fact("K", "n", "r", "p", 1, "code", x=1)
    b = OLD.fact("K", "n", "r", "p", 1, "code", x=1)
    assert a == b


def test_one_test_path_definition_for_the_whole_package():
    """Four modules previously carried near-copies that had drifted, so the same file was
    a fixture to one extractor and production code to the next."""
    assert is_test("src/test/java/com/e5/FooTest.java")
    assert is_test("tests/test_thing.py")
    assert is_test("src/main/java/com/e5/FooTests.java")
    assert not is_test("src/main/java/com/e5/Foo.java")
    assert not is_test("src/main/python/latest_state.py")


# ------------------------------------------------------------------ the Java front end
def test_a_declaration_inside_a_comment_is_not_a_declaration():
    """WAS A REGEX DEFECT. `AutoHubAggregatorHandlerTest.java` is a file in which every
    line is commented out, and `(?:class|interface)\\s+(\\w*Aggregator\\w*)` matched
    `//class AutoHubAggregatorHandlerTest {`."""
    f = JavaFile("// class CommentedTaskHandler {}\n/* class BlockTaskHandler {} */\n"
                 "class RealTaskHandler {}\n")
    assert [t.name for t in f.types()] == ["RealTaskHandler"]


def test_an_annotation_type_is_not_an_interface():
    """WAS A REGEX DEFECT. `public @interface E5Aggregator` is an annotation TYPE; the
    regex matched the token `interface` inside `@interface`."""
    f = JavaFile("public @interface E5Aggregator { String[] targetTypes(); }\n")
    assert [t.name for t in f.types() if t.kind in ("class", "interface")] == []


def test_an_enum_is_not_a_class():
    """Caught by the diff on the FIRST port: including enums reported
    `AggregatorTargetType` as a handler."""
    f = JavaFile("enum AggregatorTargetType { A, B }\nclass RealAggregator {}\n")
    kinds = {t.name: t.kind for t in f.types()}
    assert kinds["AggregatorTargetType"] == "enum"
    assert kinds["RealAggregator"] == "class"


def test_a_string_literal_carries_the_construct_it_sits_in():
    """The distinction regex cannot make, and the reason for the dependency: a literal
    passed to a call in production code vs the same literal declared in a fixture."""
    f = JavaFile('''
    class A {
      static final String Q = "monitoring_service_vq";
      @KafkaListener(topics = "t")
      void f() { String local = "lit"; getTask("arg"); switch (x) { case "CASE" -> y(); } }
    }''')
    roles = {s.value: s.role for s in f.strings()}
    assert roles["monitoring_service_vq"] == CONSTANT
    assert roles["t"] == ANNOTATION
    assert roles["lit"] == LOCAL_VAR
    assert roles["arg"] == CALL_ARG
    assert roles["CASE"] == SWITCH_LABEL


def test_a_call_arg_records_the_method_it_was_passed_to():
    f = JavaFile('class A { void f() { getTask("q"); } }')
    s = next(s for s in f.strings() if s.value == "q")
    assert s.context == "getTask" and s.owner == "f"


def test_both_switch_forms_and_both_label_forms_are_read():
    """The platform writes its routing table twice, once with literals and once with
    named constants, so the compiler cannot compare them - which is what
    `check_routing.py` exists to police. A label form that cannot be read is a gate that
    cannot run."""
    f = JavaFile('''
    class A {
      void arrow(String t) { switch (t) {
        case "A", "B" -> go(x);
        case Consts.C -> go(y);
      } }
      void colon(String t) { switch (t) {
        case "D": go(z); break;
      } }
    }''')
    arrow = next(s for s in f.switches() if s.owner == "arrow")
    assert [(t, l) for t, l in arrow.labels] == [("A", True), ("B", True),
                                                 ("Consts.C", False)]
    assert len(arrow.arms) == 2
    assert "go(x)" in arrow.arms[0].body
    colon = next(s for s in f.switches() if s.owner == "colon")
    assert [t for t, _ in colon.labels] == ["D"]


def test_an_arm_keeps_its_labels_with_its_body():
    """Routing is not `which task names appear`, it is `which name maps to which
    destination` - that is the label-to-body association."""
    f = JavaFile('class A { void f(String t) { switch (t) {'
                 ' case "X" -> put(VQ, workflowManagerVQ);'
                 ' case "Y" -> put(VQ, monitoringVQ); } } }')
    arms = f.switches()[0].arms
    got = {a.labels[0][0]: a.body for a in arms}
    assert "workflowManagerVQ" in got["X"]
    assert "monitoringVQ" in got["Y"]


def test_a_field_read_names_its_receiver():
    f = JavaFile("class A { void f() { use(DBAccessType.STATE); use(Other.STATE); } }")
    reads = [(o, fl) for o, fl, _ in f.field_reads()]
    assert ("DBAccessType", "STATE") in reads and ("Other", "STATE") in reads


# ---------------------------------------------------------------- the Python front end
def test_a_nested_function_is_not_part_of_the_public_surface():
    """WAS A REGEX DEFECT. `^def\\s+([a-z]\\w*)` matched methods inside classes and nested
    helpers; `execute_akst_task` in keywords.py was being reported as a keyword."""
    f = PyFile("def outer():\n    def execute_akst_task():\n        pass\n"
               "class C:\n    def method(self):\n        pass\n")
    top = {x.name for x in f.functions() if x.top_level}
    assert top == {"outer"}


def test_an_uppercase_letter_does_not_hide_a_function():
    """WAS A REGEX DEFECT, and the largest one. `[a-z][a-z0-9_]*` ends the match at the
    first uppercase letter so `\\s*\\(` never matches and the def is skipped - which hid
    the entire FTPS and SFTP keyword families, 27% of that component's public surface."""
    f = PyFile("def e5_SFTP_upload_file(a):\n    pass\n"
               "def E5_Yield():\n    pass\n"
               "def executeTearDown():\n    pass\n")
    assert {x.name for x in f.functions()} == {"e5_SFTP_upload_file", "E5_Yield",
                                              "executeTearDown"}


def test_an_env_binding_keeps_the_variable_and_the_env_var_apart():
    """rf-fb-worker-core has `PLATFORM_VIRTUAL_QUEUE = os.getenv("PLATFORM_VIRTUAL_QNAME")`.
    The ENV VAR is the contract; the variable is local. Confusing them silently picks the
    wrong half of a join."""
    f = PyFile('import os\nPLATFORM_VIRTUAL_QUEUE = os.getenv("PLATFORM_VIRTUAL_QNAME", "")\n')
    assert f.env_bindings() == [("PLATFORM_VIRTUAL_QUEUE", "PLATFORM_VIRTUAL_QNAME", 2)]


def test_an_unparseable_file_is_reported_not_silently_empty(tmp_path):
    r = _repo(tmp_path, {"pkg/keywords.py": "def broken(:\n"})
    kinds = {f["kind"] for f in rf_keywords(r)}
    assert "RpaKeywordParseError" in kinds, \
        "an unparseable file and a file with no keywords must not look the same"


# -------------------------------------------------------------------------- extractors
def test_handlers_reads_both_languages(tmp_path):
    """Caught by the diff: the regex ran on .py too, and the first port read only Java,
    losing devops-pipeline's real `WorkflowAggregator`."""
    r = _repo(tmp_path, {"a/X.java": "class JavaTaskHandler {}\n",
                         "b/y.py": "class WorkflowAggregator(Base):\n    pass\n"})
    names = {f["name"] for f in handlers(r)}
    assert names == {"JavaTaskHandler", "WorkflowAggregator"}


def test_an_import_after_a_blank_line_is_cited_on_its_own_line(tmp_path):
    """WAS A REGEX DEFECT affecting 155 of 303 facts. `^\\s*` under re.M consumes the
    preceding blank line, so `m.start()` lands a line early - and the line number is
    what `./e5 cite check` verifies and `./e5 cite drift` anchors on."""
    r = _repo(tmp_path, {"a/X.java":
                         "package a;\n\nimport com.e5.common.utils.kafka.Thing;\n"})
    f = PORTED["sdk_imports"](r)[0]
    assert f["line"] == 3, f


def test_every_ported_extractor_takes_a_source_and_returns_facts(tmp_path):
    r = _repo(tmp_path, {"a/X.java": "class A {}\n"})
    for name, fn in PORTED.items():
        got = fn(r)
        assert isinstance(got, list), name
        for f in got:
            assert set(f) >= {"id", "kind", "name", "repo", "path", "line",
                              "provenance", "confidence", "props"}, (name, f)


def test_the_old_registry_delegates_to_the_package():
    """`tools/sync.py` is unchanged; it calls the same names and gets the parser."""
    import extractors as OLD
    for name in PORTED:
        assert OLD.EXTRACTORS[name].__doc__.startswith("Delegates to extract.declared"), name


# ------------------------------------------------------------------------- routing
_CONSTS = """package e5;
public class InterfaceConstants {
    public static final String WORKFLOW_RESOURCE_PROVISIONING = "WORKFLOW_RESOURCE_PROVISIONING";
    public static final String VQ_NAME = "vqName";
}
"""


def test_a_qualified_constant_case_label_resolves_to_its_value(tmp_path):
    r"""WAS A REGEX DEFECT, and the most consequential one in this extractor.

    `case\s+([A-Za-z0-9_"\', ]+?)\s*->` has no `.` in its character class, so
    `case InterfaceConstants.WORKFLOW_RESOURCE_PROVISIONING ->` matched NOTHING and the
    arm was invisible. The route had to be curated by hand in
    `knowledge/_reference/joins/task-names.yaml` to stop the tenth task silently
    vanishing. It is extracted now.
    """
    r = _repo(tmp_path, {
        "src/InterfaceConstants.java": _CONSTS,
        "src/TaskRoutingHelper.java": """package e5;
class TaskRoutingHelper {
  void go(String taskName) {
    switch (taskName) {
      case "EXPORT_STATE" -> { config.put(InterfaceConstants.VQ_NAME, workflowOpsVQ); }
      case InterfaceConstants.WORKFLOW_RESOURCE_PROVISIONING -> {
          config.put(InterfaceConstants.VQ_NAME, workflowOpsVQ);
      }
      default -> { }
    }
  }
}
"""})
    names = {n for f in routing(r) if f["kind"] == "RoutingRule"
             for n in f["props"]["task_names"]}
    assert names == {"EXPORT_STATE", "WORKFLOW_RESOURCE_PROVISIONING"}
    forms = {fm for f in routing(r) if f["kind"] == "RoutingRule"
             for fm in f["props"]["label_form"]}
    assert forms == {"literal", "constant:InterfaceConstants.WORKFLOW_RESOURCE_PROVISIONING"}


def test_a_destination_does_not_bleed_in_from_the_following_arm(tmp_path):
    """WAS A REGEX DEFECT. The old version read a 400-CHARACTER TEXT WINDOW after the
    case label, which runs straight into the next arm, so an arm that sets no
    destination silently reported its neighbour's. An arm's calls belong to that arm."""
    r = _repo(tmp_path, {"src/TaskRoutingHelper.java": """package e5;
class TaskRoutingHelper {
  void go(String taskName) {
    switch (taskName) {
      case "LOCAL" -> { handleLocally(event); }
      case "REMOTE" -> { config.put("vqName", workflowOpsVQ); }
      default -> { }
    }
  }
}
"""})
    by = {f["props"]["task_names"][0]: f["props"] for f in routing(r)
          if f["kind"] == "RoutingRule"}
    assert "sets_vq" not in by["LOCAL"]
    assert by["REMOTE"]["sets_vq"] == "workflowOpsVQ"


def test_a_default_arm_in_an_unrelated_switch_does_not_silence_the_finding(tmp_path):
    """WAS A REGEX DEFECT, and it hid a real one. The old check asked whether the word
    `default` appeared anywhere in the FILE. `TaskManagerHelperImpl.java` has a routing
    switch at :124 with no default and an unrelated switch at :284 with one, 160 lines
    away - so the platform's SECOND undefaulted routing switch was never reported."""
    r = _repo(tmp_path, {"src/TaskRoutingHelper.java": """package e5;
class TaskRoutingHelper {
  void route(String taskName) {
    switch (taskName) { case "A" -> { send(a); } }
  }
  void unrelated(String mode) {
    switch (mode) { case "X" -> { x(); } default -> { y(); } }
  }
}
"""})
    risks = [f for f in routing(r) if f["kind"] == "RoutingRisk"
             and "no default" in f["props"]["issue"]]
    assert len(risks) == 1
    assert risks[0]["props"]["switch_lines"] == [4]


def test_an_enum_constant_is_read_through_name(tmp_path):
    r"""`Task.EXPORT_STATE` and `Task.EXPORT_STATE.name()` are both written in this
    estate, and the joinable identity is the constant in the middle either way. The
    regex captured `(\w+)` and so happened to stop in the right place; the port says
    `the segment after the holder` instead of relying on a character class running out.
    """
    r = _repo(tmp_path, {"src/TaskRoutingHelper.java": """package e5;
class TaskRoutingHelper {
  void go(String taskName) {
    switch (taskName) {
      case "A" -> { info.setTaskType(TaskType.E5_WORKFLOW_OPS.name());
                    payload.put("fbName", Task.EXPORT_STATE.name()); }
      default -> { }
    }
  }
}
"""})
    props = next(f["props"] for f in routing(r) if f["kind"] == "RoutingRule")
    assert props["sets_task_type"] == "E5_WORKFLOW_OPS"
    assert props["sets_fb_name"] == "EXPORT_STATE"


def test_an_interface_field_is_a_constant_without_the_final_keyword(tmp_path):
    r"""An interface field is implicitly `public static final`, so the keyword is absent
    and `(?:static\s+)?final\s+String` could not see it. A constant label declared
    that way would have resolved to nothing and been reported `unresolved`."""
    f = JavaFile("interface Names { String PING = \"PING_TASK\"; }\n")
    assert f.constants()["PING"][0] == "PING_TASK"
    f2 = JavaFile("class C { String mutable = \"x\"; static final String K = \"v\"; }\n")
    assert "mutable" not in f2.constants() and f2.constants()["K"][0] == "v"


def test_routing_ignores_a_switch_outside_a_routing_class(tmp_path):
    """A selection rule, not a parsing one: a switch in an unrelated class is not the
    routing table however much it looks like one."""
    r = _repo(tmp_path, {"src/SomeOtherService.java": """package e5;
class SomeOtherService {
  void go(String taskName) { switch (taskName) { case "A" -> { send(a); } } }
}
"""})
    assert routing(r) == []


# -------------------------------------------------------------------------- events
def test_a_kafka_topic_default_is_not_an_event_type(tmp_path):
    """The rule, applied to a kind that had quietly escaped it. `e5.audit.events` and
    `e5.webhook.requests` are TOPIC names: every occurrence in source is a Spring
    `${VAR:default}` default, a Javadoc comment or a test, and qa runs
    `e5_qa_audit_events` / `e5_qa_webhook_requests` (observed 2026-10-06). A
    CloudEvents TYPE is read from no config key anywhere and is a payload contract."""
    r = _repo(tmp_path, {"src/main/java/C.java": """package e5;
class C {
  @Value("${kafka.topics.audit-events:e5.audit.events}") String auditTopic;
  String eventType = "e5.platform.event.workflow.STARTED";
}
"""})
    assert [f["name"] for f in events(r)] == ["e5.platform.event.workflow.STARTED"]


def test_an_event_type_in_a_comment_is_not_an_event_type(tmp_path):
    """WAS A REGEX DEFECT. 4 of the 23 facts came from Javadoc, and eleven lines of
    `WebhookRequestProducerTest.java` that matched are inside commented-out code."""
    r = _repo(tmp_path, {"src/main/java/C.java": """package e5;
/** Publishes to e5.platform.event.ghost.ONLY_IN_A_COMMENT. */
class C {
  // String x = "e5.platform.event.ghost.COMMENTED_OUT";
  String real = "e5.platform.event.workflow.STARTED";
}
"""})
    assert [f["name"] for f in events(r)] == ["e5.platform.event.workflow.STARTED"]


def test_production_beats_a_fixture_for_the_citation(tmp_path):
    """One fact per type per repo, so WHICH site it cites is the whole question. The
    old version took whichever file `os.walk` reached first, which let a JUnit fixture
    claim the name and suppress the real site - the citation then pointed into a test.
    """
    body = 'class C { String t = "e5.platform.event.workflow.STARTED"; }\n'
    r = _repo(tmp_path, {"src/test/java/ATest.java": body,
                         "src/main/java/Zzz.java": body})
    f, = events(r)
    assert f["path"] == "src/main/java/Zzz.java" and "test_only" not in f["props"]


def test_a_type_only_a_fixture_knows_is_marked_as_such(tmp_path):
    """event-store's only two event types live in `EventBuilderTest.java` and are
    lowercase (`...workflow.notstarted`) where production is `NOT_STARTED`. Dropping
    them would hide that; reporting them unmarked would assert it."""
    r = _repo(tmp_path, {"src/test/java/ATest.java":
                         'class A { String t = "e5.platform.event.workflow.notstarted"; }\n'})
    f, = events(r)
    assert f["props"]["test_only"] is True


def test_a_concatenated_prefix_is_not_a_complete_type(tmp_path):
    """credential-monitor writes `.setEventtype("e5.platform.event.workflow." + status)`.
    The literal is a PREFIX. Saying so is the difference between a contract and a
    fragment, and the regex said nothing."""
    r = _repo(tmp_path, {"src/main/java/C.java":
                         'class C { void f() { b.setEventtype("e5.platform.event.workflow." + s); } }\n'})
    f, = events(r)
    assert f["name"] == "e5.platform.event.workflow" and f["props"]["is_prefix"] is True


def test_a_json_schema_enum_is_reachable(tmp_path):
    """`eventstore-client-sdk` pins `e5.platform.event.customevent` in a JSON Schema
    enum - the most contractual statement of the type in the estate - and the old
    suffix list had no `.json`. JSON is read through the YAML front end, which is a
    superset and carries line marks."""
    r = _repo(tmp_path, {"schema/EventStoreInfo.schema.json":
                         '{"properties": {"eventtype": {\n  "enum": ["e5.platform.event.customevent"]}}}'})
    f, = events(r)
    assert f["name"] == "e5.platform.event.customevent" and f["line"] == 2
    assert f["props"]["context"] == "properties.eventtype.enum[0]"


# -------------------------------------------------------------------------- config
_ENVFILE = """name: svc
image:
  repository: e5-platform-svc
env:
  - name: ENVIRONMENT
    value: qa
  - name: "client_id"
    value:
  - name: DB_PASSWORD
    valueFrom:
      secretKeyRef:
        name: db
"""


def test_a_quoted_config_key_is_a_config_key(tmp_path):
    r"""WAS A REGEX DEFECT. `(\s*)-?\s*name:\s*([A-Za-z0-9_.\-]+)\s*$` has no `"` in
    its character class, so `- name: "client_id"` matched nothing. Three keys were
    invisible in all five of workflow-api's environment files, two of them
    `client_id` and `client_secret` - so "does this service take an OAuth secret from
    config?" answered no."""
    r = _repo(tmp_path, {"deployment-configs/qa-config.yaml": _ENVFILE})
    keys = {f["props"]["key"] for f in config(r) if f["kind"] == "ConfigKey"}
    assert keys == {"ENVIRONMENT", "client_id", "DB_PASSWORD"}


def test_a_name_that_is_not_an_env_var_is_not_a_config_key(tmp_path):
    """`name` also names the service, the image and a secret reference. The regex
    decided by peeking at the NEXT LINE for `value:`, plus a second guess that the key
    was SCREAMING_SNAKE - which made the naming CONVENTION part of the definition. The
    path `...env[i].name` is the actual rule."""
    r = _repo(tmp_path, {"deployment-configs/qa-config.yaml": _ENVFILE})
    keys = {f["props"]["key"] for f in config(r) if f["kind"] == "ConfigKey"}
    assert "svc" not in keys          # the top-level service name
    assert "db" not in keys           # secretKeyRef.name, one level deeper


def test_a_secret_reference_is_distinguished_from_a_literal(tmp_path):
    """`valueFrom` means the value is a secret reference. The old `has_value_binding`
    was True on all 1,836 occurrences, so it said nothing at all."""
    r = _repo(tmp_path, {"deployment-configs/qa-config.yaml": _ENVFILE})
    by = {f["props"]["key"]: f["props"]["binding"] for f in config(r)
          if f["kind"] == "ConfigKey"}
    assert by["DB_PASSWORD"] == "valueFrom" and by["ENVIRONMENT"] == "value"


def test_one_fact_per_key_not_one_per_environment_file(tmp_path):
    """1,836 occurrences of 397 keys, and the duplication was not harmless: the v3
    plan's worked example found `ConfigKey credential-monitor:taskManager @
    dev-config.yaml:15` cited FOUR TIMES as the evidence for one Component."""
    r = _repo(tmp_path, {"deployment-configs/dev-config.yaml": _ENVFILE,
                         "deployment-configs/qa-config.yaml": _ENVFILE,
                         "deployment-configs/prod-config.yaml": _ENVFILE})
    keys = [f for f in config(r) if f["kind"] == "ConfigKey"]
    assert len(keys) == 3
    assert keys[0]["props"]["declared_in"] == ["dev-config.yaml", "prod-config.yaml",
                                               "qa-config.yaml"]


def test_a_key_missing_from_one_environment_is_a_finding(tmp_path):
    """0 today, and that is why it is worth having: a key added to dev and forgotten
    in prod is an outage, and nothing here could see it before."""
    r = _repo(tmp_path, {"deployment-configs/dev-config.yaml": _ENVFILE,
                         "deployment-configs/prod-config.yaml":
                             "env:\n  - name: ENVIRONMENT\n    value: prod\n"})
    gaps = {f["props"]["key"]: f["props"]["missing_from"] for f in config(r)
            if f["kind"] == "ConfigKeyGap"}
    assert gaps == {"client_id": ["prod-config.yaml"],
                    "DB_PASSWORD": ["prod-config.yaml"]}


def test_an_unparseable_config_file_is_reported_not_half_read(tmp_path):
    """The line-regex version read a broken file one line at a time and reported
    whatever it recognised, so a broken config and a healthy one produced the same
    kind of answer."""
    r = _repo(tmp_path, {"deployment-configs/qa-config.yaml": "env:\n  - name: [A\n"})
    errs = [f for f in config(r) if f["kind"] == "ConfigParseError"]
    assert len(errs) == 1 and errs[0]["props"]["severity"] == "high"


def test_no_config_fact_carries_a_value(tmp_path):
    """A pod spec carries live DB credentials in `value:`. The fact schema has no field
    for one, and this is what keeps it that way."""
    r = _repo(tmp_path, {"deployment-configs/qa-config.yaml":
                         "env:\n  - name: DB_PASSWORD\n    value: hunter2\n"})
    assert "hunter2" not in json.dumps(config(r))


# --------------------------------------------------------------------------- proto
_PROTO = """syntax = "proto3";
package e5.test;
import "google/protobuf/empty.proto";

message Outer {
  message Inner { string a = 1; }
  Inner inner = 1;
  map<string, int32> counts = 2;
}

enum Colour { RED = 0; GREEN = 1; }

service Thing {
  rpc First (Outer) returns (google.protobuf.Empty);
  rpc Second (Outer) returns (Outer);
}
"""


def _proto_repo(tmp_path):
    return _repo(tmp_path, {"src/main/proto/t.proto": _PROTO})


def test_every_rpc_is_cited_at_its_own_line(tmp_path):
    r"""WAS A REGEX DEFECT, and the largest one in the set. The old extractor gave every
    rpc the SERVICE's line, so all 126 rpcs of deployment_management.proto cited one
    line - and that line was blank, because `^\s*service` under `re.M` also ate the
    newline before it. `./e5 cite check` was verifying the existence of a blank line."""
    f = {x["name"]: x for x in proto(_proto_repo(tmp_path))}
    assert f["e5.test.Thing"]["line"] == 13
    assert f["e5.test.Thing.First"]["line"] == 14
    assert f["e5.test.Thing.Second"]["line"] == 15


def test_a_request_type_is_fully_qualified(tmp_path):
    """`returns (Empty)` is not joinable - this estate declares both
    `google.protobuf.Empty` and `com.e5.platform.ss.proto.Empty`, in one file. Only the
    compiler can tell them apart, because only the compiler follows `import`."""
    f = {x["name"]: x for x in proto(_proto_repo(tmp_path))}
    assert f["e5.test.Thing.First"]["props"]["response"] == "google.protobuf.Empty"
    assert f["e5.test.Thing.First"]["props"]["request"] == "e5.test.Outer"


def test_a_nested_message_is_scoped_not_hoisted(tmp_path):
    """The regex read brace blocks and reported a nested message as a top-level one
    with the bare name, so `Image`, `Topic`, `S3` and `RDS` appeared twice each in
    deployment-mgmt as if they were peers of the messages that contain them."""
    names = {x["name"] for x in proto(_proto_repo(tmp_path))
             if x["kind"] == "ProtoMessage"}
    assert names == {"e5.test.Outer", "e5.test.Outer.Inner"}


def test_a_map_field_keeps_its_map_type(tmp_path):
    """protoc lowers `map<string, int32>` to `repeated OuterEntry`. Reporting the entry
    message would name a type the .proto does not declare."""
    f = {x["name"]: x for x in proto(_proto_repo(tmp_path))}
    flds = {d["name"]: d for d in f["e5.test.Outer"]["props"]["fields"]}
    assert flds["counts"]["type"] == "map<string, int32>"
    assert flds["counts"]["label"] == ""
    assert flds["inner"]["type"] == "e5.test.Outer.Inner"


def test_an_enum_is_extracted_at_all(tmp_path):
    """There was no `ProtoEnum`. 44 of them, and an enum is exactly the kind of thing a
    release diff needs to watch - adding a value is safe, renaming one is not."""
    e = [x for x in proto(_proto_repo(tmp_path)) if x["kind"] == "ProtoEnum"]
    assert len(e) == 1 and e[0]["props"]["values"] == ["RED", "GREEN"]


def test_a_proto_that_does_not_compile_is_reported(tmp_path):
    """An extractor that goes quiet when the compiler fails is worse than one that
    never used the compiler: the facts silently lose a whole service."""
    r = _repo(tmp_path, {"src/main/proto/t.proto":
                         'syntax = "proto3";\nmessage Broken { oops }\n'})
    out = proto(r)
    assert [x["kind"] for x in out] == ["ProtoParseError"]
    assert out[0]["props"]["severity"] == "high"


# ----------------------------------------------------------------- the YAML front end
def test_a_yaml_scalar_carries_its_path_and_its_line():
    """`extract_config` asked whether the NEXT LINE matched `value:` to decide whether
    a `- name:` entry was an environment variable, because `name` also names
    containers, ports and volumes. The path answers it outright."""
    f = YamlFile("spec:\n  containers:\n    - name: app\n      env:\n"
                 "        - name: DB_HOST\n          value: x\n")
    paths = {n.path: (n.value, n.line) for n in f.scalars()}
    assert paths["spec.containers[0].name"] == ("app", 3)
    assert paths["spec.containers[0].env[0].name"] == ("DB_HOST", 5)
    assert f.has("spec.containers[0].env[0].value")


def test_an_unparseable_yaml_file_is_not_an_empty_one():
    """A file that does not parse is a defect in the repository; a file with nothing in
    it is not. Reporting both as nothing loses the first."""
    bad = YamlFile("a: [1,\n")
    assert not bad.ok and bad.error
    assert YamlFile("").ok and YamlFile("").scalars() == []


# ------------------------------------------------------------------------- graphql
_SDL = """type Query {
  deployment(id: ID!): Deployment
  deployments(
    tenant: String!
    limit: Int
  ): [Deployment!]!
}

scalar JSON

type Deployment { id: ID!  name: String }
"""


def test_a_root_field_is_cited_at_its_own_line(tmp_path):
    """The same defect the proto port found for rpcs: every root field was given the
    line of the `type Query {` that contains them, so `Query.deployment` and
    `Query.deployments` cited one line between them."""
    r = _repo(tmp_path, {"src/main/resources/graphql/q.graphqls": _SDL})
    by = {f["name"]: f["line"] for f in graphql(r) if f["kind"] == "GraphQLRootField"}
    assert by == {"Query.deployment": 2, "Query.deployments": 3}


def test_an_argument_list_broken_across_lines_is_still_read(tmp_path):
    r"""`(\w+)\s*(\([^)]*\))?\s*:\s*(.+)` ran LINE BY LINE, so `[^)]*` could never
    close on a multi-line argument list."""
    r = _repo(tmp_path, {"src/main/resources/graphql/q.graphqls": _SDL})
    f = next(x for x in graphql(r) if x["name"] == "Query.deployments")
    assert f["props"]["args"] == [{"name": "tenant", "type": "String!"},
                                  {"name": "limit", "type": "Int"}]
    assert f["props"]["returns"] == "[Deployment!]!"


def test_a_scalar_is_a_type(tmp_path):
    """The kind list was `(type|input|interface|enum|union)`. `scalar JSON` is how this
    schema says "arbitrary payload", which is exactly the thing a reader asks about."""
    r = _repo(tmp_path, {"src/main/resources/graphql/q.graphqls": _SDL})
    kinds = {f["name"]: f["props"]["graphql_kind"] for f in graphql(r)
             if f["kind"] == "GraphQLType"}
    assert kinds == {"Query": "type", "JSON": "scalar", "Deployment": "type"}


def test_a_client_operation_document_is_not_a_schema(tmp_path):
    """A `.graphql` outside `resources/` is a client-side query, not SDL. A selection
    rule kept from the regex version, because it is a correct one."""
    r = _repo(tmp_path, {"src/test/queries/get.graphql": "query Get { a }\n"})
    assert graphql(r) == []


# ---------------------------------------------------------------------- migrations
def test_sqlglot_and_the_fallback_agree_on_the_line(tmp_path):
    """A line number is evidence a reader checks, and half the statements in this
    estate come from each path. They must not disagree about where a table is."""
    sql = ("CREATE TABLE plain (x int);\n\n"
           "CREATE TABLE IF NOT EXISTS public.fancy\n(\n"
           "  nm character varying COLLATE pg_catalog.\"default\"\n)\n"
           "TABLESPACE pg_default;\n")
    p = tmp_path / "db" / "migration" / "V1__x.sql"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(sql)
    by = {f["name"]: (f["line"], f["props"]["parsed_by"])
          for f in migrations(DirRepo(id="t", root=tmp_path)) if f["kind"] == "Table"}
    assert by == {"plain": (1, "sqlglot"), "fancy": (3, "regex-fallback")}


# ------------------------------------------------- the dependency split, which is a rule
_THIRD_PARTY = ("tree_sitter", "sqlglot", "graphql", "grpc_tools", "yaml",
                "neo4j", "networkx", "semgrep", "fastapi", "duckdb")


def _imports(path: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module.split(".")[0])
    return out


def test_the_retrieval_path_takes_no_dependency():
    """Extraction may take dependencies - it runs in CI and in Docker. Retrieval must
    not, so the plugin works on a laptop with nothing installed.

    TRANSITIVELY. The first version of this test read only the three entry files' own
    import lines, which is not the property anyone cares about: `kb_core` imports
    `impact_graph`, which imports `graph_model`. Had `graph_model` grown a networkx
    import - and factoring the traversal into networkx was the obvious place to put it -
    this test would have passed and the plugin would have failed on a bare laptop at the
    first `platform_impact` call. It now walks the whole LOCAL import chain.
    """
    seen, queue, chain = set(), ["kb_core.py", "kb_nav.py", "mcp_server.py"], {}
    while queue:
        mod = queue.pop()
        if mod in seen:
            continue
        f = KB / "tools" / mod
        if not f.is_file():
            continue
        seen.add(mod)
        for name in _imports(f):
            if (KB / "tools" / f"{name}.py").is_file():
                chain.setdefault(f"{name}.py", mod)
                queue.append(f"{name}.py")
    assert {"kb_nav.py", "task_flows.py", "catalog.py"} <= seen, \
        "the chain walk found nothing - mcp_server no longer reaches kb_nav?"
    for mod in sorted(seen):
        bad = _imports(KB / "tools" / mod) & set(_THIRD_PARTY)
        via = "" if mod in ("kb_core.py", "query.py", "mcp_server.py") \
            else f" (reached via {chain[mod]})"
        assert not bad, (f"{mod} imports {bad}{via} - everything the retrieval path "
                         f"can reach must stay stdlib-only")


def test_the_extraction_dependency_is_declared():
    req = (KB / "tools" / "requirements-extract.txt")
    assert req.is_file(), "a dependency nobody declared is a dependency CI will not have"
    body = req.read_text()
    assert "tree-sitter" in body and "tree-sitter-java" in body


# -------------------------------------------------------------------------- the harness
def test_the_diff_harness_runs_and_reports():
    r = subprocess.run([sys.executable, "-m", "extract.cli", "diff", "db_bindings",
                        "--repo", "platform-core"],
                       cwd=KB, capture_output=True, text=True)
    assert "db_bindings" in r.stdout, r.stdout + r.stderr
    assert "new=" in r.stdout and "old=" in r.stdout

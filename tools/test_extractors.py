"""Tests for the SQL migration extractor.

These exist because the estate report asked a question nothing else asks - "what does
the platform have that no card names" - and the answer contained a table called
`CREATE`. A table name that is a SQL keyword is not a documentation gap; it is an
extractor reading English as SQL.

The extractor moved to `extract/declared/migrations.py` (sqlglot, plus a counted regex
fallback for the 46 statements sqlglot cannot read). These tests moved with it rather
than being deleted: the defects they pin are properties of the ESTATE, not of the
implementation that happened to hit them.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from extract.core import DirRepo                       # noqa: E402
from extract.declared.migrations import migrations, _KEYWORDS   # noqa: E402
from extract.parse.sqlfile import blank_comments       # noqa: E402


def _run(tmp_path, sql: str) -> list[dict]:
    p = tmp_path / "src" / "main" / "resources" / "db" / "migration" / "V1__x.sql"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(sql)
    return migrations(DirRepo(id="t", root=tmp_path))


def tables(tmp_path, sql: str) -> list[tuple[str, int]]:
    return [(f["name"], f["line"]) for f in _run(tmp_path, sql) if f["kind"] == "Table"]


def changes(tmp_path, sql: str) -> list[tuple[str, str, str]]:
    return [(f["props"]["table"], f["props"]["op"], f["props"]["column"])
            for f in _run(tmp_path, sql) if f["kind"] == "TableChange"]


def test_a_comment_above_a_statement_does_not_become_the_table_name(tmp_path):
    """WAS WRONG ONCE, in the live knowledge base.

    `CREATE\\s+TABLE\\s+` matched the words in `-- Create table`, and the `\\s+` after
    TABLE then crossed the newline and captured the first word of the real statement. So
    deployment-mgmt gained a table named `CREATE`, and - because finditer resumes after
    the match it already made - LOST `dynamic_scaling_request` entirely. One comment,
    one phantom table, one real table missing.
    """
    got = tables(tmp_path, "-- Create table\n"
                 "CREATE TABLE IF NOT EXISTS public.dynamic_scaling_request (\n"
                 "    scaling_id uuid NOT NULL\n);\n")
    assert got == [("dynamic_scaling_request", 2)]


def test_a_commented_out_rollback_is_not_a_schema_change(tmp_path):
    """Flyway migrations conventionally carry their undo in a trailing comment. Read as
    SQL, the knowledge base believed `is_active` was added and then dropped in the same
    migration - two facts, one of them describing a column the database still has."""
    sql = ("ALTER TABLE public.dynamic_scaling_subscription\n"
           "    ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT false;\n"
           "\n"
           "-- ALTER TABLE public.dynamic_scaling_subscription "
           "DROP COLUMN IF EXISTS is_active;\n")
    assert changes(tmp_path, sql) == [("dynamic_scaling_subscription", "add", "is_active")]
    assert changes(tmp_path,
                   "--ALTER TABLE workflow_ops_state DROP COLUMN IF EXISTS version;\n") == []


def test_blanking_a_comment_moves_no_line_and_no_offset(tmp_path):
    """Comments are blanked rather than deleted so that every reported line number is
    still the line the statement is actually on. A citation gate downstream checks
    those numbers against the file."""
    sql = "/* create\n   table */\nCREATE TABLE audit_log (x int);\n-- tail\n"
    out = blank_comments(sql)
    assert len(out) == len(sql) and out.count("\n") == sql.count("\n")
    assert tables(tmp_path, sql) == [("audit_log", 3)]


def test_a_semicolon_inside_a_dollar_quoted_block_does_not_end_the_statement(tmp_path):
    """A PostgreSQL `DO $$ ... $$` body contains semicolons. Splitting on `;` cuts it
    into fragments, and the fragments then look like statements - which is how a
    `format('CREATE TABLE audit.%I PARTITION OF ...')` inside a function body became a
    table with an EMPTY NAME in audit-service."""
    sql = ("DO $$\nBEGIN\n  EXECUTE format('CREATE TABLE audit.%I PARTITION OF "
           "audit.events', p);\nEND $$;\n"
           "CREATE TABLE real_table (x int);\n")
    assert tables(tmp_path, sql) == [("real_table", 5)]


def test_two_columns_added_in_one_statement_are_two_changes(tmp_path):
    """`ALTER TABLE t ADD COLUMN a, ADD COLUMN b;` - the regex needed the literal
    `ALTER TABLE` prefix for each match, so it saw the first and never the second.
    `fb_worker_pod.max_replicas` and `schedule.end_date` were invisible."""
    sql = ("ALTER TABLE public.fb_worker_pod\n"
           "    ADD COLUMN IF NOT EXISTS min_replicas INT,\n"
           "    ADD COLUMN IF NOT EXISTS max_replicas INT;\n")
    assert changes(tmp_path, sql) == [("fb_worker_pod", "add", "min_replicas"),
                                      ("fb_worker_pod", "add", "max_replicas")]


def test_adding_a_constraint_is_not_adding_a_column(tmp_path):
    """`ADD CONSTRAINT foo UNIQUE (x)` yielded a column named CONSTRAINT. The AST has
    a different node type for it, so the distinction is free now."""
    sql = "ALTER TABLE t ADD CONSTRAINT chk_range CHECK (a < b);\n"
    assert changes(tmp_path, sql) == []


def test_a_statement_sqlglot_cannot_read_is_counted_not_dropped(tmp_path):
    """sqlglot degrades 46 of this estate's statements to an opaque `Command`, 11 of
    them CREATE TABLEs carrying pgAdmin's `TABLESPACE pg_default`. The fallback keeps
    the table; the count is what says the fallback is still needed."""
    sql = ("CREATE TABLE IF NOT EXISTS public.common_util\n(\n"
           "    id uuid NOT NULL DEFAULT gen_random_uuid(),\n"
           "    nm character varying COLLATE pg_catalog.\"default\"\n)\n"
           "TABLESPACE pg_default;\n")
    out = _run(tmp_path, sql)
    tbl = [f for f in out if f["kind"] == "Table"]
    gap = [f for f in out if f["kind"] == "SqlParseGap"]
    assert [f["name"] for f in tbl] == ["common_util"]
    assert tbl[0]["props"]["parsed_by"] == "regex-fallback"
    assert gap and gap[0]["props"]["fell_back_to_regex"] == 1


def test_the_real_estate_has_no_table_named_after_a_sql_keyword():
    bad = []
    for line in (HERE.parent / "graph" / "facts.jsonl").open():
        d = json.loads(line)
        if d["kind"] == "Table" and d["name"].lower() in _KEYWORDS:
            bad.append(f"{d.get('component', d['repo'])} {d['name']} @ "
                       f"{d['path']}:{d['line']}")
    assert not bad, bad

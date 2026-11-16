"""SQL DDL: statements with their line numbers, then sqlglot per statement.

    for st in statements(text):
        st.sql, st.line, st.parsed       # parsed is None when sqlglot cannot

WHY SPLIT FIRST
---------------
`sqlglot.parse(whole_file)` gives expressions without reliable line numbers and fails
the WHOLE file on one unsupported statement - and this estate has plenty: 46 statements
fall back to an opaque `Command`, and `workflow-api V9__...sql` does not parse at all.
Splitting first means one unsupported statement costs one statement.

The splitter has to know two things a `text.split(";")` does not:

  * **a `--` comment is not SQL.** The most expensive defect this package records came
    from exactly that: `-- Create table foo` matched `CREATE ... TABLE`, made `CREATE`
    the table name, and `finditer` then resumed PAST the real statement, so the table
    was never extracted. Comments are BLANKED, not removed, so every byte offset - and
    therefore every line number - stays where it was.
  * **`$$ ... $$` is one token.** A PostgreSQL `DO $$ BEGIN ... ; ... END $$;` block
    contains semicolons that do not end the statement.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

try:
    import sqlglot
    from sqlglot import exp
except ImportError as e:            # pragma: no cover - the message IS the behaviour
    raise ImportError(
        "the SQL front end needs sqlglot:\n"
        "    python3 -m pip install -r tools/requirements-extract.txt\n"
        "Retrieval does not need it - only extraction does. See extract/PORTING.md."
    ) from e

DIALECT = "postgres"

# sqlglot logs a WARNING per statement it cannot parse, and there are 46 - enough to
# bury the indexer's own output. Silenced here and COUNTED instead: every file that
# falls back emits a `SqlParseGap` fact carrying the number, which is a record that
# survives the log being thrown away.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

_COMMENT = re.compile(r'--[^\n]*|/\*.*?\*/', re.S)
_DOLLAR = re.compile(r'\$[A-Za-z_]*\$')


def blank_comments(t: str) -> str:
    """Replace comment bodies with spaces, keeping newlines and every byte offset."""
    return _COMMENT.sub(lambda m: re.sub(r'[^\n]', ' ', m.group(0)), t)


_STRING = re.compile(r"'(?:[^']|'')*'")


def blank_strings(t: str) -> str:
    """The same treatment for single-quoted literals.

    A table name is never inside a string, but DDL written BY a PL/pgSQL function is:
    `EXECUTE format('CREATE TABLE audit.%I PARTITION OF audit.events', p)`. The regex
    fallback read that as a declaration and produced a table whose name was the empty
    string - `audit.%I` with the `%I` not matching, leaving nothing after the dot.
    """
    return _STRING.sub(lambda m: re.sub(r'[^\n]', ' ', m.group(0)), t)


@dataclass
class Stmt:
    sql: str
    line: int                 # 1-based line of the statement's first non-space char
    parsed: object = None     # a sqlglot Expression, or None
    error: str = ""

    @property
    def opaque(self) -> bool:
        """sqlglot parsed it but understood nothing - its `Command` fallback."""
        return self.parsed is None or isinstance(self.parsed, exp.Command)


def split(text: str) -> list[Stmt]:
    t = blank_comments(text)
    out, start, i, n = [], 0, 0, len(t)
    while i < n:
        if t[i] == "$":
            m = _DOLLAR.match(t, i)
            if m:
                close = t.find(m.group(0), m.end())
                i = n if close < 0 else close + len(m.group(0))
                continue
        if t[i] == "'":
            j = t.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if t[i] == ";":
            out.append((start, t[start:i]))
            start = i + 1
        i += 1
    if t[start:].strip():
        out.append((start, t[start:]))
    stmts = []
    for off, sql in out:
        lead = len(sql) - len(sql.lstrip())
        if not sql.strip():
            continue
        stmts.append(Stmt(sql=sql.strip(), line=t[:off + lead].count("\n") + 1))
    return stmts


def statements(text: str) -> list[Stmt]:
    for st in split(text):
        try:
            st.parsed = sqlglot.parse_one(st.sql, dialect=DIALECT,
                                          error_level=sqlglot.ErrorLevel.IGNORE)
        except Exception as e:                      # sqlglot raises several types
            st.parsed, st.error = None, str(e).replace("\n", " ")[:160]
        yield st

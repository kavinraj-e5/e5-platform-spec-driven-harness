"""Structured extraction. One fact schema, one git accessor, one parser per language.

    python3 -m extract.cli run                 extract everything (declared tier)
    python3 -m extract.cli diff <name>         new vs the old regex extractor, per repo
    python3 -m extract.cli parse <file.java>   what the Java front end sees

WHY THIS PACKAGE EXISTS
-----------------------
`tools/extractors.py` WAS 11 extractors sharing one idea: find a pattern in text with a
regular expression. All eleven now live here, behind a parser chosen per surface, and
that module keeps only the registry and the call signature `tools/sync.py` uses.

Every defect fixed in the regexes over the preceding month was the same defect - a regex
cannot see structure, so it matches inside comments, inside strings, and inside test
fixtures:

  * `-- Create table foo` made `CREATE` a table name, and the following statement was
    skipped, losing a real table
  * two commented-out Flyway rollbacks were read as real DROPs
  * `#{scalingEstimatorConsumerConfig.topic}` was reported as an identifier
  * a JUnit argument was indistinguishable from production configuration

Those were fixed one at a time, by hand, with a test each. A parser removes the class.

It was not a tidy-up. `extract.cli diff` runs each parser against the regex it replaces
over all 23 mirrors, and nothing lands until every difference is explained in
`extract/PORTING.md`. Across the eleven ports it found **1,860 facts cited at the wrong
line**, 11 whose identity was a value the deployment injects, 3 pointing at something
that does not exist, and a contract surface that was simply absent - 27% of one
component's public keywords, 44 proto enums, a GraphQL scalar, three quoted config keys.
It also caught five bugs in the ports themselves before they landed.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
The retrieval path - `tools/kb_core.py`, `tools/query.py`, `tools/mcp_server.py` - stays
on the standard library so the plugin works on a laptop with nothing installed.
Extraction may take dependencies because it runs in CI and in Docker. The split is
enforced by a test.
"""

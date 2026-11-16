"""Language front ends. Each turns source TEXT into structure, and nothing else.

A front end knows about syntax. It never knows about E5, about virtual queues, or about
what a fact is - that is `extract/declared/`. Keeping the split means a parser bug and a
platform-knowledge bug cannot be confused for one another, which they repeatedly were in
`tools/extractors.py`, where `_VQ_PROP` encoded both "what a yaml key looks like" and
"which keys are queues".
"""

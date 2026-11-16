---
description: Verify the harness is wired — credentials, mirrors, catalog, toolchain
---
Run the harness health check and interpret it for the user.

Locate the knowledge base first. Prefer `$E5_KB_ROOT`; otherwise read the path from
`${CLAUDE_PLUGIN_ROOT}/hooks/kb_root.txt`, which `tools/prepare_plugin.py` stamps at
install time. The plugin is installed as a copy and does not contain the knowledge base,
so neither location can be assumed.

Then run, from that directory, **always with `--no-prompt`**:

```
./e5 init --no-prompt
```

init **checks only**: the two credentials, mirror hygiene, the catalog and the tools. It
clones nothing. Cloning, compiling and rebuilding the knowledge base is `/e5-sync`.

If init reports that the credentials file is missing or still holds the template, tell the
user to fill in `~/.e5/credentials.md` (created by `./e5 setup` from
`templates/credentials.md`) and run `/e5-init` again.

## If the user wants to change credentials

The credentials live in one markdown file: `~/.e5/credentials.md` (or the path in
`$E5_GIT_CRED_FILE`). `init` reads that file on every run and never writes
it. To change a credential, the user edits that file and re-runs `/e5-init`.

**Do not collect the credential yourself.** Anything typed into this conversation is written
to the session transcript, and a token in a transcript is a leaked token.

## Reporting the result

Report what it found, and be specific about consequences rather than repeating the output:

- **GitHub credential missing or unreachable** — nothing can be indexed. This is blocking.
- **JFrog missing** — indexing still works, but the task protocol stays invisible: the
  `Command`, `EndpointType` and `WorkerType` enums live only in published artifacts with no
  source in any repository. Builds will also fail. Not blocking for queries, blocking for
  anything that compiles.
- **A mirror with credentials in its remote** — a real leak. `python3 tools/mirror.py sync`
  re-points it. Say this plainly; do not bury it.
- **A repo not on a release branch** — `main` is a different line of development in this
  estate, not "latest". Facts from such a repo are not current platform behaviour.
- **Catalog invalid** — refuse to go further and show `python3 tools/catalog.py check`.
- **A missing tool** — say which sync step it costs: no `java` means the JVM repos are not
  compiled; no `pyright-langserver` means no Python call graph (`./e5 setup` installs it).

If everything passes and init says repos are not on this machine yet, the next step is
`/e5-sync`: it clones every platform repo into `platform/<system>/<repo>` inside the harness,
compiles them, runs the extractors and pyright and rebuilds the knowledge base. Say that the
first run is about 1 GB and 15-30 minutes. Otherwise the next step is `/e5-feature` or
`/e5-bugfix`.

Never print a credential, and never echo a value from the credentials file. `init` reports
presence only, and so should you.

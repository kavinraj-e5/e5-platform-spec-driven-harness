# Installing the E5 harness

The harness repo holds the tools, the extractors, the Claude Code plugin and the knowledge
base. **It holds no platform service code.** `/e5-sync` clones the platform repos onto your
machine with your own credentials, into `platform/` inside the harness, then compiles them
and rebuilds the knowledge base from them.

## You need
| Need | Why |
|---|---|
| `python3` 3.10 or newer, and `git` | required |
| Claude Code (`claude` CLI) | to use the harness |
| a GitHub personal access token with read access to the element5inc repos | cloning the platform repos |
| a JFrog username and password or token | private build dependencies; the task protocol and SDKs are published there |
| a JDK (17 and 21) and `gradle` *(optional)* | compiling the JVM repos; reported, not required |
| `semgrep` *(optional)* | re-checking the known issues |

`pyright` (the Python call graph) is installed for you by `./e5 setup`.

## Install (once)
```
git clone https://github.com/element5inc/<harness-repo>.git ~/e5-platform-harness
cd ~/e5-platform-harness
./e5 setup
```
`./e5 setup`:
- checks the tools
- creates one Python environment (`.venv-mcp/`), including pyright
- creates **`~/.e5/credentials.md`** from `templates/credentials.md`, readable only by you
- installs the `e5-platform` plugin into Claude Code

It never reads or prints a credential.

## Then, in Claude Code
1. **Fill in `~/.e5/credentials.md`:** your GitHub username and token, your JFrog user and
   password. The format is in the file. Do not commit it, paste it into a chat, or put it
   in the harness folder.
2. **Restart Claude Code** in the harness folder.
3. **`/e5-init`** checks the GitHub and JFrog credentials separately, and the tools. It
   changes nothing.
4. **`/e5-sync`** builds your copy of everything. The first run is about 1 GB and 15-30
   minutes; later runs only redo what moved.
   - clones every platform repo as a bare mirror into `~/.e5/cache`
   - checks each one out at the release the knowledge base indexes, by system:
     ```
     platform/
       workflow-development/workflow-library/   platform-core, wf-scaffolding, variant-scaffolding
       app-development/app/                     rf-fb-base-image
       app-development/app-library/             rf-fb-worker-core, app-sdk, non-rpa-worker-core
       app-development/ui-synthesizer
       subsystems/                              interface, deployment-mgmt, workflow-manager,
                                                workflow-ops, monitoring-service, dynamic-scaling, ...
       communication/                           akst-service, task-sdk-java, task-sdk-python, ...
       reporting/                               event-store, event-processor, audit-service, ...
       platform-delivery/                       workflow-config, devops-pipeline
     ```
     These are the same folder names as `knowledge/`, so a component's document and its
     code sit at the same relative place.
   - compiles the JVM repos (gradle or maven, JFrog for private dependencies)
   - runs the extractors, and pyright over the Python repos, into `graph/`
   - regenerates the knowledge base's generated parts and runs every gate
5. **`/e5-feature`**, **`/e5-bugfix`**, **`/e5-update-knowledge-base`**: work on the platform.

You have five commands: `/e5-init`, `/e5-sync`, `/e5-feature`, `/e5-bugfix`,
`/e5-update-knowledge-base`.

## The checkouts under `platform/`
- They are git worktrees of the mirrors, detached at the indexed release. They share the
  mirror's git config, which carries no credential (`/e5-init` audits it). `platform/` is
  git-ignored: nothing in it is ever committed to the harness.
- Read and browse them freely. **Do not make changes in them.** A checkout with local edits
  is left alone by sync and reported as DIRTY, and the extractors never read it.
  `/e5-feature` and `/e5-bugfix` make their changes in separate worktrees, one per change
  set, under `~/.e5/workspace`.

## Keeping up to date
- `./e5 update` pulls the newest harness and knowledge base, refreshes the environment and
  updates the plugin. Restart Claude Code afterwards.
- `/e5-sync` fetches the newest commits and rebuilds what moved.
- Corrections you record (`/e5-update-knowledge-base`) are files in this repo. Open a pull
  request so the whole team gets them.

## Where things live
| What | Where |
|---|---|
| harness, plugin, knowledge base | the folder you cloned |
| the platform repos, by system | `platform/` in that folder (git-ignored) |
| your credentials | `~/.e5/credentials.md` (override with `$E5_GIT_CRED_FILE`) |
| bare mirrors of the platform repos | `~/.e5/cache` (override with `$E5_CACHE`) |
| build trees while compiling | `~/.e5/scip` (removed after each build) |
| worktrees for an approved change | `~/.e5/workspace` (override with `$E5_KB_WORKSPACE_ROOT`) |
| published artifacts downloaded for introspection | `~/.e5/artifacts` |

## Troubleshooting
- **`/e5-init` says the GitHub credential is missing:** the file still holds the template
  placeholders, or it is not at `~/.e5/credentials.md`.
- **A repo is reported `NO-BUILD` in sync:** that release does not compile on your machine
  (often a JDK version or a JFrog credential). It is advisory: the other extractors do
  not need a build. `./e5 init` names the missing tool.
- **No `kb_*` tools in Claude Code:** run `./e5 setup` again (it rebuilds `.venv-mcp/`),
  then restart Claude Code.
- **The plugin cannot find the knowledge base:** the harness folder moved. Run `./e5 setup`
  again, or `export E5_KB_ROOT=/path/to/the/harness`.

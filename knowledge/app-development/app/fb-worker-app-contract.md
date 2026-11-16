---
type: architecture
name: FB Worker App Contract
provenance: code
verified: 2026-10-10
---

# What the platform gives an app team, and what it demands back

An fb-worker image is **not** a platform deliverable. The platform ships three things:

| Deliverable | Card |
|---|---|
| `e5-rf-fb-base` — the container with Citrix, Xvfb, a window manager, VNC, clipboard tools, OCR, conda | [rf-fb-base-image](rf-fb-base-image.md) |
| `rf-fb-worker-core` — the Python library: poll loop, AKST pull, `TaskComplete` reply, credentials, Citrix window helpers | [rf-fb-worker-core](rf-fb-worker-core.md) |
| `hchb_uisynthesizer_client` + the UI Synthesizer service — runtime generation of template-match needles | [ui-synthesizer](ui-synthesizer.md) |

An app team writes Robot Framework on top and builds an image. **Those app repos are out of
scope for this knowledge base** — their screens, locators, function blocks and business error
codes are customer automation, not platform surface. What *is* platform surface is the
contract below: break it and the app does not start, and nothing in the library will tell you
which part you missed.

## The library has no base class

There is no `BaseWorker` to subclass. `api/interfaces.py` is entirely commented out. The
extension mechanism is **Robot Framework duck-typing**: the library calls keywords and imports
paths **by name**, and an app satisfies the contract by happening to define them.

| The library requires | Where it is looked up | When |
|---|---|---|
| `./robots/init_fb_worker.robot` | `api/keywords.py:78` — `BuiltIn().import_resource(...)` | **at module import time** |
| `initialize-fb` | `FBCore.py:487` | once, at worker start |
| `fb-pre-execution` / `fb-post-execution` | `api/decorators.py:118`, `:128` | around every task |
| `teardown-start` (takes an `errMsg` argument) | `FBCore.py:402`, `api/decorators.py:145` | on exit and on error |
| `fb-<FbName>-execute` | `FBCore.py:296-300` | per task |
| `./robots/functionblocks/*.robot` | `FBCore.py:462-468` | discovery |
| `./schema/configuration_schema.json` | `FBCore.py:417`, `file_utils.py:81` | validation |
| `./yamls/`, `./resources/TaskResources.json` | `file_utils.py:139`, `:106-127` | load |

Two consequences that cost people time:

- The import at `api/keywords.py:78` is **relative to the process working directory** and runs
  at *import* time, not call time. The app must be started with its own `code/` as CWD, and the
  library cannot be imported outside Robot at all.
- `fb-<FbName>-execute` is assembled as `"fb-" + fb_name + "-execute"` with a trailing `FB`
  stripped from `fb_name`, where `fb_name` comes off the task envelope
  (`taskInfo.taskPayload.fbName`). Robot keyword matching ignores case, spaces and underscores,
  which is why on-disk spellings vary and still resolve — and why a genuine typo surfaces as
  "no keyword found" at task time rather than at build time.

## Nothing in the platform maps `fbName` to an app

The envelope names a function block. The library turns that into a keyword name. **No registry,
index or routing table records which app implements which `fbName`** — the binding exists only
because the right image was deployed into the right namespace. This is the one edge in the
fb-worker path that cannot be derived from any repo the platform owns.

## Lifecycle: what the platform does and where the app is called

```
container start        base image: Xvfb :99, window manager, VNC         (app entrypoint starts them)
  ↓
app entrypoint         conda run … python app.py → robot.run(start.robot)
  ↓
Start FB Worker        rf-fb-worker-core FBCore.start_fb_worker()
  ↓
initialize-fb          ← APP: log in, reach a usable application state
  ↓
poll loop              isTaskAvailable → load_task → 30 s sleep when idle
  ↓
fb-pre-execution       ← APP: assert the application is still in a good state
fb-<Name>-execute      ← APP: do the work, stream results with `E5 Yield`
fb-post-execution      ← APP
  ↓
teardown-start         ← APP: release the credential, end the remote session
```

`E5 Yield` (`api/keywords.py:107`, `e5_yield_pipeline`) is the **only** result channel. A
function block that returns its payload with Robot's `RETURN` will run to completion and report
nothing.

The worker is **single-threaded and single-task**: in-flight state is held in process-global
`builtins` attributes (`FBCore.py:209-220`), which structurally forbids concurrency.

## The two automation surfaces

Apps that drive a remote application over Citrix have two completely different interaction
models in one process, and conflating them is the most common source of confusion:

1. **Browser automation, briefly.** Login goes through Citrix StoreFront in **Playwright
   Chromium with `headless=False`** — non-headless is mandatory, because the ICA launch has to
   land on the X display rather than in a headless context. StoreFront returns an ICA file,
   Workspace App starts `wfica`, and the published application appears as an X11 window on
   `:99`.
2. **Pixel automation, for the rest of the run.** The browser is closed. From here there is no
   DOM, no accessibility tree and no control handle — Citrix delivers a pixel stream. Every
   interaction is a template match against a screenshot plus synthetic keystrokes, addressed
   through `wmctrl` window titles.

This is why [ui-synthesizer](ui-synthesizer.md) exists, why `KeyboardEventMode=Unicode`
is set in the base image, and why a VDA-side font or DPI change is a fleet-wide outage rather
than a cosmetic one.

## Getting text out of a Citrix session

There is one mechanism: select, Ctrl+C, read the client-side X11 clipboard. The library wraps
it in `e5_get_clipboard_value` (`integrations/citrix/HCHBAutomation.py`), which on Linux presses
**Alt+F8 then Esc** before reading. That is not a workaround for a focus bug — it is what makes
Citrix take ownership of the X `CLIPBOARD` selection. Remove it and reads fail on healthy
sessions.

The chain has seven hops:

```
remote app → Windows clipboard on the VDA → Citrix clipboard channel → wfica
           → X11 CLIPBOARD on :99 → xclip → RPA.Desktop → the keyword
```

It fails silently. See
clipboard-read-failure-is-silent (archived).

## Ending the session is the app's job, and the library makes it easy to get wrong

Three actions are routinely conflated and only one ends the remote session:

| Action | Effect |
|---|---|
| the application's own logout menu | none — the Windows session on the VDA keeps running |
| `Close Citrix Window` → `wmctrl -i -c` (`HCHBAutomation.py:389,406,476-492`) | **disconnect** — the session survives on the broker and the next login reattaches to it |
| Citrix Connection Center → **Log Off** | the only path that destroys the session |

The library provides the window-closing helper and **no session-logoff helper at all**, so the
default path an app reaches for is the one that leaks sessions. See
citrix-sessions-are-disconnected-never-logged-off (archived).

There is also **no SIGTERM handler anywhere in the library** (`grep SIGTERM|SIGINT|signal` → 0
hits); shutdown is flag-file based and disabled by default. Every pod restart or eviction is a
pure disconnect, and that is platform behaviour, not an app choice.

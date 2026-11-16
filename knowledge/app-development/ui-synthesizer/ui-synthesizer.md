---
type: tooling
name: UI Synthesizer
repo: ui-synthesizer
repo_path: platform-whole-context/platform-workflow-app-building/HCHB.UiSynthesizer
branch: release-2.0.0
commit: da8b600
language: csharp
build: msbuild
deployment: "Windows host (IIS/Kestrel) + a Windows service + a containerised batch job. Not in k8s."
owns_schema: false
provenance: code
verified: 2026-10-10
status: no-release-branch
---

# UI Synthesizer

**Platform-provided tooling for Citrix UI automation. It renders text to a bitmap so a worker
can template-match that bitmap against a remote desktop's pixel stream.**

The repo, namespaces and NuGet package are all prefixed `HCHB` for historical reasons. Nothing
in it is HCHB-specific — the input is `(text, weight, size, alignment, background, foreground)`
and the output is a PNG. Read the name as a legacy prefix, not a scope.

## The problem it exists to solve

A Citrix session delivers **pixels**. There is no DOM, no accessibility tree, no control
handle — so a worker locates a control by matching a reference image against a screenshot.
For *static* chrome you can ship a PNG captured once. For **text that is only known at
runtime** — a patient name, a row value, a dropdown entry the task payload supplies — you
cannot ship a PNG in advance.

UI Synthesizer closes that gap: given the text and the font/colour characteristics of the
control, it **generates the needle** at request time.

`ImageGenerateEngine.cs:49` and `:90` both carry the same comment on the same line:

> `//Set the rendering hint to ClearType ----- The most important line in the code`

That is not a stylistic preference. The remote Windows session renders text with **ClearType
sub-pixel antialiasing**, which produces coloured fringing on glyph edges. A needle rendered
with grayscale antialiasing has different pixels and will not match. Reproducing the server's
exact rasterisation is the entire trick; `TextRenderingHint.ClearTypeGridFit` is what does it.

Two consequences worth stating:

- **GDI+ and `System.Drawing` are load-bearing.** This is why the service is .NET Framework 4.8
  on Windows and not a cross-platform port. ClearType is a Windows text renderer.
- Matching is therefore sensitive to anything that changes server-side rasterisation: a font
  substitution, a DPI change, a theme change, a Windows version bump on the VDA.

## Request shape

Six parameters, and they are the whole contract:

| Parameter | Constraint | Source |
|---|---|---|
| `ControlText` | 1–200 chars | `RequestSchema.py:7`, `UISynthesizerController.cs:228` |
| `FontWeight` | `normal` \| `bold` | `Enums/FontWeights.cs` |
| `FontSize` | 1–100 | `RequestSchema.py:9`, `:231-234` |
| `FontAlignment` | `left` \| `center` \| `right` | `Enums/FontAlignments.cs` |
| `BackgroundColor` | HTML colour (name or `#rrggbb`) | `ColorTranslator.FromHtml`, `ImageGenerateEngine.cs:78-79` |
| `TextColor` | HTML colour | same |

The output bitmap is **auto-sized**: `Graphics.MeasureString` is called first and the canvas is
created at `width+1 × height+1` (`ImageGenerateEngine.cs:93,102`). Callers do not specify
dimensions.

### Escape vocabulary in `ControlText`

Three substitutions, applied before rendering (`ImageGenerateEngine.cs:16-18,42-45`):

| Token | Becomes |
|---|---|
| `$br$` | a newline (`\r\n`) |
| `$ul$` | `&` — the Windows mnemonic prefix, so the *next* character is rendered underlined |
| `&` | `&&` — a literal ampersand |

`StringFormat.HotkeyPrefix = HotkeyPrefix.Show` (`:39`) is what makes `$ul$` underline rather
than disappear. This matters because Windows menu and button labels routinely carry an
underlined accelerator character, and a needle without the underline will not match one.

## Two control modes

- **Freestyle** — the caller supplies all six parameters. `FreeStyleControl.cs`, font hardcoded
  to `Microsoft Sans Serif` (`:9`).
- **Pre-defined** — the caller supplies text plus a `ControlName`, and the style comes from
  `Data/Elements.yml`. Sixteen names in `Enums/ControlName.cs`: `DropdownButton{Enabled,
  Disabled}`, `ImageButton{Enabled,Disabled}`, `TabHeader{Enabled,Disabled}`, `RadioButton`,
  `DropDown`, `CheckBox`, `GridCell`, `BlueButton`, `BlackButton`, `DisabledButton`,
  `Textbox{Enabled,Disabled}`, `FreeStyle`.

  `Elements.yml` is the style table — the enabled/disabled pairs differ only in `FontColor`
  (`Black` vs `Gray`). Adding a control kind is a YAML edit plus an enum value, not code.

## Public surface

**.NET library** (`HCHB.UISynthesizer.cs`, static class `UiSynthesizer`):
`GenerateImageByText(text, weight, size, alignment, bg, fg)` · `GenerateImageByText(text,
ControlName)` · `GenerateImage(controlSpecJson)` · `GenerateImage(ControlSpec)` ·
`GetControlNames()`.

**HTTP API** (`UISynthesizerController.cs`, routed as `/[action]`):

| Endpoint | Returns |
|---|---|
| `GET /GenerateImage?controlText=…&fontWeight=…&…` | `image/png` bytes |
| `POST /GenerateImageBase64` (JSON body) | `{"ImageContent": "data:image/png;base64,…"}` |
| `GET /buildcache/DownloadImageCache` | the whole image cache as one zip, served from S3 |

Validation is strict and returns `400` with a `ModelState` dictionary: missing fields, bad
colour, out-of-range size, and — unusually — **duplicate query-string keys**
(`HasDuplicateKeyInGernerateParams`, `:279`).

**Python SDK** — `hchb_uisynthesizer_client`, the package an fb-worker actually consumes.

## The cache chain — four tiers, and it is the interesting part

Rendering is expensive and the same needle is wanted thousands of times, so almost nothing
re-renders. Every tier keys on the **same identity**: the MD5 of the canonical JSON of the six
parameters, suffixed `.png`.

Both ends compute it independently and must agree byte-for-byte —
`helper.py:17-23` (Python, hand-built string) and `CommonUtility.cs:74-83` (C#,
`JsonSerializer.Serialize`). **Two implementations of one hash, with no shared fixture
pinning them together.** If field order or casing drifts on either side, the client silently
stops finding cached images and every lookup becomes a round trip.

```
1. worker process memory   CacheObject._images, a process-global dict      client.py:17-37
2. S3 zip, pulled on a timer   APScheduler, every CACHE_REFRESH_TIME min   client.py:55-62
3. API memory cache        IMemoryCache, AbsoluteExpiration = MaxValue     CommonUtility.cs:118-124
4. render                  GDI+, then saved to disk as <md5>.png           CommonUtility.cs:112-160
```

Tier 4 is also the *producer* of tier 2. A Windows service (`FileSyncer/FileUploader`) runs a
`FileSystemWatcher` on the API's output directory (`Worker.cs:59-68`) and uploads every new
PNG to S3. A separate containerised batch job (`uisynth-batch/uisynth-batch.py`, `python:3.9`
+ `boto3`) then folds the loose objects into `UISynthesizerImagesAll.zip` — the artefact
clients download — coordinating through an index file with per-folder statuses
`toBeZipped → InProgress → Zipped | Failed` and an **ETag compare-and-set** retry loop
(`uisynth-batch.py:51-74`) because there is no lock.

So an image generated by one worker becomes available to every other worker — but only after
it has travelled disk → S3 → zip → the next scheduled client refresh. **The cache is
eventually consistent on the order of the refresh interval, not immediately.**

`@functools.cache` on `ImageGenerator.get_image` (`client.py:64`) adds a fifth, unbounded
memo in front of all of it. It is never cleared, so a long-lived worker's memory grows with
the number of distinct needles it has ever asked for.

## How an fb-worker reaches it

```
app locator spec            "$ControlText|bold|10|CENTER|#444444|white"
  ↓ split on "|", substitute $ControlText          (app-side convention)
Get Image From Synthesizer  6 positional args      rf-fb-worker-core/api/keywords.py:1131-1150
  ↓
get_image()                                        integrations/synthesizer/UiSynthesizer.py:18
  ↓
ImageGenerator.get_image()                         hchbuisynthesizerclient
  ↓ md5 → cache hit? → else HTTP
GET ${HCHBUISYNTH_URL}/GenerateImage
  ↓
PNG bytes → written to a temp file → RPA.Desktop template match
```

The pipe-delimited six-field spec is an **app-side convention**, not part of this tool's
contract; the platform contract is the six positional parameters.

**Config keys:** `HCHBUISYNTH_URL` (SDK, `config.py:15` — the only one the SDK reads),
`CACHE_REFRESH_TIME` and `START_SYNC_JOB` (read by the core at
`integrations/synthesizer/UiSynthesizer.py:12-13`), `BUCKET_NAME`, `INDEX_FILE`, `S3_ZIP`,
`RETRY_COUNT` (batch job). Values are never recorded here.

**Failure modes:**

- **`HCHBUISYNTH_URL` unset raises at first call, not at startup.** `ConfigParams` reads the
  env var lazily and `ConfigNotSetError` surfaces from inside `get_image`. A misconfigured
  worker therefore starts, polls, picks up a task, and fails mid-task.
- **The scheduler is started in `__init__`, the module instantiates `ImageGenerator` at import
  time** (`rf-fb-worker-core/integrations/synthesizer/UiSynthesizer.py:15`). The background
  thread exists for the life of the process whether or not any image is ever requested, and
  `START_SYNC_JOB` defaults to `False` via `os.getenv(..., False)` — which returns the
  **string** `"False"` when the variable is set to that, and `"False"` is truthy. Setting
  `START_SYNC_JOB=False` turns the sync job *on*.
- **`CACHE_REFRESH_TIME` is read as a string and passed straight to APScheduler**
  (`os.getenv("CACHE_REFRESH_TIME", 1)` → `scheduler.add_job(..., minutes=cache_refresh_time)`).
  The unset default is the int `1`; any value you actually configure is a `str`. APScheduler's
  `IntervalTrigger` builds a `timedelta` from `minutes=`, and `timedelta(minutes='5')` raises
  `TypeError: unsupported type for timedelta minutes component: str` — verified directly.
  So **the knob is only safe when left unset**, and only bites when `START_SYNC_JOB` is also
  set (the scheduler is created only on that branch). Not executed end to end here —
  APScheduler is not installed in this sandbox — but no `int()` conversion exists on either
  side of the call.
- **`refresh_cache` never evicts.** It only adds keys absent from the dict
  (`client.py:34-36`), so a stale entry for a changed needle is never replaced.
- **The zip is downloaded whole, every refresh**, and held in memory while it is unpacked
  (`helper.py:33-40`). Cost grows with the total number of needles ever generated, across all
  workers, forever.
- The disk save in `SaveImageAsync` is `async void` in effect — called without `await`
  (`CommonUtility.cs:114`), so an upload failure is unobserved.

**Known divergences:**
uisynthesizer-sdk-2-cannot-run-in-an-fb-worker (archived)

**Related:** [rf-fb-base-image](rf-fb-base-image.md) ·
[fb-worker-app-contract](fb-worker-app-contract.md) ·
[rf-fb-worker-core](rf-fb-worker-core.md)

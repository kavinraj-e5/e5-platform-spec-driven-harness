---
type: runbook
name: Debug a Citrix fb-worker — clipboard, image matching, stuck sessions
applies_to: [rf-fb-worker-core, rf-fb-base-image, ui-synthesizer]
triggers: ["clipboard empty", "extracted value blank", "Ctrl+C returns nothing", "image not found", "template match failing", "worker stuck after login", "citrix session wedged", "synthesizer error"]
provenance: code
verified: 2026-10-10
---

# Debug a Citrix fb-worker

Platform-level only. App locators, screens and business error codes are the app team's;
everything here is in the base image, `rf-fb-worker-core`, or UI Synthesizer.

## First: which surface is failing?

A Citrix worker has two interaction models and they fail differently.

| Symptom | Surface | Go to |
|---|---|---|
| never reaches a logged-in state; browser errors | Playwright / StoreFront | §1 |
| `IMAGE_NOT_FOUND`, template match misses | pixel matching / synthesizer | §2 |
| a value comes back empty or stale | clipboard channel | §3 |
| works once then fails for the rest of the session | session state | §4 |

## 1. Login leg

Login is **Playwright Chromium with `headless=False`** against Citrix StoreFront. Non-headless
is mandatory — the ICA launch must land on the X display.

- Is `DISPLAY=:99` set and is Xvfb actually up? `ls -l /tmp/.X11-unix/X99`. A **stale socket
  with no Xvfb behind it** is the classic trap: entrypoints guard on the socket's existence,
  see it, and proceed into a display that is not there. Clear with
  `rm -f /tmp/.X99-lock /tmp/.X11-unix/X99` and restart.
- Is a window manager running? Without `icewm` there are no window titles, and `wmctrl`,
  focus and Alt+Tab all stop working — which looks like every subsequent step failing, not
  like a missing WM.
- Attach and watch: `x0vncserver` is in the base image on `:99`.

## 2. Image matching

Two locator kinds, two different failure causes.

**Static PNG needles.** These degrade when the *server's* rendering changes — a VDA font, DPI,
theme or Windows version change. The tell is many unrelated locators failing at once, across
apps, starting at a specific time. Nothing on the client side will fix it.

**Synthesized needles** (text rendered at request time by
[ui-synthesizer](../app-development/ui-synthesizer/ui-synthesizer.md)). Check in this order:

1. **Is `HCHBUISYNTH_URL` set?** It is read lazily, so an unset value does not fail at startup
   — it raises `ConfigNotSetError` from inside the first `get_image` call, mid-task.
2. **Is the request valid?** The service returns `400` with a `ModelState` dictionary. Text is
   capped at 200 chars, font size 1–100, weight ∈ {normal, bold}, alignment ∈ {left, center,
   right}, and both colours must parse as HTML colours. It also rejects **duplicate
   query-string keys**.
3. **Is the needle right but the match still failing?** The service renders with
   `TextRenderingHint.ClearTypeGridFit` to reproduce the server's sub-pixel antialiasing. If
   the remote session is *not* rendering with ClearType — a policy or theme change on the VDA —
   the generated needle is correct and still will not match.
4. **Underlines and newlines.** `$ul$` underlines the next character, `$br$` is a newline, `&`
   is a literal ampersand. A label with an accelerator underline needs `$ul$` or it will not
   match.
5. **A stale cached needle.** `refresh_cache` only *adds* keys it does not already have — it
   never replaces. A wrong image cached under a given hash stays wrong for the life of the
   process.

Do not set `CACHE_REFRESH_TIME` unless you have checked it: it is read as a string and passed
to APScheduler's `minutes=`, and `timedelta(minutes='5')` raises `TypeError`.

## 3. Clipboard

The chain is seven hops:

```
remote app → Windows clipboard on the VDA → Citrix clipboard channel → wfica
           → X11 CLIPBOARD on :99 → xclip → RPA.Desktop → the keyword
```

**Run the four-way test before anything else.** It is two minutes and it is the only thing that
separates "this read failed" from "this session is dead":

| Test | Exercises | Healthy |
|---|---|---|
| A — copy and paste *inside* the remote app | the VDA's own clipboard | pass |
| B — copy in the remote app, read on the client | ICA channel, VDA → client | pass |
| C — set a value on the client, paste into the remote app | ICA channel, client → VDA | pass |
| D — copy between two client-side apps | the client's X11 clipboard | pass |

**A and D pass, B and C fail** → the channel is dead for the session. Go to §4; nothing in §3
will help.

Otherwise, check the local causes:

- **Did the caller clear the clipboard first?** Without a clear, a failed copy returns the
  *previous* value — which is worse than empty, because it looks valid.
- **Is the Alt+F8/Esc nudge still in the path?** `e5_get_clipboard_value` presses it on Linux
  before reading. It is not a focus workaround — it is what makes Citrix take ownership of the
  X `CLIPBOARD` selection. Code that bypasses the library helper and calls
  `RPA.Desktop.Get Clipboard Value` directly skips it.
- **Probe ownership directly:**
  ```bash
  DISPLAY=:99 xclip -selection clipboard -t TARGETS -o
  ```
  ~12 targets including `_ISL_UNICODE` → `wfica` owns the selection, Citrix is serving. Two
  targets (`TARGETS`, `UTF8_STRING`) → the local stub owns it. Error → no owner.

  `_ISL_UNICODE` present is a reliable **positive**. Its absence is **not** proof of a broken
  channel — a genuinely empty source field looks identical. See
  clipboard-read-failure-is-silent (archived).

## 4. A wedged session

If B and C fail while A and D pass, the session is unrecoverable. **Do not restart the pod and
do not retry** — both reattach you to the same session.

- Same credential again → recovers nothing (0 of 10 observed outages).
- Different credential → recovers (6 of 8), because there is no disconnected session under it.
- Explicit Citrix **Log Off**, then a fresh login → recovers (12 of 12 reads).

**Log off properly.** `wmctrl -i -c` and `pkill wfica` both *disconnect*; the session survives
and you land straight back on it. The only path that ends it is Citrix Connection Center
(`/opt/Citrix/ICAClient/util/conncenter`) → select the session → **Log Off**, which sits
directly next to **Disconnect**.

Why these accumulate in the first place:
citrix-sessions-are-disconnected-never-logged-off (archived).

## 5. Things that look like fixes and are not

- **Lengthening sleeps.** Channel failures last the whole session; they are not marginal timing
  misses. Longer sleeps cost throughput on every read in the fleet and recover nothing.
- **Rebuilding the base image to a newer Citrix client.** The failure reproduces across
  unrelated client implementations, which places it on the VDA side.
- **`EnableClipboardV2=0`.** A Windows-endpoint registry workaround. There is no Linux
  equivalent — the base image's entire clipboard surface is `ClipboardAllowed=*` in
  `All_Regions.ini`.

## 6. Before closing

Check whether the run **reported success with empty fields**. A dead channel returns `""`, and
unless the app guards against it that empty value is already downstream in the task output.

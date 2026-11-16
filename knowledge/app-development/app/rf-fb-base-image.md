---
type: tooling
name: RF FB Worker Base Image
repo: rf-fb-base-image
repo_path: "NO LOCAL CHECKOUT - mirror only"
branch: fedora-v8
commit: f3d32d3
language: dockerfile
build: docker
deployment: "e5-rf-fb-base:vN.0.0 in ECR; consumed as the FROM line of every fb-worker image"
owns_schema: false
provenance: code
verified: 2026-10-10
status: no-release-branch
---

# RF FB Worker Base Image

**The container the platform hands to app teams.** Everything an RPA worker needs to drive a
remote Windows application over Citrix, pre-installed and pre-configured. An app image is
`FROM e5-rf-fb-base:vN` plus that app's own source — nothing else.

One Dockerfile per Fedora base per revision, in `fedora/<fedora-ver>/<rev>/`. Branch naming is
`fedora-vN`; there are no semver release branches. **Latest is `fedora/v38/v8`, which is a
significant jump** — Fedora 38 and **Python 3.12.3**, where the long-running line was Fedora 36
and Python 3.8.16.

## What is in it, and why each piece is there

| Installed | Why an RPA worker needs it |
|---|---|
| **Citrix Workspace App** (`ICAClient-rhel-gcc-8-26.04.10.1-0.x86_64.rpm`) | the ICA client — `selfservice` to launch published apps, `wfica` to host the session |
| **Xvfb** (`xorg-x11-server-Xvfb`) | a virtual X display. `ENV DISPLAY=:99`. There is no physical screen. |
| **icewm** | a real window manager. Without one, windows have no titles and no focus model — and `wmctrl`, window titles and Alt+Tab are how the worker addresses windows. |
| **wmctrl**, **xprop** | enumerate, activate and close windows by title |
| **xclip**, **xsel** | read and write the X11 CLIPBOARD selection — the only way text crosses out of a Citrix session |
| **tigervnc-server** (`x0vncserver`) | attach to `:99` and watch a live run. The password is baked at build time from the `VNC_PASSWD` build arg into `~/.vnc/passwd`. |
| **tesseract** | OCR, for text no template match can reach |
| **chromium** + **chromedriver**, **npm/nodejs** | the browser leg — Citrix StoreFront login is browser automation before the desktop automation starts |
| **miniconda** (env `conda_robot_framework`) + **poetry** | the Python runtime apps build into |
| `/opt/recording/recorder` | a prebuilt Go binary (ELF) for session screen recording, added to `PATH` |

`ctx_rehash` runs at build time (`:33`) to install the Citrix certificate store, and
`chromium-browser` is symlinked aside to `/opt/robotframework/bin/chromium-browser` (`:34`) —
the Dockerfile labels that one `# FIXME: below is a workaround, as the path is ignored`.

## The Citrix configuration is the base image's, not the app's

Two ini files are baked in, each written to **both** the system config and root's profile
(`:83-94`) — so an app that never touches Citrix config still gets these:

```
/opt/Citrix/ICAClient/config/All_Regions.ini   and   /root/.ICAClient/All_Regions.ini
/opt/Citrix/ICAClient/config/wfclient.ini      and   /root/.ICAClient/wfclient.ini
```

**`All_Regions.ini` contains exactly one clipboard setting:**

```ini
[Virtual Channels\Clipboard]
ClipboardAllowed=*
```

That is the whole clipboard policy surface on this platform. `wfclient.ini` has **no clipboard
section at all**. There is no Linux equivalent of the Windows-side `EnableClipboardV2` registry
knob, so clipboard-redirection workarounds written for Windows endpoints do not transfer — see
clipboard-read-failure-is-silent (archived).

Settings in `wfclient.ini` that change automation behaviour and are easy to miss:

| Key | Effect |
|---|---|
| `KeyboardEventMode=Unicode` | synthetic keystrokes are sent as Unicode, not scancodes — this is why typing arbitrary text works |
| `MouseSendsControlV=True` | middle-click pastes |
| `DriveEnabledZ=True`, `DrivePathZ=/rf` | the container's `/rf` is mapped into the session as a drive — this is the file-transfer path out of Citrix |
| `AllowBidirectionalContentRedirection=False` | URL redirection off |
| `PersistentCacheEnabled=Off` | no bitmap disk cache |
| `IgnoreErrors=9,15,32` | three ICA error codes are suppressed rather than surfaced |
| `[Thinwire3.0] DesiredHRES=640 / DesiredVRES=480 / DesiredColor=15` | negotiated session geometry and colour depth — **below the `1920x1080x24` Xvfb screen** |

## Things to know before changing it

- **`VNC_PASSWD` is a build argument that is also persisted as `ENV`** (`:77`). It is therefore
  readable from the image's own metadata by anyone who can pull it. Rotating it means a rebuild.
- **`CMD tail -f /dev/null`.** The base image does nothing on its own; the app image supplies
  the real `ENTRYPOINT`.
- **Xvfb gets started by the app, not by the image.** The image installs it and sets `DISPLAY`;
  starting it is the app entrypoint's job. Apps commonly start it more than once and only the
  first takes effect — the later `-screen` geometry is silently ignored.
- **Python moved from 3.8.16 to 3.12.3 at `v8`** while `rf-fb-worker-core` still pins
  `python = '3.8.16'`. Anything depending on the interpreter version needs checking against the
  base revision an app actually builds on, not against the newest branch. See
  uisynthesizer-sdk-2-cannot-run-in-an-fb-worker (archived).
- Package installs are **unversioned** (`dnf install -y curl chromedriver chromium npm …`), so
  two builds of the same Dockerfile at different times are not the same image. Only the Citrix
  RPM and the miniconda installer are pinned. A locator corpus tuned against one build can
  degrade against a later one via a font or renderer change, with no version to point at.

**Related:** [ui-synthesizer](ui-synthesizer.md) ·
[fb-worker-app-contract](fb-worker-app-contract.md) ·
[rf-fb-worker-core](rf-fb-worker-core.md)

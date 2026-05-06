# Scanline — Project Plan
### Raspberry Pi Channel Manager

---

## Overview

Scanline is a Python-based headless channel manager for Raspberry Pi 3+. It boots straight to content and lets you flip through view-only "channels" — streams, webpages, Plex/media, and custom visualizers — using a keyboard. A retro TV Guide OSD handles navigation. A styled loading screen plays between channel switches. The system runs as a systemd service, boots without a desktop environment, and is configured via a single YAML file editable over SSH.

Scanline is a **passive display surface**, not an interactive system. Users can't type into webpages or interact with channel content; channels are for watching/looking only. All keyboard input is reserved for channel navigation and channel-specific OSD controls.

Project name: **Scanline** (deploy path: `/home/chives/scanline/`)

---

## System Architecture

```
┌────────────────────────────────────────────────────┐
│                    scanline.py                     │
│   ┌─────────────┐  ┌───────────────┐  ┌─────────┐  │
│   │ Input Layer │  │  OSD Engine   │  │ Channel │  │
│   │  (evdev +   │  │  (pygame      │  │ Registry│  │
│   │   pygame)   │  │   fullscreen) │  │  .yaml  │  │
│   └──────┬──────┘  └──────┬────────┘  └────┬────┘  │
│          └────────────────┘                │        │
│                    │                       │        │
│              ┌─────▼───────────────────────▼─────┐  │
│              │        Renderer Dispatcher         │  │
│              └──┬──────┬──────┬──────┬───────────┘  │
└─────────────────┼──────┼──────┼──────┼──────────────┘
                  │      │      │      │
           ┌──────┘  ┌───┘  ┌───┘  ┌───┘
           ▼         ▼      ▼      ▼
        MPV       Chromium  Topo   MPV
      (streams,   (webpages) (ASCII (Plex/
       media)               viz)   local)
```

All renderers run as subprocesses under a minimal X11 session (`xinit` → `openbox`). The OSD is a fullscreen, **opaque** pygame window — it covers the entire screen when visible (guide or loading screen) and is hidden when a channel is active. No compositor required. Input is handled via **evdev** at the kernel level, bypassing X11 focus entirely.

---

## Directory Structure

```
/home/chives/scanline/
├── scanline.py              # Main process — owns OSD + event loop
├── launch.sh                # X session bootstrap (called by systemd)
├── channels.yaml            # Channel config (edit over SSH)
├── state.json               # Persisted runtime state (last channel)
├── renderers/
│   ├── base_renderer.py     # Abstract base: spawn(), kill(), is_ready()
│   ├── mpv_renderer.py      # Streams, local media, Plex
│   ├── chromium_renderer.py # Webpages and dashboards
│   ├── script_renderer.py   # Arbitrary shell commands
│   └── topo_renderer.py     # ASCII topographic visualizer (pygame, fullscreen)
├── osd/
│   ├── guide.py             # TV Guide overlay (pygame, fullscreen)
│   ├── loading.py           # Loading/static screen (pygame, fullscreen)
│   └── fonts/               # Bundled retro fonts (Press Start 2P, VT323)
├── input/
│   └── evdev_reader.py      # Global keyboard capture via /dev/input/event*
├── topo/
│   ├── topo_noise.c         # C extension: noise, slots, atlas scatter
│   ├── build_noise.sh       # Compile script (run on Pi)
│   ├── topo_noise.so        # Built artifact — not committed
│   └── topo-ascii-spec-v1.0.0.json   # Canonical spec reference
├── assets/
│   └── static_noise.png     # Loading screen static FX
├── requirements.txt
├── install.sh               # One-shot setup
├── deploy.sh                # Dev-machine deploy helper
├── scanline.service         # systemd unit
└── CLAUDE.md
```

---

## Channel Config — `channels.yaml`

```yaml
channels:
  - name: "Aleph Monitor"
    number: 1
    type: webpage
    uri: "http://192.168.1.192:8081"

  - name: "ASCII Topography"
    number: 2
    type: topo
    palette: "PHOSPHOR"
    speed: "MED"

  - name: "Lo-Fi Radio"
    number: 3
    type: stream
    uri: "https://www.youtube.com/watch?v=jfKfPfyJRdk"

  - name: "Plex — Synthwave Mix"
    number: 4
    type: plex_playlist
    uri: "http://192.168.1.192:32400"
    playlist_id: "12345"
    token: "your-plex-token"

  - name: "Local Media"
    number: 5
    type: media
    path: "/mnt/media/ambient"
    loop: true

  - name: "WeatherStar 4000"
    number: 6
    type: webpage
    uri: "http://192.168.1.192:8082/?latLonQuery=43206&kiosk=true&settings-mediaPlaying-boolean=true"

settings:
  default_channel: 2
  resume_last_channel: true     # if true, resume last-watched on restart; falls back to default
  loading_timeout_s: 10
  audio_device: "alsa/sysdefault:CARD=vc4hdmi"   # HDMI audio default for Pi 3
```

### Channel Types

| Type | Renderer | Notes |
|---|---|---|
| `stream` | MPV + yt-dlp | Twitch, YouTube, HLS, any yt-dlp URL |
| `webpage` | Chromium `--kiosk` | Fullscreen browser, no chrome, no cursor |
| `topo` | topo_renderer.py | ASCII topographic visualizer (pygame) |
| `plex_playlist` | MPV | Plex HTTP API → temp `.m3u` → MPV |
| `media` | MPV | Local file, directory, or glob; optional loop |
| `script` | subprocess | Generic shell command |

---

## Display Stack

**X11 path** (required for Chromium webpage channels):

- X session launched via `xinit` from a systemd service running `launch.sh`
- Window manager: `openbox` in minimal mode (manages window Z-order, lets us raise the OSD on demand)
- No compositor — opaque OSD strategy avoids the need for picom/compton
- Content renderers: run fullscreen (`mpv --fs`, `chromium --kiosk`, `topo_renderer.py --fullscreen`)
- OSD layer: a single pygame fullscreen window managed by `scanline.py`, with three states:
  - **HIDDEN**: window is iconified/off-screen — renderer below is visible
  - **GUIDE**: window is fullscreen, opaque — covers screen, shows TV guide
  - **LOADING**: window is fullscreen, opaque — covers screen, shows static + channel info

When the OSD transitions GUIDE → HIDDEN, the underlying renderer becomes immediately visible (no compositing tricks).

### Input — evdev, not pygame focus

X11 focus is unreliable when MPV and Chromium grab the screen. Scanline reads keyboard events directly from `/dev/input/event*` via evdev — same pattern as topo-3B. This works regardless of which X window has focus.

- `input/evdev_reader.py` opens all keyboard event devices, filters for KEY_DOWN/KEY_HOLD events
- Pumps key events into `scanline.py`'s main loop alongside pygame events
- pygame events are still used for OSD-internal state (mouse, window events) but keyboard is evdev-only
- Service runs as root (or with appropriate input group permissions) to access `/dev/input/event*`

---

## Input Handling

All keyboard input flows through the evdev reader in `scanline.py`. Keys are mapped to actions; the active state (HIDDEN guide vs. open guide vs. topo channel) determines which actions are valid.

### Key Map

| Key | Action | Valid when |
|---|---|---|
| `Page Up` | Channel Up — opens guide on next channel | Always |
| `Page Down` | Channel Down — opens guide on prev channel | Always |
| `1`–`9` | Jump directly to channel by number | Always |
| `Up` / `Down` | Navigate guide list | Guide open |
| `Enter` | Select highlighted channel | Guide open |
| `Escape` | Dismiss guide without changing | Guide open |
| `N` / `B` | Topo: next / prev palette | Topo channel active |
| `C` | Topo: cycle char mode | Topo channel active |
| `S` | Topo: cycle speed | Topo channel active |
| `P` / `Space` | Topo: pause/play | Topo channel active |
| `Q` | Quit Scanline | Dev mode only |

Topo-specific keys are forwarded via FIFO (`/tmp/scanline-topo-ctl`) when the topo channel is active.

Note: arrow keys `Left` / `Right` are intentionally **not** mapped to channel change — reserved for any future renderer-internal use.

---

## OSD — TV Guide Overlay

A single pygame fullscreen window owned by `scanline.py`, opaque (no transparency), three states.

### GUIDE state

- Activated by `Page Up`, `Page Down`, or any number key
- Full-screen styled background (CRT scanline pattern as a baked PNG, not real transparency)
- Centered guide box: ~6 visible channel rows, current channel highlighted
- Left column: channel number badge (amber / phosphor green)
- Center: channel name in retro bitmap font (Press Start 2P or VT323, bundled in `osd/fonts/`)
- Right column: channel type icon
- Highlighted row: inverted colors with a subtle glow
- CRT-style border around the box
- **5-second inactivity timer** auto-selects highlighted channel; resets on any keypress

### LOADING state

- Fullscreen static/noise effect (pygame surface filled with random pixels each frame)
- Optional brief "channel switch" static flash before fading to the static-loop
- Large channel number centered
- Channel name below
- Blinking `[ LOADING... ]` text near the bottom
- Dismissed when the new renderer signals ready (see Process Lifecycle)

### HIDDEN state

- pygame window iconified or moved off-screen
- Renderer below is unobscured

State transitions are driven entirely by `scanline.py`'s event loop. No separate OSD process.

---

## ASCII Topographic Visualizer — `topo_renderer.py`

Reimplementation of topo-fb.py targeting a pygame X11 window rather than `/dev/fb0`. Same noise algorithm (warped fBm via C extension), same atlas system, same palettes and character modes — different output surface.

### Font and Grid

Target **≥30 FPS on Pi 3** with TV-distance legibility:

| Font | Grid (1920×1080) | Expected FPS |
|---|---|---|
| Terminus 12×24 | 160×45 cells | ~35–40 FPS |
| Terminus 10×20 | 192×54 cells | ~30–35 FPS |

Default: **Terminus 12×24** (160×45 grid). Half-res noise is only 80×22 = 1760 evaluations (vs 9600 in topo-3B), well within Pi 3 headroom.

### Performance Architecture

- C extension (`topo_noise.so`, ported from topo-3B) handles noise, slots, and atlas scatter
- numpy back buffer blitted to pygame surface via `pygame.surfarray.blit_array(screen, back_buf.T)` (note transpose: pygame is W×H, numpy is H×W)
- Noise + slots gated on drift advance; render + blit run every loop iteration
- Target: ≥30 FPS at MED speed on Pi 3

### PSF Font Loading

PSF font loader ported verbatim from topo-3B (`topo-fb.py` lines 44–135). Default font: `/usr/share/consolefonts/Uni2-Terminus24x12.psf.gz`. Used to build the glyph atlas; pygame draws pre-rendered pixel tiles, not live text.

### Readiness Signal

When the first frame is on screen, topo writes `READY\n` to stdout and flushes. `scanline.py` reads stdout in a non-blocking loop and dismisses the loading screen on `READY`. Falls back to a 5s timeout if no READY signal arrives.

### FIFO Control

`scanline.py` creates `/tmp/scanline-topo-ctl` **before** spawning topo and passes the path as a CLI arg (`--ctl-fifo /tmp/scanline-topo-ctl`). This avoids the race where the OSD writes before topo has opened the FIFO. Topo opens it with `O_RDWR | O_NONBLOCK` (same pattern as topo-fb.py) so it never gets EOF.

| OSD Key | FIFO Command | Effect |
|---|---|---|
| `N` | `n\n` | Next palette |
| `B` | `b\n` | Prev palette |
| `C` | `c\n` | Cycle char mode |
| `S` | `s\n` | Cycle speed |
| `P` / `Space` | `p\n` | Pause/play |

### Default Palettes

Ship all palettes from the spec: PHOSPHOR, SURVEY, INFRARED, BATHYMETRY, GHOST, FOSSIL, NEON NOIR, AURORA, OPERATOR, TOXIC, FLORAL SHOPPE, CREAMY SUNSET, PERIDOT, DUSTY PRAIRIE, SGB-2H (and remaining SGB variants).

---

## Process Lifecycle

On channel switch:

```
1.  evdev key triggers channel change
2.  OSD enters GUIDE state (if not already), or jumps to LOADING on number key
3.  User selects channel (Enter, or 5s timer fires)
4.  OSD enters LOADING state (fullscreen, opaque)
5.  Current renderer subprocess receives SIGTERM (to process group)
6.  Wait up to 3s for clean exit, then SIGKILL
7.  state.json updated with new channel number
8.  New renderer subprocess spawned with start_new_session=True
9.  Readiness poll begins:
      MPV        → ping MPV IPC socket
      Chromium   → check remote-debugging port (9222)
      Topo       → wait for READY\n on stdout
      Plex/media → MPV first segment playing
10. Readiness confirmed (or timeout) → OSD enters HIDDEN state
11. Renderer is live
```

**On renderer crash:** `scanline.py` polls subprocess `poll()` each loop iteration. Dead process → show "Signal Lost" screen for 2s → return to last good channel (or default).

**Process group kill:** all renderers spawned with `subprocess.Popen(..., start_new_session=True)` so SIGTERM hits the whole group (catches yt-dlp under MPV, Chromium child processes, etc.).

---

## Renderer Details

### MPV Renderer (`mpv_renderer.py`)

Spawn args (Pi 3 tuned):
```
mpv --fs --no-osc --really-quiet
    --hwdec=auto-copy
    --vo=gpu --gpu-context=x11
    --ao=alsa --audio-device=<from settings>
    --input-ipc-server=/tmp/mpv-ipc
    --stream-lavf-reconnect-streamed=yes
```

- Streams: pipe URL through `yt-dlp` to resolve best stream URL first
- Plex: fetch playlist via Plex HTTP API (`GET /playlists/<id>/items?X-Plex-Token=<token>`), build temp `.m3u`, pass to `--playlist`
- Readiness: ping MPV IPC socket, wait for `core-idle: false`
- `--hwdec=auto-copy` enables Pi 3's VideoCore IV hardware H.264 decoder (essential for 1080p)

### Chromium Renderer (`chromium_renderer.py`)

Spawn args:
```
chromium-browser --kiosk --noerrdialogs --disable-infobars --incognito
                 --autoplay-policy=no-user-gesture-required
                 --remote-debugging-port=9222
                 --disable-features=Translate
                 --no-first-run
                 <uri>
```

- Readiness: poll remote-debugging port (TCP 9222) until it responds
- Kill: SIGTERM to process group (Chromium spawns helper processes)
- `--autoplay-policy=no-user-gesture-required` is required for the WeatherStar channel's audio + auto-cycling animation to start cold without user interaction. Harmless for other webpage channels.
- Pi 3 caveat: heavy SPAs may be slow. Aleph dashboard at `:8081` and ws4kp (static + JS) are both lightweight — confirmed fine.

#### Webpage channel URL conventions

Some webpage channels need URL params to behave correctly in a non-interactive kiosk. Document them in `channels.yaml` directly so the channel config is self-explanatory:

| Channel | URL pattern |
|---|---|
| Aleph Monitor | `http://192.168.1.192:8081` (no params needed) |
| WeatherStar 4000 | `http://<host>:8082/?latLonQuery=<zip-or-place>&kiosk=true&settings-mediaPlaying-boolean=true` — `kiosk=true` hides UI chrome, `settings-mediaPlaying-boolean=true` auto-cycles forecast screens |

### Topo Renderer (via `script_renderer.py`)

- Spawns `python3 /home/chives/scanline/renderers/topo_renderer.py --fullscreen --ctl-fifo /tmp/scanline-topo-ctl`
- Readiness: read stdout, wait for `READY\n` (timeout 5s)
- Control: write to `/tmp/scanline-topo-ctl`
- Kill: SIGTERM

### Script Renderer (`script_renderer.py`)

- Spawns configured command in new process group
- Readiness: process alive after 500ms (configurable per channel)
- Kill: SIGTERM to process group

---

## Pi 3 Performance & Memory Budget

Pi 3B has 1GB RAM and 4× Cortex-A53 @ 1.2GHz. Tight but workable.

**RAM budget (rough estimates, idle):**
| Component | RAM |
|---|---|
| Raspberry Pi OS Lite (no desktop) | ~80 MB |
| X11 + openbox | ~40 MB |
| scanline.py + pygame OSD | ~60 MB |
| MPV (streaming) | ~150 MB |
| Chromium (lightweight HTML) | ~250 MB |
| Topo renderer | ~50 MB |
| **Headroom** | **~370 MB** |

**Mitigations:**
- Enable a 1GB swap file in `install.sh` (`dphys-swapfile`) — covers Chromium spikes
- Only one renderer is active at a time; previous renderer is fully killed before new one spawns
- Disable unnecessary services: bluetooth, avahi, triggerhappy

**CPU budget:** Topo at 30 FPS uses ~80% of one core (with C extension). MPV 1080p H.264 is ~30% one core with hwdec. Chromium can spike all 4 cores briefly on page load. Generally: only one heavy thing runs at a time, so we have headroom.

---

## Development Mode

`scanline.py --windowed` runs Scanline on the dev machine without a Pi:

- pygame OSD opens in a 1280×720 window instead of fullscreen
- evdev input is replaced with pygame keyboard events (degraded path for dev only)
- Renderers that work on the dev machine (MPV, topo if compiled, Chromium) run as normal
- Useful for iterating on OSD design and channel switching logic — most of phases 3 and 4 can be done here

---

## Boot & Deployment

### `launch.sh` (X session bootstrap)

```bash
#!/bin/bash
# Started by systemd via xinit. Runs in the X session.
openbox &
sleep 0.5
exec python3 /home/chives/scanline/scanline.py
```

### `scanline.service`

```ini
[Unit]
Description=Scanline Channel Manager
After=network.target

[Service]
User=chives
Type=simple
ExecStart=/usr/bin/xinit /home/chives/scanline/launch.sh -- :0 vt1 -nolisten tcp
Restart=on-failure
RestartSec=5

# evdev requires the user to be in the 'input' group
SupplementaryGroups=input video tty

[Install]
WantedBy=multi-user.target
```

`xinit` starts the X server, runs `launch.sh` as the X session client, and exits when the client exits — clean systemd lifecycle.

### `install.sh`

```bash
# System deps
sudo apt install -y python3-pip mpv chromium-browser openbox xorg xinit x11-utils \
                    gcc build-essential console-data \
                    dphys-swapfile

# 1GB swap (covers Chromium spikes on 1GB Pi 3)
sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
sudo systemctl restart dphys-swapfile

# Add pi to input group (evdev access)
sudo usermod -a -G input,video,tty chives

# Python deps
pip3 install -r requirements.txt

# yt-dlp
sudo curl -L https://yt-dlp.org/downloads/latest/yt-dlp -o /usr/local/bin/yt-dlp
sudo chmod +x /usr/local/bin/yt-dlp

# Build C extension on Pi
cd /home/chives/scanline/topo && bash build_noise.sh

# Disable unused services to free RAM
sudo systemctl disable bluetooth avahi-daemon triggerhappy

# Install and enable Scanline
sudo cp scanline.service /etc/systemd/system/
sudo systemctl enable scanline
sudo systemctl start scanline
```

### `deploy.sh` (modeled after topo-3B)

```
./deploy.sh           — copy Python files, restart service
./deploy.sh --c       — also copy + recompile C extension on Pi
./deploy.sh --service — also reinstall systemd service / launch.sh
./deploy.sh --full    — everything (use after reimage)
```

Deploy target: `chives@192.168.1.43`, path `/home/chives/scanline/`.

### SSH Management

| Task | Command |
|---|---|
| Edit channels | `ssh chives@192.168.1.43 nano /home/chives/scanline/channels.yaml` |
| Reload channels | `ssh chives@192.168.1.43 sudo systemctl restart scanline` |
| View logs | `ssh chives@192.168.1.43 journalctl -u scanline -f` |
| Stop Scanline | `ssh chives@192.168.1.43 sudo systemctl stop scanline` |
| Update yt-dlp | `ssh chives@192.168.1.43 sudo yt-dlp -U` |
| Rebuild C ext | `ssh chives@192.168.1.43 "cd /home/chives/scanline/topo && bash build_noise.sh"` |

---

## Dependencies

```
# System (apt)
python3, python3-pip
mpv
yt-dlp
chromium-browser
openbox
xorg, xinit, x11-utils
console-data           # provides PSF fonts in /usr/share/consolefonts
gcc, build-essential
dphys-swapfile

# Python (pip)
pygame>=2.5
pyyaml
requests
evdev                  # /dev/input/event* parser

# C extension
topo_noise.c → topo_noise.so (compiled on Pi: -O3 -march=native -lm -lpthread)

# Bundled in repo
osd/fonts/PressStart2P.ttf
osd/fonts/VT323.ttf
```

---

## Build Phases

### Phase 1 — Core Skeleton
- [ ] `channels.yaml` schema + loader
- [ ] `state.json` read/write helpers (last channel persistence)
- [ ] `base_renderer.py` abstract class (`spawn`, `kill`, `is_ready`)
- [ ] `scanline.py` main loop (no display yet; just lifecycle management)
- [ ] Dummy renderer for manual testing
- [ ] `--windowed` dev mode flag (sets up pygame for windowed dev iteration)

### Phase 2 — MPV Renderer + Media
- [ ] `mpv_renderer.py` — local media playback with Pi 3 hwdec flags
- [ ] MPV IPC socket readiness check
- [ ] Audio device wiring from settings
- [ ] Manual channel switch (CLI trigger, no OSD yet)
- [ ] Verify kill/respawn lifecycle (no orphan processes)

### Phase 3 — Input + OSD
- [ ] `evdev_reader.py` — global keyboard capture
- [ ] `loading.py` — fullscreen pygame static screen with channel info
- [ ] `guide.py` — fullscreen pygame guide with retro fonts
- [ ] OSD state machine (HIDDEN / GUIDE / LOADING) in scanline.py
- [ ] Number-key direct channel jump
- [ ] Wire OSD into channel switch lifecycle
- [ ] Test on dev machine in `--windowed` mode

### Phase 4a — Topo Visualizer Foundation
- [ ] `topo_noise.c` — port from topo-3B (4-thread noise, slots, atlas scatter)
- [ ] `build_noise.sh` — Pi compile script
- [ ] PSF font loader (port verbatim from topo-3B)
- [ ] `topo_renderer.py` — pygame fullscreen, single palette + char mode, atlas system
- [ ] `READY\n` stdout signal after first frame
- [ ] Verify ≥30 FPS at 12×24 font on Pi 3 (production-quality first-light)

### Phase 4b — Topo Polish
- [ ] All palettes from spec
- [ ] All char modes from spec (BRAILLE, SHADE, BLOCKS, STRATA, STIPPLE, RELIEF)
- [ ] FIFO control wired into OSD key forwarding
- [ ] Wire as `topo` channel type in channels.yaml

### Phase 5 — Chromium + Streams
- [ ] `chromium_renderer.py` — kiosk mode, remote-debugging readiness check, process group kill
- [ ] Wire Aleph dashboard (`http://192.168.1.192:8081`) as channel 1
- [ ] `mpv_renderer.py` — stream support via yt-dlp
- [ ] Plex playlist fetcher → MPV `.m3u` → playback

### Phase 6 — Polish & Hardening
- [ ] "Signal Lost" screen on renderer crash + auto-return to last good channel
- [ ] Auto-restart dead streams (configurable per channel)
- [ ] Pi 3 RAM/CPU validation under sustained channel switching
- [ ] `install.sh` and `deploy.sh` finalized + tested
- [ ] `CLAUDE.md` written
- [ ] End-to-end test: reimage Pi, run install.sh, verify all channels work

---

## Key Design Decisions

**Why X11 instead of direct framebuffer?**
Chromium requires X11. Webpage channels (Aleph dashboard) are a day-one requirement. MPV, the topo renderer (pygame), and the OSD all work fine in X11. A framebuffer-only path could be added later for Pi 4/5 if needed.

**Why opaque OSD instead of transparent overlay?**
True transparency on X11 requires a compositor (picom). On Pi 3 a compositor adds 10–15% baseline CPU load. Opaque fullscreen OSD that hides entirely when a channel is active gives us the same UX (guide pops up, channel resumes) without the cost. The aesthetic loss is the absence of "show channel content faintly behind the guide" — acceptable v1 tradeoff.

**Why evdev instead of pygame keyboard events?**
MPV `--fs` and Chromium `--kiosk` aggressively grab X11 focus. pygame can't reliably receive global keyboard events while another fullscreen X client has focus. evdev reads the kernel input layer directly, bypassing X focus entirely. Same pattern is already proven in topo-3B.

**Why a separate pygame subprocess for topo instead of in-process?**
Renderer isolation: if the visualizer crashes, the channel manager detects the dead process and shows the Signal Lost screen. Also keeps the OSD's pygame event loop clean — the OSD only processes OSD events, not topo animation frames.

**Why PSF fonts for the topo renderer instead of pygame TTF fonts?**
PSF bitmap fonts give pixel-perfect glyph rendering at exact integer sizes (12×24, 10×20) that map perfectly to the atlas tile system. TTF fonts at small sizes have antialiasing artifacts that break the per-cell palette coloring model.

**Font for the TV Guide OSD?**
Press Start 2P or VT323 (TTF, rendered by pygame), bundled in `osd/fonts/`. These are retro display fonts suited for UI, not for the dense character grid the topo renderer needs.

---

## Future Ideas

- **Web remote**: Flask endpoint on Pi, change channels from phone browser
- **Gamepad input**: extend evdev_reader to handle gamepad events
- **Scheduled channels**: time-based switching (e.g., news at 8am)
- **Transition effects**: brief static burst between channel switches
- **Pi 5 upgrade**: Wayland/wlr-overlay-layer instead of X11, true transparent OSD via wlroots layer-shell
- **VaporCity channel**: Three.js ambient scene as a dedicated channel
- **More topo channels**: different default fonts/palettes per channel instance
- **Optional compositor**: picom for users on Pi 4+ who want transparent overlays

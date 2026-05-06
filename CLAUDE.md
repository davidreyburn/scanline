# CLAUDE.md — Scanline

## What this is

Scanline is a Raspberry Pi 3 headless channel manager. It boots via systemd to a TV-connected Pi and lets you flip through "channels" (streams, webpages, ASCII visualizer, Plex, local media) using a keyboard. A retro TV Guide OSD handles navigation. Runs as a systemd service under a minimal X11 session.

**Full project plan:** `scanline-project-plan.md`

---

## Build status

| Phase | Contents | Status |
|-------|----------|--------|
| 1 — Core Skeleton | config loader, base renderer, dummy renderer, main loop | ✅ Done |
| 2 — MPV + Media | mpv_renderer.py, IPC readiness, hardware decode | ✅ Done |
| 3 — Input + OSD | evdev_reader.py, loading screen, TV guide overlay | ✅ Done |
| 4a — Topo Foundation | C extension, PSF font loader, pygame renderer | ✅ Done |
| 4b — Topo Polish | all palettes/modes, FIFO control | ✅ Done |
| 5 — Chromium + Streams | chromium_renderer.py, yt-dlp streams, Plex | ✅ Done |
| 6 — Hardening | crash recovery, Pi 3 tuning, install.sh, deploy.sh | ✅ Done |

---

## Deploy target

| Field | Value |
|-------|-------|
| Host | `scanline` — `192.168.1.43` |
| User | `chives` |
| Deploy path | `/home/chives/scanline/` |
| SSH | `chives@192.168.1.43` |

SSH key installed — no password prompt. No alias configured; use full host string or add to `~/.ssh/config`.

Reference deploy pattern: `C:\Users\David\agent\topo-3B\deploy.sh`

---

## Running locally (dev)

```bash
pip install -r requirements.txt
python3 scanline.py --windowed
```

`--windowed` opens a 1280×720 pygame window instead of fullscreen. On Windows, OSD is disabled (no pygame/evdev); the channel state machine still runs.

---

## Control FIFO (Phase 1+, Linux/Pi only)

```bash
echo "next"      | sudo tee /tmp/scanline-ctl   # next channel
echo "prev"      | sudo tee /tmp/scanline-ctl   # previous channel
echo "channel 3" | sudo tee /tmp/scanline-ctl   # jump to channel 3
echo "quit"      | sudo tee /tmp/scanline-ctl   # shut down
```

---

## Architecture

```
scanline.py           main loop — IDLE/SPAWNING/LIVE/CRASHED state machine
renderers/
  base_renderer.py    abstract base: spawn(), kill(), is_ready()
  dummy_renderer.py   Phase 1 test stub (sleep subprocess)
  mpv_renderer.py     Phase 2 — streams, media, Plex
  chromium_renderer.py  Phase 5 — webpages
  topo_renderer.py    Phase 4 — ASCII topographic visualizer (pygame)
input/
  evdev_reader.py     Phase 3 — global keyboard via /dev/input/event*
osd/
  guide.py            Phase 3 — TV guide overlay (pygame fullscreen)
  loading.py          Phase 3 — loading/static screen (pygame fullscreen)
topo/
  topo_noise.c        Phase 4 — C extension (noise, slots, atlas scatter)
  build_noise.sh      Phase 4 — compile on Pi
```

## Key design decisions

- **X11** (not framebuffer) — required for Chromium webpage channels
- **evdev** (not pygame keyboard) — global input bypasses X11 focus
- **Opaque fullscreen OSD** — shows/hides cleanly with no compositor needed
- **`start_new_session=True`** on all renderer subprocesses — clean process-group kill
- **Non-blocking main loop** — IDLE/SPAWNING/LIVE/CRASHED states; OSD phases map directly to these states (Phase 3)
- **Renderer factory in `make_renderer()`** — add `elif channel['type'] == X` for each new renderer type
- **Topo blit strategy** — `render_chars_32_cm` writes directly into `pygame.surfarray.pixels2d(screen)` (column-major, (W,H) layout) to avoid a transpose copy. Achieves ~9 FPS at 1920×1080; bottleneck is X11 `XShmPutImage` (~67ms/frame).

## Channels config

Edit `channels.yaml` over SSH. Fill in real values for:
- `channels[3].token` — Plex token
- `channels[3].playlist_id` — Plex playlist ID

Restart to reload: `sudo systemctl restart scanline`

## Things to watch on Pi 3

- Terminus 12×24 PSF font must be available: `apt install console-data`
- `topo_noise.so` must be compiled on the Pi: `cd topo/ && bash build_noise.sh`
- Pi user must be in `input` group for evdev: `sudo usermod -a -G input pi`
- Enable swap before running Chromium channels: `dphys-swapfile` in `install.sh`

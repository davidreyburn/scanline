#!/usr/bin/env python3
"""
Scanline — channel manager main loop.

Usage:
  python3 scanline.py [--windowed]

Control FIFO (from any SSH session):
  echo "next"      | sudo tee /tmp/scanline-ctl
  echo "prev"      | sudo tee /tmp/scanline-ctl
  echo "channel 3" | sudo tee /tmp/scanline-ctl
  echo "quit"      | sudo tee /tmp/scanline-ctl
"""
import argparse
import json
import os
import select
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Dict, Optional

import yaml

from renderers.base_renderer import BaseRenderer

# OSD + input are Linux-only (pygame/evdev not installed on Windows dev)
_HAVE_OSD = sys.platform != 'win32'

if _HAVE_OSD:
    import pygame
    from input.evdev_reader import EvdevReader
    from osd.loading import LoadingScreen
    from osd.guide import Guide

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE       = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, 'channels.yaml')
STATE_PATH  = os.path.join(_HERE, 'state.json')
CTL_FIFO    = '/tmp/scanline-ctl'
TOPO_FIFO   = '/tmp/scanline-topo-ctl'

POLL_INTERVAL  = 0.05   # seconds per main loop tick
CRASH_PAUSE    = 2.0    # seconds to show "signal lost" before respawning

# ---------------------------------------------------------------------------
# Renderer states
# ---------------------------------------------------------------------------
IDLE     = 'idle'
SPAWNING = 'spawning'
LIVE     = 'live'
CRASHED  = 'crashed'

# OSD states
OSD_OFF     = 'off'
OSD_GUIDE   = 'guide'
OSD_LOADING = 'loading'

# Topo control FIFO commands
_TOPO_CMD: Dict[str, str] = {
    'topo_n': 'n',
    'topo_b': 'b',
    'topo_c': 'c',
    'topo_s': 's',
    'topo_p': 'p',
}


# ---------------------------------------------------------------------------
# Config + state helpers
# ---------------------------------------------------------------------------

def load_config(path: str) -> Dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def load_state(path: str, default_channel: int) -> Dict[str, Any]:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'last_channel': default_channel}


def save_state(path: str, state: Dict[str, Any]) -> None:
    with open(path, 'w') as f:
        json.dump(state, f)


# ---------------------------------------------------------------------------
# Renderer factory
# ---------------------------------------------------------------------------

def make_renderer(channel: Dict[str, Any], settings: Dict[str, Any],
                  windowed: bool = False) -> BaseRenderer:
    ch_type = channel.get('type', 'script')

    if ch_type in ('media', 'stream', 'plex_playlist'):
        from renderers.mpv_renderer import MpvRenderer
        return MpvRenderer(channel, settings)

    if ch_type == 'topo' and sys.platform != 'win32':
        from renderers.topo_renderer import TopoRenderer
        return TopoRenderer(channel, settings, windowed=windowed)

    if ch_type == 'webpage' and sys.platform != 'win32':
        from renderers.chromium_renderer import ChromiumRenderer
        return ChromiumRenderer(channel, settings)

    from renderers.dummy_renderer import DummyRenderer
    if ch_type not in ('topo', 'webpage', 'script'):
        print(f'  [warn] unknown channel type {ch_type!r} — using DummyRenderer',
              flush=True)
    return DummyRenderer(channel, settings)


# ---------------------------------------------------------------------------
# Control FIFO helpers (POSIX only — skipped on Windows)
# ---------------------------------------------------------------------------

def setup_fifo(path: str) -> Optional[int]:
    if sys.platform == 'win32':
        return None
    if os.path.exists(path):
        if not stat.S_ISFIFO(os.stat(path).st_mode):
            os.remove(path)
            os.mkfifo(path, 0o666)
    else:
        os.mkfifo(path, 0o666)
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    print(f'Control FIFO: {path}', flush=True)
    return fd


def teardown_fifo(fd: Optional[int], path: str) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        os.remove(path)
    except OSError:
        pass


def read_fifo(fd: Optional[int]) -> Optional[str]:
    if fd is None:
        return None
    r, _, _ = select.select([fd], [], [], 0)
    if not r:
        return None
    try:
        data = os.read(fd, 256)
        if data:
            return data.decode('utf-8', errors='ignore').strip()
    except BlockingIOError:
        pass
    return None


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------

def resolve_channel_cmd(cmd: str, current: int,
                        channels: Dict[int, Any]) -> Optional[int]:
    """Parse a FIFO control command; return target channel number or None."""
    nums = sorted(channels.keys())
    if not nums:
        return None
    idx   = nums.index(current) if current in nums else 0
    parts = cmd.lower().split()
    if not parts:
        return None
    verb = parts[0]

    if verb == 'next':
        return nums[(idx + 1) % len(nums)]
    if verb == 'prev':
        return nums[(idx - 1) % len(nums)]
    if verb == 'channel' and len(parts) > 1:
        try:
            n = int(parts[1])
            return n if n in channels else None
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description='Scanline channel manager')
    parser.add_argument('--windowed', action='store_true',
                        help='Dev mode: windowed OSD instead of fullscreen')
    args    = parser.parse_args()
    windowed = args.windowed

    # --- Config ---------------------------------------------------------------
    config   = load_config(CONFIG_PATH)
    channels: Dict[int, Any] = {ch['number']: ch for ch in config['channels']}
    settings = config.get('settings', {})
    default_channel = settings.get('default_channel', min(channels))
    resume_last     = settings.get('resume_last_channel', True)
    loading_timeout = settings.get('loading_timeout_s', 10)

    state       = load_state(STATE_PATH, default_channel)
    current_num = state['last_channel'] if resume_last else default_channel
    if current_num not in channels:
        current_num = default_channel

    # --- Control FIFO ---------------------------------------------------------
    fifo_fd = setup_fifo(CTL_FIFO)

    # --- Signals --------------------------------------------------------------
    _quit = False

    def on_signal(sig: int, frame: Any) -> None:
        nonlocal _quit
        _quit = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT,  on_signal)

    # --- OSD / input ----------------------------------------------------------
    _screen: Optional[Any] = None
    _osd_state = OSD_OFF
    _guide:   Optional[Any] = None
    _loading: Optional[Any] = None
    _evdev:   Optional[Any] = None

    if _HAVE_OSD:
        # Disable SDL audio — scanline doesn't use it and the ALSA driver
        # produces constant underrun noise in the journal.
        os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
        pygame.init()
        if windowed:
            _screen = pygame.display.set_mode((1280, 720))
        else:
            _screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        pygame.display.set_caption('Scanline')
        _screen.fill((0, 0, 0))
        pygame.display.flip()
        _guide   = Guide(channels)
        _loading = LoadingScreen()
        _evdev   = EvdevReader()

    _x11_env = {**os.environ, 'DISPLAY': ':0'}

    # Find the OSD window's X11 ID via wmctrl.  pygame's get_wm_info()['window']
    # returns an SDL/Wayland surface ID — a different number to what wmctrl and
    # xdotool see.  wmctrl -l reliably lists WM-managed windows by title.
    _wm_id: int = 0
    if _HAVE_OSD and not windowed:
        try:
            lines = subprocess.run(
                ['wmctrl', '-l'], env=_x11_env,
                capture_output=True, timeout=2.0,
            ).stdout.decode().splitlines()
            # wmctrl -l format: "0xNNNN  desktop hostname  title"
            for _ln in lines:
                _parts = _ln.split(None, 3)
                if len(_parts) >= 4 and _parts[3].strip() == 'Scanline':
                    _wm_id = int(_parts[0], 16)
                    break
            if _wm_id:
                print(f'[osd] X11 window id: 0x{_wm_id:x}', flush=True)
            else:
                print('[osd] warning: Scanline window not found via wmctrl', flush=True)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass

    def _x11_run(*args: str) -> None:
        try:
            subprocess.run(list(args), env=_x11_env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass

    # WM_CLASS substrings for renderer types that run fullscreen X11 windows.
    # Used to find their X11 IDs via wmctrl -lx.
    _FULLSCREEN_WM_CLASS: Dict[str, str] = {
        'webpage':       'chromium',
        'stream':        'mpv',
        'media':         'mpv',
        'plex_playlist': 'mpv',
    }

    def _renderer_wm_ids() -> list:
        """Return X11 window IDs (hex strings) for the current renderer.

        Uses wmctrl -lx which reliably enumerates all WM-managed windows
        (xdotool search fails to find SDL2 and Chromium windows on this Pi).
        """
        ch_type = channels.get(current_num, {}).get('type', '')
        wm_class_prefix = _FULLSCREEN_WM_CLASS.get(ch_type, '')
        if not wm_class_prefix:
            return []
        try:
            lines = subprocess.run(
                ['wmctrl', '-lx'], env=_x11_env,
                capture_output=True, timeout=1.0,
            ).stdout.decode().splitlines()
            ids = []
            for line in lines:
                parts = line.split()
                # wmctrl -lx: 0xWID  DESK  WM_CLASS  [HOST]  TITLE
                if len(parts) < 3 or not parts[0].startswith('0x'):
                    continue
                if wm_class_prefix in parts[2].lower():
                    ids.append(parts[0])
            return ids
        except (OSError, subprocess.TimeoutExpired):
            return []

    def _raise_osd() -> None:
        if not (_wm_id and not windowed):
            return
        # Minimize the renderer window entirely so it vacates the screen
        # (xdotool windowminimize sends _NET_WM_STATE_HIDDEN).  Simply
        # stripping fullscreen and raising doesn't work — openbox keeps
        # fullscreen-layer windows above normal-layer windows unconditionally.
        # Minimizing removes the window from view while keeping the process
        # alive; the _NET_WM_STATE_FULLSCREEN hint is preserved so restoring
        # it later re-enters fullscreen automatically.
        for wid in _renderer_wm_ids():
            _x11_run('xdotool', 'windowminimize', wid)
        # wmctrl -i -a raises + focuses the OSD window.
        _x11_run('wmctrl', '-i', '-a', hex(_wm_id))

    def _lower_osd() -> None:
        if not (_wm_id and not windowed):
            return
        # Activate the renderer window — restores from minimized state and
        # returns focus.  openbox re-enters fullscreen automatically because
        # _NET_WM_STATE_FULLSCREEN was preserved while it was minimized.
        for wid in _renderer_wm_ids():
            _x11_run('wmctrl', '-i', '-a', wid)

    def _set_osd(new_state: str, pre_select: Optional[int] = None) -> None:
        nonlocal _osd_state
        if new_state == OSD_LOADING and _loading is not None:
            _loading.reset()
        if new_state == OSD_GUIDE and _guide is not None:
            _guide.open(pre_select)
        _osd_state = new_state
        if new_state == OSD_OFF:
            _lower_osd()
        else:
            _raise_osd()

    # --- Renderer state machine -----------------------------------------------
    renderer:           Optional[BaseRenderer] = None
    renderer_state:     str   = IDLE
    readiness_deadline: float = 0.0
    crash_time:         float = 0.0

    def _sorted_nums():
        return sorted(channels.keys())

    def _prev_channel() -> int:
        nums = _sorted_nums()
        idx  = nums.index(current_num) if current_num in nums else 0
        return nums[(idx - 1) % len(nums)]

    def _next_channel() -> int:
        nums = _sorted_nums()
        idx  = nums.index(current_num) if current_num in nums else 0
        return nums[(idx + 1) % len(nums)]

    def switch_to(num: int) -> None:
        nonlocal renderer, renderer_state, current_num
        if renderer is not None:
            renderer.kill()
            renderer = None
        current_num    = num
        renderer_state = IDLE

    def _write_topo(action: str) -> None:
        """Forward an action to the topo renderer's control FIFO (non-blocking)."""
        cmd = _TOPO_CMD.get(action)
        if cmd is None or sys.platform == 'win32':
            return
        try:
            fd = os.open(TOPO_FIFO, os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(fd, (cmd + '\n').encode())
            finally:
                os.close(fd)
        except OSError:
            pass

    def _handle_action(action: str) -> None:
        nonlocal _quit
        if action == 'quit':
            _quit = True

        elif action in ('ch_prev', 'ch_next'):
            if _osd_state == OSD_GUIDE and _guide is not None:
                _guide.navigate(-1 if action == 'ch_prev' else +1)
            else:
                target = _prev_channel() if action == 'ch_prev' else _next_channel()
                _set_osd(OSD_GUIDE, pre_select=target)

        elif action in ('nav_up', 'nav_down'):
            if _osd_state == OSD_GUIDE and _guide is not None:
                _guide.navigate(-1 if action == 'nav_up' else +1)

        elif action == 'select':
            if _osd_state == OSD_GUIDE and _guide is not None:
                switch_to(_guide.selected_channel)
                _set_osd(OSD_LOADING)

        elif action == 'dismiss':
            if _osd_state == OSD_GUIDE:
                _set_osd(OSD_OFF if renderer_state == LIVE else OSD_LOADING)

        elif action.startswith('ch_') and action[3:].isdigit():
            n = int(action[3:])
            if n in channels:
                switch_to(n)
                _set_osd(OSD_LOADING)

        elif action in _TOPO_CMD:
            if channels.get(current_num, {}).get('type') == 'topo':
                _write_topo(action)

    # --- Start ----------------------------------------------------------------
    print(f'Scanline starting — channel {current_num} '
          f'({channels[current_num]["name"]})', flush=True)
    if windowed:
        print('  [dev] --windowed mode', flush=True)

    # --- Main loop ------------------------------------------------------------
    try:
        while not _quit:
            now = time.monotonic()

            # -- Evdev input --------------------------------------------------
            if _HAVE_OSD and _evdev is not None:
                action = _evdev.poll()
                if action:
                    _handle_action(action)

            # -- Control FIFO -------------------------------------------------
            raw = read_fifo(fifo_fd)
            if raw:
                for line in raw.splitlines():
                    cmd = line.strip()
                    if not cmd:
                        continue
                    if cmd.lower() == 'quit':
                        _quit = True
                        break
                    target = resolve_channel_cmd(cmd, current_num, channels)
                    if target is not None:
                        print(f'[ctl] {cmd!r} → channel {target} '
                              f'({channels[target]["name"]})', flush=True)
                        switch_to(target)
                        _set_osd(OSD_LOADING)
                    else:
                        print(f'[ctl] unknown command: {cmd!r}', flush=True)

            # -- Renderer state machine ----------------------------------------
            if renderer_state == IDLE:
                ch = channels.get(current_num)
                if ch is None:
                    print(f'Channel {current_num} missing — falling back to default',
                          flush=True)
                    current_num = default_channel
                    ch = channels[current_num]
                renderer = make_renderer(ch, settings, windowed=windowed)
                renderer.spawn()
                state['last_channel'] = current_num
                save_state(STATE_PATH, state)
                readiness_deadline = now + loading_timeout
                renderer_state     = SPAWNING
                print(f'[ch {current_num}] spawning: {ch["name"]}', flush=True)
                # Show loading screen unless the user is actively browsing the guide
                if _osd_state != OSD_GUIDE:
                    _set_osd(OSD_LOADING)

            elif renderer_state == SPAWNING:
                if not renderer.is_alive():
                    print(f'[ch {current_num}] died before ready — signal lost',
                          flush=True)
                    renderer       = None
                    renderer_state = CRASHED
                    crash_time     = now
                elif renderer.is_ready():
                    elapsed = renderer.time_since_spawn()
                    print(f'[ch {current_num}] live ({elapsed:.2f}s)', flush=True)
                    renderer_state = LIVE
                    if _osd_state == OSD_LOADING:
                        _set_osd(OSD_OFF)
                elif now >= readiness_deadline:
                    print(f'[ch {current_num}] readiness timeout — going live anyway',
                          flush=True)
                    renderer_state = LIVE
                    if _osd_state == OSD_LOADING:
                        _set_osd(OSD_OFF)

            elif renderer_state == LIVE:
                if not renderer.is_alive():
                    print(f'[ch {current_num}] crashed — signal lost', flush=True)
                    renderer       = None
                    renderer_state = CRASHED
                    crash_time     = now
                    if _osd_state == OSD_OFF:
                        _set_osd(OSD_LOADING)

            elif renderer_state == CRASHED:
                if now - crash_time >= CRASH_PAUSE:
                    renderer_state = IDLE

            # -- OSD draw -----------------------------------------------------
            if _HAVE_OSD and _screen is not None:
                if _osd_state == OSD_GUIDE and _guide is not None:
                    _guide.draw(_screen)
                    if _guide.is_timed_out():
                        target = _guide.selected_channel
                        print(f'[guide] timeout → ch {target} '
                              f'({channels[target]["name"]})', flush=True)
                        switch_to(target)
                        _set_osd(OSD_LOADING)
                elif _osd_state == OSD_LOADING and _loading is not None:
                    ch_name = channels.get(current_num, {}).get('name', '?')
                    _loading.draw(_screen, current_num, ch_name)
                else:
                    _screen.fill((0, 0, 0))
                pygame.display.flip()
                pygame.event.pump()

            time.sleep(POLL_INTERVAL)

    finally:
        if renderer is not None:
            renderer.kill()
        if _HAVE_OSD:
            if _evdev is not None:
                _evdev.close()
            pygame.quit()
        teardown_fifo(fifo_fd, CTL_FIFO)
        print('Scanline stopped.', flush=True)


if __name__ == '__main__':
    main()

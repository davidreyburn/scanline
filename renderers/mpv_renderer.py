import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List

from renderers.base_renderer import BaseRenderer

IPC_PATH = '/tmp/mpv-ipc'


class MpvRenderer(BaseRenderer):
    """
    Renderer for channel types: media, stream, plex_playlist.

    Phase 2: local media only (path → MPV).
    Phase 5: adds stream (yt-dlp URL resolution) and plex_playlist support.

    Readiness strategy:
      Linux — poll MPV IPC socket; ready when core-idle is False (actively playing)
      Windows — timer fallback (3s), IPC not available
    """

    def _do_spawn(self) -> None:
        # Remove stale socket so MPV can create a fresh one.
        if sys.platform != 'win32' and os.path.exists(IPC_PATH):
            try:
                os.remove(IPC_PATH)
            except OSError:
                pass

        self.process = subprocess.Popen(
            self._build_args(),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _build_args(self) -> List[str]:
        ch       = self.channel
        ch_type  = ch.get('type', 'media')
        args: List[str] = [
            'mpv',
            '--fs',
            '--no-osc',
            '--really-quiet',
            f'--input-ipc-server={IPC_PATH}',
            '--stream-lavf-reconnect-streamed=yes',
        ]

        if sys.platform != 'win32':
            # Pi 3 / Linux: hardware decode + X11 GPU output
            args += [
                '--hwdec=auto-copy',
                '--vo=gpu',
                '--gpu-context=x11',
            ]
            audio_dev = self.settings.get('audio_device', '')
            if audio_dev:
                args += ['--ao=alsa', f'--audio-device={audio_dev}']

        if ch_type == 'media':
            if ch.get('loop', False):
                args.append('--loop-playlist=inf')
            args.append(ch['path'])

        # stream and plex_playlist handled in Phase 5
        return args

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        if sys.platform == 'win32':
            return self.time_since_spawn() >= 3.0
        if not os.path.exists(IPC_PATH):
            return False
        return self._ipc_is_playing()

    def _ipc_is_playing(self) -> bool:
        """
        Connect to MPV's IPC socket and ask if core-idle is False.
        core-idle is False when MPV is actively decoding/playing.
        Returns False on any error so the caller retries next tick.
        """
        import socket as _sock
        try:
            with _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(IPC_PATH)
                cmd = json.dumps({'command': ['get_property', 'core-idle']}) + '\n'
                s.sendall(cmd.encode())
                raw = s.recv(1024).decode(errors='ignore')
                # MPV may send event lines before the command response.
                # Responses have an 'error' field; events have an 'event' field.
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        if 'error' in msg:
                            return (msg['error'] == 'success'
                                    and msg.get('data') is False)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return False

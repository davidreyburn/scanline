import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import requests

from renderers.base_renderer import BaseRenderer

IPC_PATH = '/tmp/mpv-ipc'


class MpvRenderer(BaseRenderer):
    """
    Renderer for channel types: media, stream, plex_playlist.

    Readiness strategy:
      Linux — poll MPV IPC socket; ready when core-idle is False (playing)
      Windows — timer fallback (3s), IPC not available
    """

    def __init__(self, channel: Dict[str, Any], settings: Dict[str, Any]) -> None:
        super().__init__(channel, settings)
        self._tmp_m3u: Optional[str] = None  # temp file for plex_playlist

    def _do_spawn(self) -> None:
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

    def kill(self, timeout: float = 3.0) -> None:
        super().kill(timeout)
        if self._tmp_m3u:
            try:
                os.remove(self._tmp_m3u)
            except OSError:
                pass
            self._tmp_m3u = None

    def _build_args(self) -> List[str]:
        ch      = self.channel
        ch_type = ch.get('type', 'media')
        args: List[str] = [
            'mpv',
            '--fs',
            '--no-osc',
            '--really-quiet',
            f'--input-ipc-server={IPC_PATH}',
        ]

        if sys.platform != 'win32':
            args += ['--hwdec=auto-copy', '--vo=gpu', '--gpu-context=x11egl']
            audio_dev = self.settings.get('audio_device', '')
            if audio_dev:
                args += ['--ao=alsa', f'--audio-device={audio_dev}']

        if ch_type == 'media':
            if ch.get('loop', False):
                args.append('--loop-playlist=inf')
            args.append(ch['path'])

        elif ch_type == 'stream':
            # Live streams don't support merged formats; prefer pre-merged best first.
            args.append('--ytdl-format=best[height<=1080]/bestvideo[height<=1080]+bestaudio/best')
            args.append(ch['uri'])

        elif ch_type == 'plex_playlist':
            m3u = self._build_plex_m3u()
            if m3u:
                self._tmp_m3u = m3u
                args += [f'--playlist={m3u}', '--loop-playlist=inf']
            else:
                print(f'[mpv] plex_playlist: failed to build M3U — falling back to URI',
                      flush=True)
                args.append(ch.get('uri', ''))

        return args

    def _build_plex_m3u(self) -> Optional[str]:
        """Fetch Plex playlist items and write a temp .m3u. Returns path or None."""
        ch         = self.channel
        server     = ch.get('uri', '').rstrip('/')
        playlist_id = ch.get('playlist_id', '')
        token      = ch.get('token', '')

        if not (server and playlist_id and token):
            print('[mpv] plex_playlist: uri, playlist_id, and token are required',
                  flush=True)
            return None

        url = f'{server}/playlists/{playlist_id}/items?X-Plex-Token={token}'
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            print(f'[mpv] plex_playlist: API request failed: {e}', flush=True)
            return None

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            print(f'[mpv] plex_playlist: XML parse error: {e}', flush=True)
            return None

        lines = ['#EXTM3U']
        for video in root.iter('Video'):
            title = video.get('title', 'Unknown')
            for part in video.iter('Part'):
                key = part.get('key', '')
                if key:
                    stream_url = f'{server}{key}?X-Plex-Token={token}'
                    lines.append(f'#EXTINF:-1,{title}')
                    lines.append(stream_url)
                    break  # one Part per Video is enough

        if len(lines) == 1:
            print('[mpv] plex_playlist: no playable items found', flush=True)
            return None

        print(f'[mpv] plex_playlist: {len(lines) // 2} items', flush=True)
        fd, path = tempfile.mkstemp(suffix='.m3u', prefix='scanline-plex-')
        with os.fdopen(fd, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        return path

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        if sys.platform == 'win32':
            return self.time_since_spawn() >= 3.0
        # IPC socket appearing means mpv has initialised and is running.
        # For streams yt-dlp resolution adds ~2s before core-idle goes False,
        # so we declare ready on socket existence rather than waiting for playback.
        return os.path.exists(IPC_PATH)

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

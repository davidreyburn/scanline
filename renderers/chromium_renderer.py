import os
import shutil
import socket
import subprocess
import sys
from typing import List

from renderers.base_renderer import BaseRenderer

_DEBUG_PORT = 9222
_USER_DATA_DIR = '/tmp/scanline-chromium'
# Bookworm ships 'chromium'; older Pi OS / Ubuntu ship 'chromium-browser'
_CHROMIUM_BIN = next(
    (b for b in ('chromium', 'chromium-browser') if shutil.which(b)),
    'chromium',
)


class ChromiumRenderer(BaseRenderer):
    """
    Renderer for type='webpage' channels.

    Launches Chromium in kiosk mode. Readiness is detected by polling
    the remote-debugging port (9222) — Chromium opens it once the
    browser engine is initialised and the page has started loading.

    Kill: SIGTERM to the whole process group (Chromium spawns several
    helper processes; start_new_session=True + group kill cleans them up).
    """

    def _do_spawn(self) -> None:
        # Remove all Singleton* files before spawning.  Chromium's GPU and
        # zygote child processes may outlive the main process (different
        # process groups survive killpg) and still hold SingletonSocket open.
        # A new Chromium connects to that socket, gets a response, prints
        # "Opening in existing browser session." and immediately exits.
        # Deleting the socket file breaks that handshake.
        for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
            try:
                os.remove(os.path.join(_USER_DATA_DIR, name))
            except OSError:
                pass
        self.process = subprocess.Popen(
            self._build_args(),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _build_args(self) -> List[str]:
        uri = self.channel.get('uri', 'about:blank')
        args = [
            _CHROMIUM_BIN,
            '--kiosk',
            '--noerrdialogs',
            '--disable-infobars',
            '--incognito',
            '--autoplay-policy=no-user-gesture-required',
            f'--remote-debugging-port={_DEBUG_PORT}',
            f'--user-data-dir={_USER_DATA_DIR}',
            '--disable-dev-shm-usage',
            '--disable-features=Translate',
            '--no-first-run',
            '--disable-session-crashed-bubble',
        ]
        # Pass ALSA output device so Chromium uses HDMI rather than defaulting
        # to the headphone jack.  Strip the "alsa/" prefix that MPV uses.
        # Ignored on systems where Chromium uses PulseAudio/PipeWire instead.
        audio_dev = self.settings.get('audio_device', '')
        if audio_dev:
            alsa_dev = audio_dev.removeprefix('alsa/')
            args.append(f'--alsa-output-device={alsa_dev}')
        args.append(uri)
        return args

    def is_ready(self) -> bool:
        if sys.platform == 'win32':
            return self.time_since_spawn() >= 5.0
        try:
            with socket.create_connection(('127.0.0.1', _DEBUG_PORT), timeout=0.3):
                return True
        except OSError:
            return False

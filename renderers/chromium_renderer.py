import shutil
import socket
import subprocess
import sys
from typing import List

from renderers.base_renderer import BaseRenderer

_DEBUG_PORT = 9222
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
        self.process = subprocess.Popen(
            self._build_args(),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _build_args(self) -> List[str]:
        uri = self.channel.get('uri', 'about:blank')
        return [
            _CHROMIUM_BIN,
            '--kiosk',
            '--noerrdialogs',
            '--disable-infobars',
            '--incognito',
            '--autoplay-policy=no-user-gesture-required',
            f'--remote-debugging-port={_DEBUG_PORT}',
            '--disable-features=Translate',
            '--no-first-run',
            '--disable-session-crashed-bubble',
            uri,
        ]

    def is_ready(self) -> bool:
        if sys.platform == 'win32':
            return self.time_since_spawn() >= 5.0
        try:
            with socket.create_connection(('127.0.0.1', _DEBUG_PORT), timeout=0.3):
                return True
        except OSError:
            return False

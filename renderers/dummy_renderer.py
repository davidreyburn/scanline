import subprocess
import sys
from typing import Any, Dict

from renderers.base_renderer import BaseRenderer


class DummyRenderer(BaseRenderer):
    """
    No-op renderer for Phase 1 lifecycle testing.

    Spawns a Python sleep subprocess. Signals ready after READY_DELAY
    seconds via time_since_spawn() — no display required.

    Replace with real renderers in later phases by extending make_renderer()
    in scanline.py to dispatch on channel['type'].
    """

    READY_DELAY: float = 0.5   # seconds after spawn before is_ready() → True
    LIFETIME:    int   = 3600  # how long the dummy stays alive (1 hour)

    def _do_spawn(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, '-c',
             f'import time; time.sleep({self.LIFETIME})'],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def is_ready(self) -> bool:
        return self.time_since_spawn() >= self.READY_DELAY

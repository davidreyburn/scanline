import os
import signal
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseRenderer(ABC):
    """
    Abstract base for all Scanline renderer subprocesses.

    Subclass contract:
      - Override _do_spawn(): create self.process via subprocess.Popen(
            ..., start_new_session=True)
      - Override is_ready(): return True once content is visible on screen
    """

    def __init__(self, channel: Dict[str, Any], settings: Dict[str, Any]) -> None:
        self.channel  = channel
        self.settings = settings
        self.process: Optional[subprocess.Popen] = None
        self._spawn_time: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def spawn(self) -> None:
        self._spawn_time = time.monotonic()
        self._do_spawn()

    @abstractmethod
    def _do_spawn(self) -> None:
        """Create self.process. Must use start_new_session=True."""

    def kill(self, timeout: float = 3.0) -> None:
        """SIGTERM the process group, wait, then SIGKILL if needed."""
        if self.process is None:
            return
        if self.process.poll() is not None:
            self.process = None
            return

        if sys.platform == 'win32':
            # Windows has no process groups — terminate directly.
            try:
                self.process.terminate()
            except OSError:
                pass
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        else:
            try:
                pgid = os.getpgid(self.process.pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    self.process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass

        self.process = None

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @abstractmethod
    def is_ready(self) -> bool:
        """Return True once the renderer's content is visible on screen."""

    def time_since_spawn(self) -> float:
        return time.monotonic() - self._spawn_time

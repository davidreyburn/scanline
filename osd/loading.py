"""Loading screen — dark TV static with channel info overlay."""

import time
from typing import Optional, Tuple

import numpy as np
import pygame

AMBER     = (255, 160,  40)
GREEN     = ( 80, 255, 120)
NOISE_FPS = 8
BLINK_S   = 0.5


class LoadingScreen:
    """Fullscreen static noise with channel number and blinking LOADING text."""

    def __init__(self) -> None:
        self._font_big  = pygame.font.SysFont('monospace', 120, bold=True)
        self._font_med  = pygame.font.SysFont('monospace',  40, bold=True)
        self._start_t   = time.monotonic()
        self._noise_t   = 0.0
        self._noise_surf: Optional[pygame.Surface] = None
        self._scan_surf:  Optional[pygame.Surface] = None
        self._last_size:  Optional[Tuple[int, int]] = None

    def reset(self) -> None:
        """Call when the loading screen is shown for a new channel."""
        self._start_t  = time.monotonic()
        self._noise_surf = None

    def _rebuild(self, W: int, H: int) -> None:
        self._last_size = (W, H)
        # Pre-bake scanline overlay (horizontal dark lines every 4 rows)
        scan = pygame.Surface((W, H), pygame.SRCALPHA)
        for y in range(0, H, 4):
            pygame.draw.line(scan, (0, 0, 0, 55), (0, y), (W - 1, y))
        self._scan_surf  = scan
        self._noise_surf = None

    def _regen_noise(self, W: int, H: int) -> None:
        gray = np.random.randint(8, 55, (H, W), dtype=np.uint8)
        rgb  = np.stack([gray, gray, gray], axis=2)
        # pygame surfarray expects (W, H, 3) — transpose H×W
        self._noise_surf = pygame.surfarray.make_surface(rgb.transpose(1, 0, 2))
        self._noise_t    = time.monotonic()

    def draw(self, screen: pygame.Surface, ch_number: int, ch_name: str) -> None:
        W, H = screen.get_size()
        now  = time.monotonic()

        if self._last_size != (W, H):
            self._rebuild(W, H)

        if self._noise_surf is None or (now - self._noise_t) >= 1.0 / NOISE_FPS:
            self._regen_noise(W, H)

        screen.blit(self._noise_surf, (0, 0))
        screen.blit(self._scan_surf,  (0, 0))

        cx, cy = W // 2, H // 2

        # Dark center panel for text legibility
        pw, ph = min(720, W - 80), 290
        panel  = pygame.Surface((pw, ph), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 185))
        screen.blit(panel, (cx - pw // 2, cy - ph // 2))

        # Large amber channel number
        ch_surf = self._font_big.render(f'CH {ch_number}', True, AMBER)
        screen.blit(ch_surf, ch_surf.get_rect(center=(cx, cy - 38)))

        # Blinking "[ LOADING... ]" in green
        if int((now - self._start_t) / BLINK_S) % 2 == 0:
            ld_surf = self._font_med.render('[ LOADING... ]', True, GREEN)
            screen.blit(ld_surf, ld_surf.get_rect(center=(cx, cy + 82)))

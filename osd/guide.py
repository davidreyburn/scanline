"""Channel Guide OSD — amber retro TV guide style."""

import time
from typing import Any, Dict, Optional, Tuple

import pygame

AMBER      = (255, 160,  40)
DARK_BG    = (  8,   8,  12)
ROW_BG     = ( 16,  16,  24)
ROW_SEL    = ( 45,  35,   0)
TEXT_DIM   = (150, 130,  70)
TEXT_BRITE = (255, 220, 120)

GUIDE_TIMEOUT = 10.0  # seconds before auto-switching to selected channel

_TYPE_TAG = {
    'media':        '[MEDIA]',
    'stream':       '[STREAM]',
    'plex_playlist':'[PLEX]',
    'webpage':      '[WEB]',
    'topo':         '[TOPO]',
    'script':       '[SCRIPT]',
}


class Guide:
    """Fullscreen channel guide with countdown bar and keyboard navigation."""

    def __init__(self, channels: Dict[int, Any]) -> None:
        self._channels   = channels
        self._nums       = sorted(channels.keys())
        self._sel        = 0
        self._open_t     = time.monotonic()
        self._scan_surf: Optional[pygame.Surface]    = None
        self._last_size: Optional[Tuple[int, int]]   = None
        self._font_title = pygame.font.SysFont('monospace', 30, bold=True)
        self._font_row   = pygame.font.SysFont('monospace', 26)
        self._font_small = pygame.font.SysFont('monospace', 20)

    def open(self, pre_select: Optional[int] = None) -> None:
        """Show the guide, optionally pre-highlighting a specific channel."""
        self._open_t = time.monotonic()
        if pre_select is not None and pre_select in self._nums:
            self._sel = self._nums.index(pre_select)

    def navigate(self, delta: int) -> None:
        """Move the highlight by delta rows and reset the countdown."""
        self._sel    = (self._sel + delta) % len(self._nums)
        self._open_t = time.monotonic()

    @property
    def selected_channel(self) -> int:
        return self._nums[self._sel]

    def is_timed_out(self) -> bool:
        return time.monotonic() - self._open_t >= GUIDE_TIMEOUT

    def _build_scanlines(self, W: int, H: int) -> None:
        surf = pygame.Surface((W, H), pygame.SRCALPHA)
        for y in range(0, H, 4):
            pygame.draw.line(surf, (0, 0, 0, 40), (0, y), (W - 1, y))
        self._scan_surf = surf
        self._last_size = (W, H)

    def draw(self, screen: pygame.Surface) -> None:
        W, H = screen.get_size()
        now  = time.monotonic()

        if self._last_size != (W, H):
            self._build_scanlines(W, H)

        BDR = 4
        PAD = 20

        screen.fill(DARK_BG)
        screen.blit(self._scan_surf, (0, 0))
        pygame.draw.rect(screen, AMBER, (0, 0, W, H), BDR)

        # Title bar
        TH = 56
        pygame.draw.rect(screen, (18, 14, 0), (BDR, BDR, W - 2 * BDR, TH))
        title = self._font_title.render('SCANLINE', True, AMBER)
        screen.blit(title, (PAD + BDR,
                            BDR + (TH - title.get_height()) // 2))

        sel_ch   = self._channels[self.selected_channel]
        sel_text = f'Ch {self.selected_channel}  {sel_ch["name"]}'
        st = self._font_title.render(sel_text, True, TEXT_BRITE)
        screen.blit(st, (W - PAD - BDR - st.get_width(),
                         BDR + (TH - st.get_height()) // 2))

        # Channel rows
        row_top = BDR + TH + 6
        row_h   = 50
        visible = min(len(self._nums), (H - row_top - 64) // row_h)
        start   = max(0, min(self._sel - visible // 2,
                             len(self._nums) - visible))

        for i in range(visible):
            ci = start + i
            if ci >= len(self._nums):
                break
            num = self._nums[ci]
            ch  = self._channels[num]
            y   = row_top + i * row_h
            sel = ci == self._sel

            pygame.draw.rect(screen, ROW_SEL if sel else ROW_BG,
                             (BDR + 2, y, W - 2 * BDR - 4, row_h - 2))

            # Number badge
            BW = 54
            pygame.draw.rect(screen,
                             AMBER if sel else (55, 44, 10),
                             (BDR + PAD, y + 5, BW, row_h - 10))
            ns = self._font_row.render(str(num), True,
                                       DARK_BG if sel else AMBER)
            screen.blit(ns, ns.get_rect(center=(BDR + PAD + BW // 2,
                                                y + row_h // 2)))

            # Channel name
            nm = self._font_row.render(ch['name'], True,
                                       TEXT_BRITE if sel else TEXT_DIM)
            screen.blit(nm, (BDR + PAD + BW + 14,
                             y + (row_h - nm.get_height()) // 2))

            # Type tag
            tag = _TYPE_TAG.get(ch.get('type', ''),
                                f"[{ch.get('type', '?').upper()}]")
            ts  = self._font_small.render(tag, True,
                                          AMBER if sel else (90, 72, 20))
            screen.blit(ts, (W - BDR - PAD - ts.get_width(),
                             y + (row_h - ts.get_height()) // 2))

        # Countdown bar
        remaining = max(0.0, GUIDE_TIMEOUT - (now - self._open_t))
        bar_h = 7
        bar_y = H - BDR - bar_h - 30
        bar_x = BDR + PAD
        bar_w = W - 2 * BDR - 2 * PAD
        fill_w = int(bar_w * remaining / GUIDE_TIMEOUT)
        pygame.draw.rect(screen, (28, 22,  4), (bar_x, bar_y, bar_w, bar_h))
        if fill_w > 0:
            pygame.draw.rect(screen, AMBER, (bar_x, bar_y, fill_w, bar_h))

        hint = self._font_small.render(
            'ENTER: select   ESC: dismiss   left/right/up/down: navigate',
            True, (100, 88, 40))
        screen.blit(hint, (bar_x, bar_y + bar_h + 5))

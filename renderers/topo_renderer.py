#!/usr/bin/env python3
"""
topo_renderer.py — ASCII topographic visualizer renderer for Scanline.

Dual-mode:
  Imported as module  → TopoRenderer(BaseRenderer) for scanline.py
  Run as __main__     → fullscreen pygame rendering loop (spawned as subprocess)

Subprocess args:
  --windowed          1280x720 window instead of fullscreen
  --ctl-fifo PATH     FIFO to read control commands from
  --palette NAME      initial palette name (default: PHOSPHOR)
  --speed NAME        SLOW | MED | FAST | FROZEN (default: MED)
"""

import gzip
import math
import os
import random
import select as _sel
import signal
import stat
import struct
import sys
import time
import ctypes as _ct
from typing import Optional

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_NOISE_SO = os.path.join(_HERE, '..', 'topo', 'topo_noise.so')

# ---------------------------------------------------------------------------
# PSF font loader (ported verbatim from topo-3B)
# ---------------------------------------------------------------------------

PSF2_MAGIC = 0x864AB572
PSF1_MAGIC = 0x0436

_PSF_SEARCH = [
    '/usr/share/consolefonts/Uni2-Terminus24x12.psf.gz',
    '/usr/share/consolefonts/Uni2-Terminus20x10.psf.gz',
    '/usr/share/consolefonts/Uni2-Terminus16.psf.gz',
    '/usr/share/consolefonts/Uni2-Terminus14.psf.gz',
]


def _utf8_decode(b):
    try:
        return ord(b.decode('utf-8'))
    except Exception:
        return None


def load_psf(path):
    """Load PSF1/PSF2 font. Returns (u2g, glyphs, width, height).
    glyphs shape: (num_glyphs, height, width) bool."""
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rb') as f:
        data = f.read()

    magic2 = struct.unpack_from('<I', data, 0)[0]
    magic1 = struct.unpack_from('<H', data, 0)[0]

    if magic2 == PSF2_MAGIC:
        _, headersize, flags, numglyph, bytesperglyph, height, width = \
            struct.unpack_from('<IIIIIII', data, 4)
        has_unicode = bool(flags & 1)
        bytes_per_row = (width + 7) // 8
        glyph_data = data[headersize: headersize + numglyph * bytesperglyph]

        glyphs = np.zeros((numglyph, height, width), dtype=bool)
        for i in range(numglyph):
            gb = glyph_data[i * bytesperglyph: (i + 1) * bytesperglyph]
            for row in range(height):
                rb = gb[row * bytes_per_row: (row + 1) * bytes_per_row]
                for col in range(width):
                    if rb[col >> 3] & (0x80 >> (col & 7)):
                        glyphs[i, row, col] = True

        u2g = {}
        if has_unicode:
            pos = headersize + numglyph * bytesperglyph
            for glyph_idx in range(numglyph):
                buf = b''
                while pos < len(data):
                    b = data[pos]; pos += 1
                    if b == 0xFF:
                        break
                    if b == 0xFE:
                        if buf:
                            cp = _utf8_decode(buf)
                            if cp is not None:
                                u2g.setdefault(cp, glyph_idx)
                        buf = b''
                    else:
                        buf += bytes([b])
                if buf:
                    cp = _utf8_decode(buf)
                    if cp is not None:
                        u2g.setdefault(cp, glyph_idx)
        else:
            for i in range(min(numglyph, 256)):
                u2g[i] = i

        for cp in range(128):
            if cp not in u2g and cp < numglyph:
                u2g[cp] = cp

        return u2g, glyphs, width, height

    elif magic1 == PSF1_MAGIC:
        mode, charsize = struct.unpack_from('BB', data, 2)
        numglyph = 512 if (mode & 1) else 256
        width, height = 8, charsize
        glyph_data = data[4: 4 + numglyph * charsize]

        glyphs = np.zeros((numglyph, height, width), dtype=bool)
        for i in range(numglyph):
            gb = glyph_data[i * charsize: (i + 1) * charsize]
            for row in range(height):
                byte = gb[row] if row < len(gb) else 0
                for col in range(8):
                    if byte & (0x80 >> col):
                        glyphs[i, row, col] = True

        u2g = {i: i for i in range(numglyph)}
        return u2g, glyphs, width, height

    else:
        raise ValueError(f'Unknown PSF magic: {magic2:#010x}')


def _find_font():
    for p in _PSF_SEARCH:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        'No Terminus PSF font found. Install console-data: '
        'sudo apt install console-data')


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

PALETTES = [
    {
        'name': 'SURVEY',
        'bg': (6, 10, 7),
        'bands': [
            (0.12, (10,  42,  58)),  (0.22, (13,  61,  85)),
            (0.30, (200, 184, 112)), (0.42, (74,  122, 58)),
            (0.55, (58,  98,  48)),  (0.67, (107, 92,  66)),
            (0.79, (138, 122, 106)), (0.89, (184, 176, 164)),
            (1.01, (232, 228, 224)),
        ],
        'contour': (255, 255, 255),
    },
    {
        'name': 'INFRARED',
        'bg': (8, 5, 5),
        'bands': [
            (0.12, (13,  5,   32)),  (0.25, (26,  8,   64)),
            (0.38, (74,  8,   48)),  (0.50, (138, 16,  32)),
            (0.62, (204, 40,  0)),   (0.74, (238, 102, 0)),
            (0.85, (255, 170, 0)),   (0.93, (255, 221, 68)),
            (1.01, (255, 255, 204)),
        ],
        'contour': (255, 68, 0),
    },
    {
        'name': 'BATHYMETRY',
        'bg': (2, 8, 16),
        'bands': [
            (0.10, (0,   8,   32)),  (0.22, (0,   24,  64)),
            (0.35, (0,   48,  96)),  (0.47, (0,   72,  128)),
            (0.58, (0,   96,  160)), (0.69, (8,   128, 184)),
            (0.79, (32,  160, 204)), (0.89, (96,  200, 224)),
            (1.01, (176, 238, 255)),
        ],
        'contour': (0, 212, 255),
    },
    {
        'name': 'GHOST',
        'bg': (6, 6, 10),
        'bands': [
            (0.15, (8,   8,   18)),  (0.28, (16,  16,  42)),
            (0.40, (26,  26,  64)),  (0.52, (40,  40,  88)),
            (0.63, (56,  56,  112)), (0.73, (80,  72,  136)),
            (0.82, (104, 88,  160)), (0.91, (136, 120, 192)),
            (1.01, (192, 184, 232)),
        ],
        'contour': (153, 136, 255),
    },
    {
        'name': 'PHOSPHOR',
        'bg': (1, 10, 3),
        'bands': [
            (0.12, (1,   14,  4)),   (0.22, (3,   26,  7)),
            (0.32, (6,   46,  14)),  (0.43, (10,  74,  24)),
            (0.54, (15,  102, 34)),  (0.65, (23,  128, 48)),
            (0.75, (34,  160, 64)),  (0.86, (51,  196, 85)),
            (1.01, (85,  238, 119)),
        ],
        'contour': (57, 255, 106),
    },
    {
        'name': 'FOSSIL',
        'bg': (18, 12, 8),
        'bands': [
            (0.12, (30,  22,  15)),  (0.22, (52,  38,  24)),
            (0.32, (82,  62,  42)),  (0.43, (115, 88,  58)),
            (0.54, (148, 118, 82)),  (0.65, (178, 152, 118)),
            (0.75, (200, 178, 148)), (0.86, (220, 204, 178)),
            (1.01, (242, 232, 215)),
        ],
        'contour': (248, 240, 225),
    },
    {
        'name': 'SGB-2H',
        'bg': (0, 0, 0),
        'bands': [
            (0.10, (20,  20,  20)),  (0.22, (44,  44,  44)),
            (0.34, (72,  72,  72)),  (0.46, (100, 100, 100)),
            (0.56, (124, 124, 124)), (0.67, (152, 152, 152)),
            (0.77, (184, 184, 184)), (0.88, (216, 216, 216)),
            (1.01, (248, 248, 248)),
        ],
        'contour': (255, 255, 255),
    },
    {
        'name': 'SGB-1H',
        'bg': (48, 24, 0),
        'bands': [
            (0.10, (48,  24,  0)),   (0.22, (80,  42,  8)),
            (0.34, (116, 64,  16)),  (0.46, (158, 94,  30)),
            (0.56, (198, 128, 60)),  (0.67, (224, 160, 96)),
            (0.77, (240, 184, 136)), (0.88, (248, 216, 184)),
            (1.01, (252, 244, 228)),
        ],
        'contour': (255, 220, 170),
    },
    {
        'name': 'SGB-3H',
        'bg': (8, 24, 0),
        'bands': [
            (0.10, (8,   24,  0)),   (0.22, (18,  48,  4)),
            (0.34, (32,  76,  8)),   (0.46, (52,  108, 18)),
            (0.56, (72,  136, 30)),  (0.67, (100, 178, 48)),
            (0.77, (144, 210, 72)),  (0.88, (192, 238, 104)),
            (1.01, (224, 248, 160)),
        ],
        'contour': (200, 255, 120),
    },
    {
        'name': 'SGB-4H',
        'bg': (40, 50, 20),
        'bands': [
            (0.10, (40,  50,  20)),  (0.22, (58,  70,  28)),
            (0.34, (82,  94,  40)),  (0.46, (108, 118, 56)),
            (0.56, (132, 142, 68)),  (0.67, (160, 168, 84)),
            (0.77, (188, 196, 104)), (0.88, (218, 226, 152)),
            (1.01, (248, 248, 200)),
        ],
        'contour': (255, 255, 180),
    },
    {
        'name': 'PERIDOT',
        'bg': (8, 56, 54),
        'bands': [
            (0.10, (8,   56,  54)),  (0.22, (12,  80,  70)),
            (0.34, (20,  110, 78)),  (0.46, (36,  140, 88)),
            (0.56, (62,  172, 98)),  (0.67, (106, 208, 122)),
            (0.77, (162, 232, 108)), (0.88, (210, 248, 130)),
            (1.01, (251, 255, 163)),
        ],
        'contour': (190, 255, 90),
    },
    {
        'name': 'FLORAL SHOPPE',
        'bg': (35, 5, 44),
        'bands': [
            (0.10, (35,  5,   44)),  (0.20, (55,  10,  62)),
            (0.30, (78,  18,  76)),  (0.42, (95,  24,  84)),
            (0.52, (20,  140, 130)), (0.63, (26,  187, 156)),
            (0.74, (80,  210, 190)), (0.86, (178, 240, 226)),
            (1.01, (247, 247, 247)),
        ],
        'contour': (26, 210, 172),
    },
    {
        'name': 'CREAMY SUNSET',
        'bg': (52, 32, 62),
        'bands': [
            (0.10, (52,  32,  62)),  (0.22, (82,  52,  94)),
            (0.34, (112, 72,  122)), (0.46, (158, 80,  108)),
            (0.56, (192, 108, 130)), (0.67, (218, 132, 118)),
            (0.77, (240, 160, 132)), (0.88, (250, 192, 162)),
            (1.01, (255, 224, 200)),
        ],
        'contour': (255, 180, 120),
    },
    {
        'name': 'DUSTY PRAIRIE',
        'bg': (60, 36, 8),
        'bands': [
            (0.10, (60,  36,  8)),   (0.22, (92,  60,  16)),
            (0.34, (130, 90,  28)),  (0.46, (164, 124, 48)),
            (0.56, (202, 164, 62)),  (0.67, (236, 198, 70)),
            (0.77, (220, 220, 178)), (0.88, (196, 222, 244)),
            (1.01, (213, 238, 255)),
        ],
        'contour': (248, 204, 68),
    },
    {
        'name': 'NEON NOIR',
        'bg': (2, 4, 18),
        'bands': [
            (0.10, (4,   8,   28)),  (0.20, (8,   18,  62)),
            (0.30, (14,  44,  114)), (0.42, (20,  90,  188)),
            (0.53, (10,  168, 228)), (0.63, (80,  20,  162)),
            (0.73, (202, 22,  168)), (0.86, (255, 44,  188)),
            (1.01, (255, 144, 242)),
        ],
        'contour': (0, 224, 255),
    },
    {
        'name': 'TOXIC',
        'bg': (4, 8, 2),
        'bands': [
            (0.10, (8,   16,  4)),   (0.20, (16,  38,  6)),
            (0.30, (26,  76,  10)),  (0.42, (44,  126, 14)),
            (0.53, (68,  188, 20)),  (0.64, (122, 226, 24)),
            (0.74, (182, 246, 30)),  (0.86, (230, 255, 50)),
            (1.01, (255, 255, 130)),
        ],
        'contour': (180, 255, 20),
    },
    {
        'name': 'AURORA',
        'bg': (0, 4, 12),
        'bands': [
            (0.10, (0,   8,   20)),  (0.20, (0,   18,  38)),
            (0.30, (2,   42,  48)),  (0.42, (4,   88,  76)),
            (0.53, (8,   148, 92)),  (0.63, (62,  200, 120)),
            (0.73, (160, 60,  180)), (0.86, (220, 40,  160)),
            (1.01, (255, 120, 220)),
        ],
        'contour': (0, 255, 180),
    },
    {
        'name': 'OPERATOR',
        'bg': (10, 10, 15),
        'bands': [
            (0.12, (13,  13,  20)),  (0.22, (13,  51,  68)),
            (0.32, (15,  72,  90)),  (0.43, (30,  100, 60)),
            (0.53, (55,  134, 39)),  (0.63, (20,  175, 120)),
            (0.73, (0,   217, 255)), (0.86, (15,  255, 100)),
            (1.01, (30,  255, 0)),
        ],
        'contour': (30, 255, 0),
    },
]


# ---------------------------------------------------------------------------
# Character modes
# ---------------------------------------------------------------------------

def _strata_cfn(dh, dv): return '#' if dh and dv else ('=' if dh else '‖')
def _relief_cfn(dh, dv): return '+' if dh and dv else ('-' if dh else '¦')

CHAR_MODES = [
    {'name': 'BRAILLE',  'fill': [' ', '.', ':', 'o', 'O', '0', '#', '#'], 'cfn': None},
    {'name': 'SHADE',    'fill': [' ', ' ', '.', '.', '+', '+', '#', '@'], 'cfn': None},
    {'name': 'BLOCKS',   'fill': [' ', '·', '░', '░', '▒', '▒', '█', '█'], 'cfn': None},
    {'name': 'STRATA',   'fill': [' ', '.', '_', '-', '=', '≈', '≡', '█'], 'cfn': _strata_cfn},
    {'name': 'STIPPLE',  'fill': [' ', '∙', '·', '+', '×', '±', '÷', '%'], 'cfn': None},
    {'name': 'RELIEF',   'fill': [' ', '.', '°', 'o', 'O', '0', '@', '█'], 'cfn': _relief_cfn},
]

CONTOUR_LEVELS = 12

# ---------------------------------------------------------------------------
# Noise (scalar Python — for drift wander computation only)
# ---------------------------------------------------------------------------

_HMAX = 0x7FFFFFFF


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def _hash2(ix, iy):
    h = (ix * 1619 + iy * 31337) & 0x7FFFFFFF
    h = (((h >> 16) ^ h) * 0x45D9F3B) & 0xFFFFFFFF
    h = (((h >> 16) ^ h) * 0x45D9F3B) & 0xFFFFFFFF
    return ((h >> 16) ^ h) & 0x7FFFFFFF


def _value_noise(x, y):
    ix, iy = int(math.floor(x)), int(math.floor(y))
    fx = _fade(x - ix)
    fy = _fade(y - iy)
    v00 = _hash2(ix,     iy    ) / _HMAX
    v10 = _hash2(ix + 1, iy    ) / _HMAX
    v01 = _hash2(ix,     iy + 1) / _HMAX
    v11 = _hash2(ix + 1, iy + 1) / _HMAX
    a = v00 + fx * (v10 - v00)
    b = v01 + fx * (v11 - v01)
    return a + fy * (b - a)


# ---------------------------------------------------------------------------
# Numpy fallback noise (for elevation grid when C extension unavailable)
# ---------------------------------------------------------------------------

NX, NY = np.float32(0.012), np.float32(0.022)
_HMAX_F32 = np.float32(_HMAX)


def _hash2_np(ix, iy):
    h = (ix.astype(np.uint32) * np.uint32(1619) +
         iy.astype(np.uint32) * np.uint32(31337)) & np.uint32(0x7FFFFFFF)
    h = (((h >> np.uint32(16)) ^ h) * np.uint32(0x45D9F3B)) & np.uint32(0xFFFFFFFF)
    h = (((h >> np.uint32(16)) ^ h) * np.uint32(0x45D9F3B)) & np.uint32(0xFFFFFFFF)
    return ((h >> np.uint32(16)) ^ h) & np.uint32(0x7FFFFFFF)


def _value_noise_np(x, y):
    ix = np.floor(x).astype(np.int32)
    iy = np.floor(y).astype(np.int32)
    fx = (x - ix).astype(np.float32)
    fy = (y - iy).astype(np.float32)
    fx = fx*fx*fx * (fx * (fx * np.float32(6) - np.float32(15)) + np.float32(10))
    fy = fy*fy*fy * (fy * (fy * np.float32(6) - np.float32(15)) + np.float32(10))
    v00 = _hash2_np(ix,   iy  ).astype(np.float32) / _HMAX_F32
    v10 = _hash2_np(ix+1, iy  ).astype(np.float32) / _HMAX_F32
    v01 = _hash2_np(ix,   iy+1).astype(np.float32) / _HMAX_F32
    v11 = _hash2_np(ix+1, iy+1).astype(np.float32) / _HMAX_F32
    return v00 + fx*(v10-v00) + fy*(v01 + fx*(v11-v01) - v00 - fx*(v10-v00))


def _fbm_np(x, y, octaves=6):
    v = np.zeros_like(x, dtype=np.float32)
    amp = np.float32(0.5); freq = np.float32(1.0); maxv = np.float32(0.0)
    for _ in range(octaves):
        v += _value_noise_np(x * freq, y * freq) * amp
        maxv += amp; amp *= np.float32(0.5); freq *= np.float32(2.0)
    return v / maxv


def _warped_fbm_np(x, y, dx, dy):
    dx, dy = np.float32(dx), np.float32(dy)
    qx = _fbm_np(x + dx,                y + np.float32(0.3) + dy * np.float32(0.4), 2)
    qy = _fbm_np(x + np.float32(1.7),   y + np.float32(9.2) + dy,                   2)
    sc = np.float32(2.2)
    return _fbm_np(x + sc*qx + np.float32(1.3) + dx*np.float32(0.6),
                   y + sc*qy + np.float32(9.2) + dy*np.float32(0.5), 3)


def _bilinear_up2(small, H_out, W_out):
    H, W = small.shape
    ys = np.linspace(0, H - 1, H_out, dtype=np.float32)
    xs = np.linspace(0, W - 1, W_out, dtype=np.float32)
    iy = ys.astype(np.int32).clip(0, H - 2)
    ix = xs.astype(np.int32).clip(0, W - 2)
    fy = (ys - iy)[:, np.newaxis]
    fx = (xs - ix)[np.newaxis, :]
    s = small
    return (s[iy[:, np.newaxis],    ix[np.newaxis, :]]   * (1-fy) * (1-fx) +
            s[iy[:, np.newaxis],   (ix+1)[np.newaxis,:]] * (1-fy) * fx     +
            s[(iy+1)[:,np.newaxis], ix[np.newaxis,:]]    * fy     * (1-fx) +
            s[(iy+1)[:,np.newaxis],(ix+1)[np.newaxis,:]] * fy     * fx)


# ---------------------------------------------------------------------------
# C extension loader
# ---------------------------------------------------------------------------

_noise_lib    = None
_c_noise      = None
_c_upsample   = None
_c_slots      = None
_c_render32   = None
_c_render32cm = None

_fp32p = _ct.POINTER(_ct.c_float)
_u32p  = _ct.POINTER(_ct.c_uint32)
_i32p  = _ct.POINTER(_ct.c_int32)


def _load_noise_lib():
    global _noise_lib, _c_noise, _c_upsample, _c_slots, _c_render32, _c_render32cm
    so_path = os.path.normpath(_NOISE_SO)
    try:
        lib = _ct.CDLL(so_path)

        lib.compute_noise_grid.restype  = None
        lib.compute_noise_grid.argtypes = [
            _fp32p, _ct.c_int, _ct.c_int,
            _ct.c_float, _ct.c_float, _ct.c_float, _ct.c_float,
        ]
        lib.bilinear_upsample.restype  = None
        lib.bilinear_upsample.argtypes = [
            _fp32p, _ct.c_int, _ct.c_int,
            _fp32p, _ct.c_int, _ct.c_int,
        ]
        lib.compute_slots.restype  = None
        lib.compute_slots.argtypes = [
            _fp32p, _ct.c_int, _ct.c_int,
            _fp32p, _ct.c_int, _ct.c_int, _ct.c_int,
            _i32p, _i32p,
        ]
        lib.render_chars_32.restype  = None
        lib.render_chars_32.argtypes = [
            _u32p, _u32p, _i32p,
            _ct.c_int, _ct.c_int, _ct.c_int, _ct.c_int, _ct.c_int, _ct.c_int,
        ]
        lib.render_chars_32_cm.restype  = None
        lib.render_chars_32_cm.argtypes = [
            _u32p, _u32p, _i32p,
            _ct.c_int, _ct.c_int, _ct.c_int, _ct.c_int, _ct.c_int, _ct.c_int,
        ]

        _noise_lib    = lib
        _c_noise      = lib.compute_noise_grid
        _c_upsample   = lib.bilinear_upsample
        _c_slots      = lib.compute_slots
        _c_render32   = lib.render_chars_32
        _c_render32cm = lib.render_chars_32_cm
        print('[topo] C extension loaded', file=sys.stderr, flush=True)
    except Exception as e:
        print(f'[topo] C extension unavailable ({e}) — using numpy fallback',
              file=sys.stderr, flush=True)


_load_noise_lib()


def _compute_elev(COLS, ROWS, dx, dy):
    """Compute (ROWS, COLS) float32 elevation grid at given drift offsets."""
    W2 = max(COLS // 2 + 1, 2)
    H2 = max(ROWS // 2 + 1, 2)
    nx = float(NX * COLS / (W2 - 1)) if W2 > 1 else float(NX)
    ny = float(NY * ROWS / (H2 - 1)) if H2 > 1 else float(NY)
    if _c_noise is not None:
        small = np.empty(W2 * H2, dtype=np.float32)
        _c_noise(small.ctypes.data_as(_fp32p), W2, H2, float(dx), float(dy), nx, ny)
        if _c_upsample is not None:
            out = np.empty(COLS * ROWS, dtype=np.float32)
            _c_upsample(small.ctypes.data_as(_fp32p), W2, H2,
                        out.ctypes.data_as(_fp32p), COLS, ROWS)
            return out.reshape(ROWS, COLS)
        return _bilinear_up2(small.reshape(H2, W2), ROWS, COLS)
    else:
        xs = np.arange(W2, dtype=np.float32) * nx
        ys = np.arange(H2, dtype=np.float32) * ny
        xx, yy = np.meshgrid(xs, ys)
        small = _warped_fbm_np(xx, yy, dx, dy)
        return _bilinear_up2(small, ROWS, COLS)


# ---------------------------------------------------------------------------
# Speed table
# ---------------------------------------------------------------------------

SPEEDS = [('SLOW', 0.220), ('MED', 0.060), ('FAST', 0.025), ('FROZEN', None)]


# ---------------------------------------------------------------------------
# Rendering subprocess entry point
# ---------------------------------------------------------------------------

def _run_renderer(ctl_fifo: str, palette_name: str, speed_name: str,
                  windowed: bool) -> None:
    import pygame

    # -- Font
    font_path = _find_font()
    print(f'[topo] font: {font_path}', file=sys.stderr, flush=True)
    u2g, glyphs, CHAR_W, CHAR_H = load_psf(font_path)
    print(f'[topo] font {CHAR_W}x{CHAR_H}, {len(glyphs)} glyphs', file=sys.stderr, flush=True)

    # -- FIFO
    fifo_fd: Optional[int] = None
    try:
        if os.path.exists(ctl_fifo):
            if not stat.S_ISFIFO(os.stat(ctl_fifo).st_mode):
                os.remove(ctl_fifo)
                os.mkfifo(ctl_fifo, 0o666)
        else:
            os.mkfifo(ctl_fifo, 0o666)
        fifo_fd = os.open(ctl_fifo, os.O_RDWR | os.O_NONBLOCK)
        print(f'[topo] control FIFO: {ctl_fifo}', file=sys.stderr, flush=True)
    except OSError as e:
        print(f'[topo] FIFO unavailable: {e}', file=sys.stderr, flush=True)

    # -- pygame init
    os.environ.setdefault('SDL_VIDEODRIVER', 'x11')
    pygame.init()
    if windowed:
        screen = pygame.display.set_mode((1280, 720), 0, 32)
    else:
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN, 32)
    pygame.display.set_caption('Scanline — Topo')
    pygame.mouse.set_visible(False)

    SCR_W, SCR_H = screen.get_size()
    COLS = SCR_W // CHAR_W
    ROWS = SCR_H // CHAR_H
    print(f'[topo] {SCR_W}x{SCR_H} grid {COLS}x{ROWS} '
          f'bits={screen.get_bitsize()} masks={screen.get_masks()}',
          file=sys.stderr, flush=True)

    # -- Pixel helper (use surface's native format)
    def _px(r, g, b):
        return screen.map_rgb(int(r), int(g), int(b))

    # -- Buffers
    # cm_buf: (W, H) column-major — pygame surfarray layout, no transpose needed
    cm_buf      = np.empty((SCR_W, SCR_H), dtype=np.uint32)
    # back_buf: (H, W) row-major — only used by the numpy fallback renderer
    back_buf    = np.empty((SCR_H, SCR_W), dtype=np.uint32)
    glyph_slots = np.empty(ROWS * COLS, dtype=np.int32)
    color_slots = np.empty(ROWS * COLS, dtype=np.int32)
    flat_idx    = np.empty(ROWS * COLS, dtype=np.int32)
    tiles_row   = np.empty((ROWS * COLS, CHAR_W), dtype=np.uint32)

    # -- Atlas builder
    atlas:          Optional[np.ndarray] = None
    atlas_key                            = None
    atlas_n_colors: int                  = 0

    def build_atlas(pi, mi):
        pal  = PALETTES[pi]
        mode = CHAR_MODES[mi]
        fill = mode['fill']
        cfn  = mode['cfn']

        masks = []
        for ch in fill:
            gi = u2g.get(ord(ch), u2g.get(ord(' '), 0))
            masks.append(glyphs[gi])
        if cfn is not None:
            for case in range(4):
                ch = cfn(bool(case & 1), bool(case & 2))
                gi = u2g.get(ord(ch), u2g.get(ord(' '), 0))
                masks.append(glyphs[gi])
        masks_np = np.array(masks, dtype=bool)       # (N_g, CHAR_H, CHAR_W)

        bg_px  = np.uint32(_px(*pal['bg']))
        col_px = np.array(
            [_px(*c) for _, c in pal['bands']] + [_px(*pal['contour'])],
            dtype=np.uint32)                          # (N_color,)

        N_glyph  = len(masks)
        N_color  = len(col_px)
        raw = np.where(
            masks_np[:, np.newaxis, :, :],
            col_px[np.newaxis, :, np.newaxis, np.newaxis],
            bg_px).astype(np.uint32)                 # (N_g, N_color, CHAR_H, CHAR_W)
        flat = raw.reshape(N_glyph * N_color, CHAR_H, CHAR_W)
        return flat.transpose(1, 0, 2).copy(), N_color  # (CHAR_H, N_g*N_color, CHAR_W)

    # -- State
    pal_idx  = next((i for i, p in enumerate(PALETTES)
                     if p['name'] == palette_name), 4)  # default: PHOSPHOR
    mode_idx = 0
    spd_idx  = next((i for i, (n, _) in enumerate(SPEEDS)
                     if n == speed_name), 1)             # default: MED
    paused   = False

    drift_x, drift_y = 0.0, 0.0
    vel_angle = random.uniform(0, 2 * math.pi)
    vel_speed = random.uniform(0.010, 0.025)
    wander_a  = random.uniform(0.0, 50.0)
    wander_s  = random.uniform(50.0, 150.0)

    last_frame_t = 0.0   # force advance on first iteration
    ready_sent   = False
    _done        = False

    fps_t0      = time.monotonic()
    fps_renders = 0
    _t_noise    = 0.0
    _t_render   = 0.0
    _t_surfblit = 0.0
    _t_flip     = 0.0
    _t_blit     = 0.0

    def on_signal(sig, frame):
        nonlocal _done
        _done = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT,  on_signal)

    try:
        while not _done:
            # -- FIFO commands ------------------------------------------------
            if fifo_fd is not None:
                r, _, _ = _sel.select([fifo_fd], [], [], 0)
                if r:
                    try:
                        data = os.read(fifo_fd, 64).decode('utf-8', errors='ignore')
                        for ch in data:
                            ch = ch.strip()
                            if not ch:
                                continue
                            if ch == 'n':
                                pal_idx = (pal_idx + 1) % len(PALETTES)
                                print(f'[topo] palette → {PALETTES[pal_idx]["name"]}',
                                      file=sys.stderr, flush=True)
                            elif ch == 'b':
                                pal_idx = (pal_idx - 1) % len(PALETTES)
                                print(f'[topo] palette → {PALETTES[pal_idx]["name"]}',
                                      file=sys.stderr, flush=True)
                            elif ch == 'c':
                                mode_idx = (mode_idx + 1) % len(CHAR_MODES)
                                print(f'[topo] mode → {CHAR_MODES[mode_idx]["name"]}',
                                      file=sys.stderr, flush=True)
                            elif ch == 's':
                                spd_idx = (spd_idx + 1) % len(SPEEDS)
                                print(f'[topo] speed → {SPEEDS[spd_idx][0]}',
                                      file=sys.stderr, flush=True)
                            elif ch == 'p':
                                paused = not paused
                                print(f'[topo] paused={paused}',
                                      file=sys.stderr, flush=True)
                    except (BlockingIOError, OSError):
                        pass

            # -- Timing -------------------------------------------------------
            now = time.monotonic()
            _, interval = SPEEDS[spd_idx]
            advance = (not paused and interval is not None and
                       (now - last_frame_t) >= interval)

            # -- Atlas (rebuild on palette/mode change) -----------------------
            new_key = (pal_idx, mode_idx)
            atlas_changed = new_key != atlas_key
            if atlas_changed:
                atlas, atlas_n_colors = build_atlas(pal_idx, mode_idx)
                atlas_key = new_key

            # -- Render only when content changes ----------------------------
            if advance or atlas_changed:
                # -- Noise + slots --------------------------------------------
                _t0 = time.monotonic()
                if advance:
                    last_frame_t = now
                    wander_a += 0.004 + random.random() * 0.003
                    wander_s += 0.003 + random.random() * 0.003
                    ang_tgt   = (_value_noise(wander_a, 0.5) - 0.5) * math.pi * 1.4
                    vel_angle += (ang_tgt - vel_angle) * 0.035
                    spd_tgt   = 0.005 + _value_noise(0.5, wander_s) * 0.025
                    vel_speed += (spd_tgt - vel_speed) * 0.055
                    drift_x   += math.cos(vel_angle) * vel_speed
                    drift_y   += math.sin(vel_angle) * vel_speed * 0.55

                elev = _compute_elev(COLS, ROWS, drift_x, drift_y)

                pal      = PALETTES[pal_idx]
                mode     = CHAR_MODES[mode_idx]
                N_BANDS  = len(pal['bands'])
                fill_len = len(mode['fill'])
                cfn      = mode['cfn']

                if _c_slots is not None:
                    band_thresh = np.array([b[0] for b in pal['bands']], dtype=np.float32)
                    _c_slots(
                        elev.ctypes.data_as(_fp32p),
                        ROWS, COLS,
                        band_thresh.ctypes.data_as(_fp32p),
                        N_BANDS, fill_len,
                        CONTOUR_LEVELS if cfn is not None else 0,
                        glyph_slots.ctypes.data_as(_i32p),
                        color_slots.ctypes.data_as(_i32p),
                    )
                else:
                    band_thresh = np.array([b[0] for b in pal['bands']])
                    e2d = elev
                    cs2d = np.minimum(
                        np.searchsorted(band_thresh, e2d, side='right'),
                        N_BANDS - 1).astype(np.int32)
                    gs2d = np.minimum(
                        (np.minimum(e2d, 0.999) * fill_len).astype(np.int32),
                        fill_len - 1)
                    if cfn is not None:
                        e_r = np.empty_like(e2d)
                        e_r[:, :-1] = e2d[:, 1:]; e_r[:, -1] = e2d[:, -1]
                        e_d = np.empty_like(e2d)
                        e_d[:-1, :] = e2d[1:, :]; e_d[-1, :] = e2d[-1, :]
                        bg = (e2d * CONTOUR_LEVELS).astype(np.int32)
                        br = (e_r * CONTOUR_LEVELS).astype(np.int32)
                        bd = (e_d * CONTOUR_LEVELS).astype(np.int32)
                        is_c  = (bg != br) | (bg != bd)
                        has_h = np.abs(e2d - e_r) > 0.001
                        has_v = np.abs(e2d - e_d) > 0.001
                        cfn_c = has_h.astype(np.int32) + has_v.astype(np.int32) * 2
                        cs2d[is_c] = N_BANDS
                        gs2d[is_c] = fill_len + cfn_c[is_c]
                    color_slots[:] = cs2d.reshape(-1)
                    glyph_slots[:] = gs2d.reshape(-1)

                flat_idx[:] = glyph_slots * atlas_n_colors + color_slots
                _t_noise += time.monotonic() - _t0

                # -- Atlas scatter directly into surface pixel buffer ---------
                # Surface memory is row-major (H,W): pixel(x,y) at y*SCR_W+x.
                # render_chars_32 writes row-major with memcpy → sequential writes.
                # pixels2d() gives a view of that same memory — no copy/transpose.
                _t1 = time.monotonic()
                if _c_render32 is not None:
                    surf_pix = pygame.surfarray.pixels2d(screen)
                    _c_render32(
                        surf_pix.ctypes.data_as(_u32p),
                        atlas.ctypes.data_as(_u32p),
                        flat_idx.ctypes.data_as(_i32p),
                        ROWS, COLS, CHAR_H, CHAR_W, atlas.shape[1], SCR_W,
                    )
                    del surf_pix  # unlock surface
                else:
                    # Numpy fallback: render to (H,W) back_buf then blit
                    for r in range(CHAR_H):
                        np.take(atlas[r], flat_idx, axis=0, out=tiles_row)
                        back_buf[r::CHAR_H, :COLS * CHAR_W] = \
                            tiles_row.reshape(ROWS, COLS * CHAR_W)
                    np.copyto(cm_buf, back_buf.T)
                    pygame.surfarray.blit_array(screen, cm_buf)
                _t_render += time.monotonic() - _t1

                # -- Present: no blit_array copy when using pixels2d ----------
                _t2 = time.monotonic()
                pygame.display.flip()
                _t_flip += time.monotonic() - _t2
                _t_blit += time.monotonic() - _t2

                if not ready_sent:
                    sys.stdout.write('READY\n')
                    sys.stdout.flush()
                    ready_sent = True

                fps_renders += 1
                elapsed = time.monotonic() - fps_t0
                if elapsed >= 5.0:
                    n = max(fps_renders, 1)
                    print(f'[topo] FPS:{fps_renders/elapsed:.1f}'
                          f'  noise:{1000*_t_noise/n:.0f}ms'
                          f'  render:{1000*_t_render/n:.0f}ms'
                          f'  flip:{1000*_t_flip/n:.0f}ms',
                          file=sys.stderr, flush=True)
                    fps_t0 = time.monotonic(); fps_renders = 0
                    _t_noise = _t_render = _t_surfblit = _t_flip = _t_blit = 0.0

            else:
                # Content unchanged — sleep until next advance, keep display alive
                if interval is not None:
                    remaining = interval - (now - last_frame_t)
                    if remaining > 0.002:
                        time.sleep(min(0.020, remaining * 0.5))
                else:
                    time.sleep(0.020)  # FROZEN speed

            # -- pygame events (keep display alive) ---------------------------
            pygame.event.pump()

    finally:
        if fifo_fd is not None:
            try:
                os.close(fifo_fd)
            except OSError:
                pass
            try:
                os.remove(ctl_fifo)
            except OSError:
                pass
        pygame.quit()
        print('[topo] stopped.', file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# BaseRenderer subclass (when imported as a module)
# ---------------------------------------------------------------------------

if __name__ != '__main__':
    import subprocess
    from renderers.base_renderer import BaseRenderer

    _CTL_FIFO_DEFAULT = '/tmp/scanline-topo-ctl'

    class TopoRenderer(BaseRenderer):
        """Renderer for type='topo' channels. Spawns topo_renderer.py as a subprocess."""

        def __init__(self, channel, settings, windowed=False):
            super().__init__(channel, settings)
            self._windowed = windowed
            self._ready    = False

        def _do_spawn(self) -> None:
            cmd = [
                sys.executable,
                os.path.abspath(__file__),
                '--ctl-fifo', _CTL_FIFO_DEFAULT,
                '--palette',  self.channel.get('palette', 'PHOSPHOR'),
                '--speed',    self.channel.get('speed', 'MED'),
            ]
            if self._windowed:
                cmd.append('--windowed')

            env = os.environ.copy()
            self.process = subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=None,   # inherit scanline's stderr so logs are visible
                env=env,
            )
            # Make stdout non-blocking for readiness polling
            if sys.platform != 'win32':
                import fcntl
                fd    = self.process.stdout.fileno()
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        def is_ready(self) -> bool:
            if self._ready:
                return True
            if not self.is_alive() or self.process.stdout is None:
                return False
            try:
                data = self.process.stdout.read(256)
                if data and b'READY' in data:
                    self._ready = True
            except (BlockingIOError, OSError):
                pass
            return self._ready


# ---------------------------------------------------------------------------
# Main entry point (subprocess mode)
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Scanline topo renderer')
    parser.add_argument('--windowed',  action='store_true',
                        help='1280x720 window instead of fullscreen')
    parser.add_argument('--ctl-fifo',  default='/tmp/scanline-topo-ctl',
                        metavar='PATH', help='control FIFO path')
    parser.add_argument('--palette',   default='PHOSPHOR', metavar='NAME')
    parser.add_argument('--speed',     default='MED',
                        choices=[n for n, _ in SPEEDS], metavar='NAME')
    args = parser.parse_args()

    _run_renderer(
        ctl_fifo     = args.ctl_fifo,
        palette_name = args.palette,
        speed_name   = args.speed,
        windowed     = args.windowed,
    )

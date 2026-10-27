"""
BUILD 666. Make a model's first blink cheap to load.

WHAT THE PROFILER FOUND (PROBE 665f/g/h, mds7 bar runners, del2, mds7plr1)
==========================================================================
The field dip when a model comes into view is its FIRST BLINK. ff7nx_facial
loads that model's closed-eye textures (left + right) from flevel.lgp the
first time the blink reaches eye index 2, and each texture cost 150-185 ms:

    TEX header read (236 bytes)   85-100 ms   -- a jump back into the file
    pixel read                    ~85 ms      -- 1 MB at ~12 MB/s

The header costs as much as a megabyte because flevel.lgp is 1.4 GB and the
facial set sat in its middle: a read that jumps backwards makes the SD card's
FAT be walked from the start of the file. A read that follows on from the
last one is cheap (one texture in pass 518 paid only its pixel read).

Two data changes, no code:

1. `lgp.loose_first_order` places flevel's loose entries -- the facial set
   first, blink textures ahead of expressions -- right after the header, so
   the walk is short. (lgp.py, on by default for flevel.lgp.)

2. `cap_entries` here shrinks the facial textures that are loaded LAZILY (the
   ones with an index suffix: eye_<model>_2, B_EYE2R_3, ...) to at most
   SEVENTH_NX_FACIAL_EYE_PX (default 64) on the long side, area-averaged with
   premultiplied alpha. The generic sets (B_/C_/T_/V_/Y_EYE2*, CHI_RED5_*,
   AKEE, AJIF ...) are 512x512 = 1 MB each and every townsperson without an
   eye set of its own falls back to one of them. A chibi eye is a few dozen
   pixels on screen; 64x64 is still above that and is 16 KB to read.
   The suffix-less textures (the open eyes the stock loader reads while the
   field is loading) are not touched. SEVENTH_NX_FACIAL_EYE_PX=0 keeps every
   texture at full size.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
import re
import struct

import lgp
import tex

PX_ENV = 'SEVENTH_NX_FACIAL_EYE_PX'
DEFAULT_PX = 64


def cap_px(env=None):
    env = os.environ if env is None else env
    raw = env.get(PX_ENV, str(DEFAULT_PX)).strip()
    try:
        px = int(raw)
    except ValueError:
        return DEFAULT_PX
    if px <= 0:
        return 0
    return max(16, min(px, 1024))


def is_lazy_facial(name):
    """A facial texture loaded at blink/expression time (has an index)."""
    low = name.lower()
    if '.' not in low or lgp.loose_rank(low) == 2:
        return False
    return re.search(r'_\d+$', low.rsplit('.', 1)[0]) is not None


def _resample(t, nw, nh):
    from PIL import Image
    w, h = t['width'], t['height']
    img = Image.frombytes('RGBA', (w, h), bytes(t['pixels']), 'raw', 'BGRA')
    img = img.convert('RGBa').resize((nw, nh), Image.BOX).convert('RGBA')
    return img.tobytes('raw', 'BGRA')


def cap_texture(data, px):
    """(new_bytes, (w, h, nw, nh)) or (None, reason)."""
    t = tex.parse(data)
    if t is None:
        return None, 'not a TEX'
    if t['palette_flag'] or t['bytes_per_pixel'] != 4 or t['palette_size']:
        return None, 'not 32-bit truecolor'
    w, h = t['width'], t['height']
    nw, nh = tex._cap_size(w, h, px)
    if (nw, nh) == (w, h):
        return None, 'already within cap'
    hdr = bytearray(data[:tex.HEADER_LEN])
    struct.pack_into('<I', hdr, tex.O_WIDTH, nw)
    struct.pack_into('<I', hdr, tex.O_HEIGHT, nh)
    if tex._u32(data, tex.O_PITCH):
        struct.pack_into('<I', hdr, tex.O_PITCH, nw * 4)
    out = bytes(hdr) + _resample(t, nw, nh)
    if tex.parse(out) is None:
        return None, 'resample produced an invalid TEX'
    return out, (w, h, nw, nh)


def cap_entries(archive, px=None, log=lambda *_: None):
    """Shrink the lazily loaded facial textures in `archive` in place."""
    px = cap_px() if px is None else px
    stats = {'px': px, 'capped': 0, 'before': 0, 'after': 0, 'kept': 0}
    if not px:
        return stats
    for e in archive.entries:
        if not is_lazy_facial(e['name']):
            continue
        new, _why = cap_texture(e['payload'], px)
        if new is None:
            stats['kept'] += 1
            continue
        stats['capped'] += 1
        stats['before'] += len(e['payload'])
        stats['after'] += len(new)
        e['payload'] = new
    return stats


def summarise(stats):
    if not stats.get('px'):
        return 'facial eye textures: full size (%s=0)' % PX_ENV
    return ('facial eye textures: %d lazily loaded texture(s) capped to %dpx '
            '(%.1f MB -> %.1f MB), %d already small; flevel places them first'
            % (stats['capped'], stats['px'], stats['before'] / 1e6,
               stats['after'] / 1e6, stats['kept']))

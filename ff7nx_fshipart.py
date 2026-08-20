"""
ff7nx_fshipart.py -- remove the isolated black wedge in fship_1/fship_12.

This is deliberately an exact field-art correction, not a general "dark
pixel" rule.  The wedge is present in Cosmos's page art and the archive
reproduces it faithfully; it is not a missing page or a parallax failure.

In both variants it lives in one layer-2 tile at dst(-192,-104).  Inside that
tile it is a disconnected near-black component touching the cell's left and
bottom edges.  The source cell is referenced once, and a stationary layer-3
sea tile covers the whole destination.  Turning only that component into the
depth-2 colour key therefore reveals the sea that is already behind it.

Every structural fact above is checked before a byte is changed.  If Cosmos,
the repacker, or the field layout changes, the pass refuses the field instead
of applying a guessed mask.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import field_bg_native as FN


FIELDS = frozenset({'fship_1', 'fship_12'})
TARGET_DST = (-192, -104)
TARGET_LAYER = 2
TILE_SIZE = 52
T_DSTX = 2
T_DSTY = 4
T_W = 18
T_TEX = 32
T_TEX2 = 34
T_SRCX = 42
UV_SCALE = 10_000_000
OFF_ENV = 'SEVENTH_NX_NO_FSHIP_ART_FIX'

# All depth-2 sizes the builder can emit.  Trying the exact layouts is safer
# than trusting the current environment when inspecting an already-built
# archive made with different settings.
D2_PX = (128, 256, 320, 384, 448, 512, 768, 1024)


class ArtFixError(ValueError):
    """The field no longer has the exact layout this correction proves safe."""


def disabled():
    return os.environ.get(OFF_ENV) == '1'


def _parse_pages(sec9):
    last = None
    keep = FN.D1_PAGE_PX
    try:
        # The depth-1 lift deliberately restores this module-global after it
        # serialises, although the finished section may now hold 512px d1
        # pages. Detect both dimensions instead of parsing a lifted section
        # with the pre-lift size.
        for d1px in dict.fromkeys((keep, 256, 512)):
            FN.D1_PAGE_PX = d1px
            for d2px in D2_PX:
                try:
                    pages, tex_start, tex_end = FN.parse_texture_block(
                        sec9, d2px)
                except Exception as exc:                      # noqa: BLE001
                    last = exc
                    continue
                return pages, tex_start, tex_end, d2px, d1px
    finally:
        FN.D1_PAGE_PX = keep
    raise ArtFixError('no supported depth-2 page size parses (%s)' % last)


def _walk_layers(sec9, back, tex):
    """Yield (layer, tile offsets), structurally to the TEXTURE marker."""
    out = []
    o = back + 4
    if back < 0:
        raise ArtFixError('no BACK marker')
    _w, _h, n1, _d, _b = struct.unpack_from('<HHHHH', sec9, o)
    o += 10
    out.append((1, [o + i * TILE_SIZE for i in range(n1)]))
    o += n1 * TILE_SIZE + 2
    for layer, unused in ((2, 16), (3, 10), (4, 10)):
        if o >= tex:
            break
        flag = sec9[o]
        o += 1
        if flag == 0:
            continue
        if flag != 1:
            raise ArtFixError('layer %d flag is %d' % (layer, flag))
        _w, _h, n = struct.unpack_from('<HHH', sec9, o)
        o += 6 + unused + 2
        out.append((layer, [o + i * TILE_SIZE for i in range(n)]))
        o += n * TILE_SIZE + 2
    if o != tex:
        raise ArtFixError('layer walk ended at %d, TEXTURE at %d' % (o, tex))
    return out


def _tile_edge(sec9, off, layer):
    if layer == 1:
        return 16
    w, h = struct.unpack_from('<HH', sec9, off + T_W)
    n = max(w, h)
    return n if n in (16, 32) else 16


def _stationary_sea_covers(sec7, sec9, layers):
    """Prove layer 3 is pinned and covers every unit of the target cell."""
    vals = struct.unpack_from('<12h', sec7, 0x18)
    (_w, _h, pos_x, pos_y, speed_x, speed_y) = (
        vals[0], vals[1], vals[4], vals[5], vals[8], vals[9])
    if (pos_x, pos_y, speed_x, speed_y) != (0, 0, 0, 0):
        return False

    x0, y0 = TARGET_DST
    covered = np.zeros((16, 16), bool)
    layer3 = next((offs for layer, offs in layers if layer == 3), [])
    for off in layer3:
        tx, ty = struct.unpack_from('<hh', sec9, off + T_DSTX)
        n = _tile_edge(sec9, off, 3)
        ax, bx = max(x0, tx), min(x0 + 16, tx + n)
        ay, by = max(y0, ty), min(y0 + 16, ty + n)
        if ax < bx and ay < by:
            covered[ay - y0:by - y0, ax - x0:bx - x0] = True
    return bool(covered.all())


def _components(mask):
    """Return lists of (y,x) for four-connected true components."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or seen[y, x]:
                continue
            seen[y, x] = True
            todo = [(y, x)]
            comp = []
            while todo:
                yy, xx = todo.pop()
                comp.append((yy, xx))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = yy + dy, xx + dx
                    if (0 <= ny < h and 0 <= nx < w and mask[ny, nx]
                            and not seen[ny, nx]):
                        seen[ny, nx] = True
                        todo.append((ny, nx))
            out.append(comp)
    return out


def artifact_component(cell):
    """The exact near-black wedge component, or None if the fingerprint fails."""
    if cell.ndim != 2 or cell.shape[0] != cell.shape[1]:
        return None
    side = cell.shape[0]
    v = cell.astype(np.uint32, copy=False)
    # 565 channels at most RGB(8,8,8).  The shipped wedge contains only
    # 0x0841, 0x0840 and 0x0041; zero itself is already transparent.
    r = ((v >> 11) & 31) * 8
    g = ((v >> 5) & 63) * 4
    b = (v & 31) * 8
    dark = (v != 0) & (np.maximum(np.maximum(r, g), b) <= 8)

    candidates = []
    for comp in _components(dark):
        ys = [p[0] for p in comp]
        xs = [p[1] for p in comp]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        area = len(comp) / float(side * side)
        # The unwanted wedge touches LEFT+BOTTOM, is isolated from the main
        # strut shadow, and occupies about 9.5% of this one cell at 768px.
        if (x0 == 0 and y1 == side - 1 and y0 > side * 0.25
                and x1 < side * 0.45 and 0.05 < area < 0.15):
            candidates.append(comp)
    return candidates[0] if len(candidates) == 1 else None


def _page_data_offset(sec9, tex_start, slot, d2px, d1px):
    """Byte offset of one page's pixels, independently checked by the parser."""
    o = tex_start + len(b'TEXTURE')
    for cur in range(FN.BG_MAX_PAGES):
        present, = struct.unpack_from('<H', sec9, o)
        o += 2
        if not present:
            continue
        size_flag, depth = struct.unpack_from('<HH', sec9, o)
        del size_flag
        o += 4
        if cur == slot:
            return o
        # FN.stored_bytes intentionally ignores its `px` argument for depth
        # 1 and consults the build-global current size.  We are detecting an
        # already-serialised section, so use the detected d1 size explicitly.
        o += (FN.stored_bytes(d2px, 2) if depth == 2 else d1px * d1px)
    raise ArtFixError('slot %d is absent' % slot)


def apply_to_field(raw, split, join, field_name):
    """Return (raw, changed_texels).  Non-target fields are byte-identical."""
    if field_name not in FIELDS:
        return raw, 0
    parts = list(split(raw))
    if len(parts) < 9:
        raise ArtFixError('not a nine-section field')
    sec7, sec9 = parts[7], parts[8]
    pages, tex_start, _tex_end, d2px, d1px = _parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}
    layers = _walk_layers(sec9, sec9.find(b'BACK'), tex_start)

    targets = []
    for layer, offs in layers:
        if layer != TARGET_LAYER:
            continue
        for off in offs:
            if struct.unpack_from('<hh', sec9, off + T_DSTX) == TARGET_DST:
                targets.append(off)
    if len(targets) != 1:
        raise ArtFixError('expected one layer-2 tile at %r, found %d'
                          % (TARGET_DST, len(targets)))
    if not _stationary_sea_covers(sec7, sec9, layers):
        raise ArtFixError('stationary layer-3 sea does not cover target cell')

    off = targets[0]
    if _tile_edge(sec9, off, TARGET_LAYER) != 16:
        raise ArtFixError('target is not a 16-unit tile')
    slot = sec9[off + T_TEX]
    page = pmap.get(slot)
    if page is None or page.depth != 2 or page.size_flag:
        raise ArtFixError('target is not on a 16-grid depth-2 page')
    u, v = struct.unpack_from('<II', sec9, off + T_SRCX)
    step = page.px // 16
    cx = int(round(u / UV_SCALE * 16))
    cy = int(round(v / UV_SCALE * 16))
    sx, sy = cx * step, cy * step
    if not (0 <= sx <= page.px - step and 0 <= sy <= page.px - step):
        raise ArtFixError('target source cell falls outside its page')

    # A shared atlas cell would turn this local correction into a second,
    # unrelated visual change. Count base AND animated-page references: an fx
    # page is sampled with this same base UV when its frame is active.
    refs = 0
    for _layer, offs in layers:
        for other in offs:
            if (sec9[other + T_TEX] != slot
                    and sec9[other + T_TEX2] != slot):
                continue
            ou, ov = struct.unpack_from('<II', sec9, other + T_SRCX)
            ocx = int(round(ou / UV_SCALE * 16))
            ocy = int(round(ov / UV_SCALE * 16))
            if (ocx, ocy) == (cx, cy):
                refs += 1
    if refs != 1:
        raise ArtFixError('target source cell has %d tile references' % refs)

    page_pixels = np.frombuffer(page.data, '<u2').reshape(page.px, page.px)
    cell = page_pixels[sy:sy + step, sx:sx + step]
    comp = artifact_component(cell)
    if comp is None:
        # Fixed point, and also the safe result for another page size whose
        # alpha reduction already made the wedge transparent.
        return raw, 0

    buf = bytearray(sec9)
    data_at = _page_data_offset(sec9, tex_start, slot, d2px, d1px)
    for y, x in comp:
        struct.pack_into('<H', buf,
                         data_at + 2 * ((sy + y) * page.px + sx + x), 0)
    parts[8] = bytes(buf)
    return join(parts), len(comp)


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    """Apply the guarded correction only to the two fship scene variants."""
    import lgp

    stats = {'on': not disabled(), 'fields': 0, 'texels': 0, 'clean': 0,
             'refused': []}
    if disabled():
        log('  fship art correction: OFF (%s=1)' % OFF_ENV)
        return stats
    encode = encode or archive.encode_field
    for name in sorted(FIELDS):
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            stats['refused'].append((name, 'field is absent'))
            continue
        try:
            raw = (lgp.lzs_decompress(payloads[name][4:])
                   if name in payloads else archive.decompressed(entry))
            new_raw, changed = apply_to_field(
                raw, lgp.split_sections, lgp.join_sections, name)
        except Exception as exc:                               # noqa: BLE001
            stats['refused'].append((name, '%s: %s'
                                     % (type(exc).__name__, str(exc)[:80])))
            continue
        if not changed:
            stats['clean'] += 1
            continue
        payloads[name] = encode(new_raw)
        stats['fields'] += 1
        stats['texels'] += changed
    if stats['refused']:
        log('  ! fship art correction: %d field(s) refused (%s)'
            % (len(stats['refused']), ', '.join(
                '%s: %s' % item for item in stats['refused'][:2])))
    return stats


def summarise(stats):
    if not stats or not stats.get('on'):
        return ''
    return ('  fship art correction: %d near-black wedge texel(s) keyed in '
            '%d field(s); %d already clean%s'
            % (stats['texels'], stats['fields'], stats['clean'],
               ', %d refused' % len(stats['refused'])
               if stats['refused'] else ''))

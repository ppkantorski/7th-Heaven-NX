"""
FF7 Switch battle stageNN.dat container: parser + tile encoder.

Confirmed byte-exact against data/battle/stage57.dat:

  Container:
    u32 count
    u32 offsets[count]        -- absolute file offsets into sections

  Sections that are backgrounds are embedded PSX TIM images:
    u32 magic (0x10)
    u32 flag                  bit0-2: pixel mode (1 = 8bpp indexed)
                               bit3:   CLUT (palette) present
    if CLUT present:
        u32 clut_size (includes this 12-byte sub-header)
        i16 clut_x, i16 clut_y
        u16 clut_w (colors per row), u16 clut_h (number of palette rows)
        u8  clut palette data: clut_h * clut_w * 2 bytes, each entry a
            16-bit PSX color: bit15=STP, bits10-14=B, bits5-9=G, bits0-4=R
    image section immediately follows the CLUT block:
        u32 img_size (includes this 12-byte sub-header)
        i16 img_x, i16 img_y
        u16 img_w_raw (in 16-bit VRAM units), u16 img_h
        u8  pixel data: for 8bpp, real_w = img_w_raw * 2, real_w*img_h bytes,
            each byte a palette index

  Tiling (confirmed on stage57, 3 tiles from one TIM + 3-row CLUT):
    when real_w == clut_h * 256, the image is `clut_h` horizontal strips of
    256px each, strip i using palette ROW i. Tile i <-> strip i.

  Stages with per-tile-varying DDS dimensions (e.g. stage22, stage01) do NOT
  fit this single-TIM/N-strip model -- their tiles are presumably separate
  single-palette TIM sections. That case is only partially handled here
  (one tile per single-palette TIM, in file order) and is NOT validated
  against real game data since only stage57.dat was available to test
  against. parse_stage() sets ambiguous=True and tiles=[] whenever the
  structure can't be established with confidence, and callers must skip
  those stages rather than guess.
"""
import struct

import tex  # reuse _median_cut / _dither_to_palette, proven against the
            # port's paletted-texture pipeline


def _u16(d, off):
    return struct.unpack_from('<H', d, off)[0]


def _rgba_from_555(c):
    r = (c & 0x1F) << 3
    g = ((c >> 5) & 0x1F) << 3
    b = ((c >> 10) & 0x1F) << 3
    a = 255
    return (r, g, b, a)


def _555_from_rgb(r, g, b):
    r5 = min(31, r >> 3)
    g5 = min(31, g >> 3)
    b5 = min(31, b >> 3)
    stp = 0 if (r, g, b) == (0, 0, 0) else 1
    return (stp << 15) | (b5 << 10) | (g5 << 5) | r5


def _try_parse_tim(data, off):
    if off + 8 > len(data):
        return None
    magic, flag = struct.unpack_from('<II', data, off)
    if magic != 0x10:
        return None
    pmode = flag & 0x7
    has_clut = bool(flag & 0x8)
    if pmode != 1 or not has_clut:
        return None  # only 8bpp+CLUT battle backgrounds are supported
    p = off + 8
    if p + 12 > len(data):
        return None
    clut_size, clut_x, clut_y, clut_w, clut_h = struct.unpack_from(
        '<IhhHH', data, p)
    pal_off = p + 12
    if pal_off + clut_size - 12 > len(data):
        return None
    p2 = p + clut_size
    if p2 + 12 > len(data):
        return None
    img_size, img_x, img_y, img_w_raw, img_h = struct.unpack_from(
        '<IhhHH', data, p2)
    real_w = img_w_raw * 2
    pix_off = p2 + 12
    if pix_off + real_w * img_h > len(data):
        return None
    if img_size != 12 + real_w * img_h:
        return None
    return {
        'tim_off': off,
        'flag': flag,
        'clut': {'w': clut_w, 'h': clut_h, 'data_off': pal_off,
                  'size': clut_size},
        'real_w': real_w, 'img_h': img_h, 'pix_off': pix_off,
        'supported': True,
    }


def parse_stage(data):
    count = struct.unpack_from('<I', data, 0)[0]
    offsets = list(struct.unpack_from('<%dI' % count, data, 4))

    tims = []
    for o in offsets:
        t = _try_parse_tim(data, o)
        if t:
            tims.append(t)

    tiles = []
    ambiguous = False
    notes = []

    if len(tims) == 1 and tims[0]['clut']['h'] >= 1 and \
            tims[0]['real_w'] == tims[0]['clut']['h'] * 256:
        t = tims[0]
        for strip in range(t['clut']['h']):
            tiles.append({
                'tim_index': 0,
                'strip': strip,
                'x0': strip * 256,
                'w': 256,
                'h': t['img_h'],
                'row_stride': t['real_w'],
                'pix_base_off': t['pix_off'],
                'pal_row_off': t['clut']['data_off']
                               + strip * t['clut']['w'] * 2,
                'pal_colors': t['clut']['w'],
            })
    elif len(tims) >= 1 and all(ti['clut']['h'] == 1 for ti in tims):
        for i, t in enumerate(tims):
            tiles.append({
                'tim_index': i,
                'strip': 0,
                'x0': 0,
                'w': t['real_w'],
                'h': t['img_h'],
                'row_stride': t['real_w'],
                'pix_base_off': t['pix_off'],
                'pal_row_off': t['clut']['data_off'],
                'pal_colors': t['clut']['w'],
            })
    else:
        ambiguous = True
        notes.append(
            'stage has %d TIM section(s) that do not match either the '
            'single-TIM/N-strip pattern or the one-TIM-per-tile pattern -- '
            'not spliceable without further reverse engineering' % len(tims))

    return {
        'count': count, 'offsets': offsets, 'tims': tims,
        'tiles': tiles, 'ambiguous': ambiguous, 'notes': notes,
    }


def decode_tile(data, stage, tile_idx):
    tl = stage['tiles'][tile_idx]
    w, h = tl['w'], tl['h']
    pal = [_rgba_from_555(_u16(data, tl['pal_row_off'] + i * 2))
           for i in range(tl['pal_colors'])]
    out = bytearray(w * h * 4)
    stride = tl['row_stride']
    base = tl['pix_base_off']
    x0 = tl['x0']
    for y in range(h):
        row = base + y * stride + x0
        orow = y * w * 4
        for x in range(w):
            idx = data[row + x]
            r, g, b, a = pal[idx]
            o = orow + x * 4
            out[o], out[o+1], out[o+2], out[o+3] = r, g, b, a
    return bytes(out), w, h


def build_modified_stage(data, stage, tile_updates):
    """
    tile_updates: {tile_idx: [ (r,g,b,a), ... ] } length w*h, row-major,
    already resized to the tile's native (w, h).
    """
    out = bytearray(data)
    for tile_idx, rgba_list in tile_updates.items():
        tl = stage['tiles'][tile_idx]
        w, h = tl['w'], tl['h']
        assert len(rgba_list) == w * h, (
            'tile %d expects %d pixels, got %d'
            % (tile_idx, w * h, len(rgba_list)))

        # Force alpha=255: background tiles are fully opaque, and tex._median_cut
        # keys on the full RGBA tuple, so alpha must be normalised or two
        # otherwise-identical colors get treated as different histogram
        # buckets.
        norm = [(r, g, b, 255) for (r, g, b, a) in rgba_list]

        palette, mapping = tex._median_cut(norm, 256)
        if not palette:
            palette, mapping = [(0, 0, 0, 255)], {(0, 0, 0, 255): 0}

        # Direct nearest-match indices, NOT tex._dither_to_palette's
        # reserved-transparent-index-0 convention -- these tiles have no
        # transparency, so index 0 is a normal opaque color like any other.
        def nearest(c, _pal=palette):
            best, bd = 0, 1 << 30
            for i, p in enumerate(_pal):
                d = ((c[0]-p[0])**2 + (c[1]-p[1])**2 + (c[2]-p[2])**2)
                if d < bd:
                    bd, best = d, i
            return best

        idx_bytes = bytearray(w * h)
        for i, c in enumerate(norm):
            idx_bytes[i] = mapping.get(c) if c in mapping else nearest(c)

        # pad palette to pal_colors entries with black
        pal_colors = tl['pal_colors']
        entries = list(palette) + [(0, 0, 0, 255)] * (pal_colors - len(palette))
        entries = entries[:pal_colors]

        pal_off = tl['pal_row_off']
        for i, (r, g, b, _a) in enumerate(entries):
            struct.pack_into('<H', out, pal_off + i * 2, _555_from_rgb(r, g, b))

        stride = tl['row_stride']
        base = tl['pix_base_off']
        x0 = tl['x0']
        for y in range(h):
            row = base + y * stride + x0
            out[row:row + w] = idx_bytes[y * w:(y + 1) * w]

    return bytes(out)

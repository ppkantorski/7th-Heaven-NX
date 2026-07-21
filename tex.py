"""
FF7 TEX texture inspection and Switch-compatibility conversion.

Why this exists: the Switch battle module appears to implement the enemy
death "red dissolve" (and some magic effects) by manipulating the texture
PALETTE, PSX-style. Modern PC mod textures are 24/32-bit with no palette,
so the effect has nothing to operate on -- defeated enemies vanish
instantly, and some render white. The field module has no such dependency
(a proven-good char.lgp full of 24-bit TEX renders fine), so conversion is
only applied to battle.lgp / magic.lgp.

convert_for_battle() takes a modern TEX and produces an 8-bit paletted one:
- resized (nearest-neighbour) down to the vanilla counterpart's dimensions
  when available, else capped at 256x256 -- vanilla-scale textures also undo
  the 168->398 MB battle.lgp bloat;
- colors median-cut quantized to the palette size the vanilla header
  declares (else 256);
- header mirrored from the vanilla entry when available (safest: the engine
  sees exactly the structure it expects), else rewritten in the standard
  paletted layout.

Everything is stdlib-only.
"""
import struct

HEADER_LEN = 0xEC

# header field offsets (bytes)
O_VERSION = 0x00
O_COLORKEY = 0x08
O_MIN_BPC = 0x14
O_MAX_BPC = 0x18
O_MIN_ABITS = 0x1C
O_MAX_ABITS = 0x20
O_MIN_BPP = 0x24
O_MAX_BPP = 0x28
O_NUM_PALETTES = 0x30
O_COLORS_PER_PAL = 0x34
O_BIT_DEPTH = 0x38
O_WIDTH = 0x3C
O_HEIGHT = 0x40
O_PITCH = 0x44
O_PAL_FLAG = 0x4C
O_BITS_PER_INDEX = 0x50
O_PAL_SIZE = 0x58
O_COLORS_PER_PAL2 = 0x5C
O_BITS_PER_PIXEL = 0x64
O_BYTES_PER_PIXEL = 0x68


def _u32(d, off):
    return struct.unpack_from('<I', d, off)[0]


def parse(data):
    """
    Parse a TEX file strictly. Returns a dict or None if this is not a TEX.
    The exact-size check matters: .P model files also start with version 1,
    and must never be misidentified.
    """
    if len(data) < HEADER_LEN or _u32(data, O_VERSION) != 1:
        return None
    w, h = _u32(data, O_WIDTH), _u32(data, O_HEIGHT)
    if not (0 < w <= 4096 and 0 < h <= 4096):
        return None
    bytespp = _u32(data, O_BYTES_PER_PIXEL)
    palsize = _u32(data, O_PAL_SIZE)
    if bytespp not in (1, 2, 3, 4):
        return None
    if len(data) != HEADER_LEN + palsize * 4 + w * h * bytespp:
        return None
    return {
        'width': w, 'height': h,
        'bytes_per_pixel': bytespp,
        'palette_flag': _u32(data, O_PAL_FLAG),
        'num_palettes': _u32(data, O_NUM_PALETTES),
        'colors_per_palette': _u32(data, O_COLORS_PER_PAL),
        'palette_size': palsize,
        'palette': data[HEADER_LEN:HEADER_LEN + palsize * 4],
        'pixels': data[HEADER_LEN + palsize * 4:],
    }


def is_unpaletted(data):
    """True for a valid TEX with no palette (24/32-bit truecolor)."""
    t = parse(data)
    return bool(t) and t['palette_flag'] == 0 and t['bytes_per_pixel'] >= 3


# ------------------------------------------------------------ conversion

def _resample_rgba(pixels, w, h, bytespp, nw, nh):
    """Nearest-neighbour resample to (nw, nh); returns list of RGBA tuples.
    Source pixel order is BGR(A) as stored in TEX truecolor data."""
    out = []
    for y in range(nh):
        sy = y * h // nh
        row = sy * w * bytespp
        for x in range(nw):
            i = row + (x * w // nw) * bytespp
            b, g, r = pixels[i], pixels[i + 1], pixels[i + 2]
            a = pixels[i + 3] if bytespp == 4 else 255
            out.append((r, g, b, a))
    return out


def _median_cut(pixels, max_colors):
    """Median-cut quantization of RGBA tuples -> (palette, index_of_pixel).
    Fully transparent input pixels are excluded; caller maps them to the
    reserved transparent index."""
    from collections import Counter
    hist = Counter(p for p in pixels if p[3] >= 128)
    if not hist:
        return [], {}
    boxes = [list(hist.items())]
    while len(boxes) < max_colors:
        # split the box with the largest spread * population
        best, best_score, best_ch = None, -1, 0
        for bi, box in enumerate(boxes):
            if len(box) < 2:
                continue
            for ch in range(3):
                vals = [c[ch] for c, _ in box]
                score = (max(vals) - min(vals)) * (len(box) ** 0.5)
                if score > best_score:
                    best, best_score, best_ch = bi, score, ch
        if best is None:
            break
        box = boxes.pop(best)
        box.sort(key=lambda cv: cv[0][best_ch])
        half = sum(v for _, v in box) / 2
        acc, cut = 0, 1
        for i, (_, v) in enumerate(box):
            acc += v
            if acc >= half and 0 < i + 1 < len(box):
                cut = i + 1
                break
        else:
            cut = len(box) // 2
        boxes.append(box[:cut])
        boxes.append(box[cut:])
    palette, mapping = [], {}
    for box in boxes:
        tot = sum(v for _, v in box)
        r = round(sum(c[0] * v for c, v in box) / tot)
        g = round(sum(c[1] * v for c, v in box) / tot)
        b = round(sum(c[2] * v for c, v in box) / tot)
        idx = len(palette)
        palette.append((r, g, b, 255))
        for c, _ in box:
            mapping[c] = idx
    return palette, mapping


def _target_dims(w, h, vanilla, cap=256):
    if vanilla and vanilla['palette_flag']:
        return vanilla['width'], vanilla['height']
    nw = min(w, cap)
    nh = min(h, cap)
    return nw, nh


def _paletted_header(base, nw, nh, n_pal, palsize):
    """Standard 8-bit paletted header, field-matched to hardware-proven
    paletted mod textures (Aerith rvae / Vincent smac / Cid rzae)."""
    hdr = bytearray(base[:HEADER_LEN])

    def put(off, val):
        struct.pack_into('<I', hdr, off, val)

    put(O_COLORKEY, 1)
    put(O_MIN_BPC, 8); put(O_MAX_BPC, 8)
    put(O_MIN_ABITS, 0); put(O_MAX_ABITS, 8)
    put(O_MIN_BPP, 8); put(O_MAX_BPP, 32)
    put(O_NUM_PALETTES, n_pal)
    put(O_COLORS_PER_PAL, 256)
    put(O_BIT_DEPTH, 8)
    put(O_WIDTH, nw); put(O_HEIGHT, nh); put(O_PITCH, nw)
    put(O_PAL_FLAG, 1)
    put(O_BITS_PER_INDEX, 8)
    put(O_PAL_SIZE, palsize)
    put(O_COLORS_PER_PAL2, 256)
    put(O_BITS_PER_PIXEL, 8)
    put(O_BYTES_PER_PIXEL, 1)
    for off in range(0x6C, 0xBC, 4):
        put(off, 0)
    put(0x9C, 8); put(0xA0, 8); put(0xA4, 8); put(0xA8, 8)
    return hdr


def _dither_to_palette(rgba, w, h, palette):
    """Floyd-Steinberg dither RGBA pixels onto `palette` (list of RGB(A)
    tuples). Returns per-pixel palette index list (0-based into palette).
    Transparent pixels return -1."""
    pr = [list(p) for p in rgba]
    out = [0] * (w * h)
    pal = [(p[0], p[1], p[2]) for p in palette]

    def nearest(r, g, b):
        best, bd = 0, 1 << 30
        for i, (pr_, pg_, pb_) in enumerate(pal):
            d = (r - pr_) ** 2 + (g - pg_) ** 2 + (b - pb_) ** 2
            if d < bd:
                bd, best = d, i
        return best

    for y in range(h):
        for x in range(w):
            i = y * w + x
            if rgba[i][3] < 128:
                out[i] = -1
                continue
            r = min(255, max(0, int(pr[i][0])))
            g = min(255, max(0, int(pr[i][1])))
            b = min(255, max(0, int(pr[i][2])))
            k = nearest(r, g, b)
            out[i] = k
            er, eg, eb = r - pal[k][0], g - pal[k][1], b - pal[k][2]
            for dx, dy, f in ((1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and rgba[ny * w + nx][3] >= 128:
                    j = ny * w + nx
                    pr[j][0] += er * f / 16
                    pr[j][1] += eg * f / 16
                    pr[j][2] += eb * f / 16
    return out


def convert_for_battle(data, vanilla_data=None, cap=256):
    """
    Convert a truecolor mod TEX for the battle module, which renders enemy
    textures WHITE unless they are paletted (hardware-established: the
    same enemies are correct as 256-color paletted and white as truecolor,
    both full-size and downscaled; only PLAYER models get away with
    truecolor). Policy:

    - vanilla counterpart paletted with 256-color palettes: quantize to
      the vanilla structure verbatim (v4-proven correct on hardware).
    - vanilla counterpart paletted with small palettes (16 colors): keep
      the vanilla palette structure verbatim but raise dimensions to
      <=cap and Floyd-Steinberg dither -- the undithered 64x64 16-color
      output read as a black blob on hardware.
    - no vanilla / vanilla truecolor: standard 1x256 paletted header.

    Returns (new_bytes, note) or (None, reason).
    """
    t = parse(data)
    if t is None:
        return None, 'not a TEX'
    if t['palette_flag'] or t['bytes_per_pixel'] < 3:
        return None, 'already paletted'

    # SINGLE output format for every conversion: 1 palette x 256 colors,
    # <=cap dimensions, standard 8-bit header. This is the only paletted
    # layout hardware-proven to render in the Switch battle module
    # (the mod's own Vincent/Aerith textures, and every correct enemy in
    # the v4 build). Mirroring vanilla 16-color / multi-palette layouts
    # produced BLACK models in every build that tried it (v4, v7),
    # regardless of pixel content, so vanilla palette structure is
    # deliberately ignored.
    w, h, bypp = t['width'], t['height'], t['bytes_per_pixel']
    nw, nh = min(w, cap), min(h, cap)
    rgba = _resample_rgba(t['pixels'], w, h, bypp, nw, nh)
    palette, mapping = _median_cut(rgba, 255)
    idx = bytearray(0 if p[3] < 128 else mapping[p] + 1 for p in rgba)
    entries = [(0, 0, 0, 0)] + palette
    entries += [(0, 0, 0, 255)] * (256 - len(entries))
    pal_bytes = bytearray()
    for r, g, b, a in entries:
        pal_bytes += bytes((b, g, r, a))              # BGRA on disk
    hdr = _paletted_header(data, nw, nh, 1, 256)
    out = bytes(hdr) + bytes(pal_bytes) + bytes(idx)
    note = (f'{w}x{h}x{bypp * 8}bit -> {nw}x{nh} paletted '
            f'({len(palette) + 1}/256 colors) '
            f'{len(data):,} -> {len(out):,} bytes')
    return out, note

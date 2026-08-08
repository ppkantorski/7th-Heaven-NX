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


def _resample_raw(pixels, w, h, bytespp, nw, nh):
    """Nearest-neighbour resample of raw per-pixel blocks (palette indices
    OR truecolor bytes -- whatever `bytespp` already is). Unlike
    _resample_rgba, this does not decode/re-encode colour: it copies each
    source pixel's bytes verbatim to its new position, so a paletted
    texture stays paletted against the SAME palette and a truecolor
    texture keeps its exact channel layout. Safe for any bytespp."""
    out = bytearray(nw * nh * bytespp)
    for y in range(nh):
        sy = y * h // nh
        srow = sy * w * bytespp
        drow = y * nw * bytespp
        for x in range(nw):
            sx = srow + (x * w // nw) * bytespp
            dx = drow + x * bytespp
            out[dx:dx + bytespp] = pixels[sx:sx + bytespp]
    return bytes(out)


def _cap_size(w, h, max_dim):
    """
    New (width, height) for a texture capped at `max_dim`.

    Two rules, both of which the first version of this function broke:

    UNIFORM. Both axes are divided by the SAME factor. `min(w, cap)` per axis
    is not a downscale, it is an anamorphic squash: a 1024x512 sheet came out
    512x512, i.e. horizontally compressed 2:1 while its height was untouched.
    FF7 .p files store texture coordinates as normalised floats (see FFNx's
    `struct texcoords { float u; float v; }`), so nothing goes out of range --
    the art just renders wrong, and worst on the polygons with the least
    texture area to spare, which is exactly where a stray-looking smear shows
    up on a small model.

    POWER OF TWO. The factor is a power of two, so a nearest-neighbour
    resample lands on exact source pixels and is a clean 2x/4x decimation
    rather than an irregular one that drops rows unevenly. It also keeps
    power-of-two sources power-of-two, which non-uniform capping did not
    (1024x768 -> 512x512 turned a legal 4:3 sheet into a square one).

    Returns (w, h) unchanged when nothing needs to happen.
    """
    if w <= max_dim and h <= max_dim:
        return w, h
    # Scale both axes by the SAME ratio, so the longer one lands exactly on
    # `max_dim`. For a power-of-two cap and a power-of-two source this is bit
    # for bit what the old halving loop produced (1024 -> 512 -> 256), so no
    # existing build moves; what it adds is caps that are not powers of two.
    # `Cap at 768px` used to quietly give 512, because halving cannot reach
    # 768 -- the number in the menu has to be the number on the texture.
    #
    # The cost is that a non-power-of-two ratio makes the nearest-neighbour
    # resample drop rows unevenly rather than decimate cleanly. That is a real
    # trade and it is the one the setting is asking for; the power-of-two
    # values are still there for anyone who wants the clean decimation.
    longest = max(w, h)
    return (max(1, w * max_dim // longest),
            max(1, h * max_dim // longest))


def cap_dimensions(data, max_dim):
    """
    Downscale a TEX file so neither dimension exceeds `max_dim`, preserving
    its exact original format: paletted stays paletted (same palette,
    resampled indices), truecolor stays truecolor (same bit depth,
    resampled channels). No quantization and no header layout changes beyond
    the fields that DESCRIBE the new pixel block.

    Aspect ratio is preserved -- see _cap_size for why that is not optional.

    The pitch field (0x44) is rewritten when, and only when, the source
    carries a nonzero one. Every vanilla field TEX on hand has pitch=0 at
    every size (all 32x32 in char.lgp), so the field module plainly does not
    need it, and a zero is passed through untouched to stay byte-compatible
    with vanilla. But a mod tool that DOES fill it in has written
    `width * bytes_per_pixel`, and leaving that stale after halving the width
    describes a row stride twice the real one: any consumer that honours it
    reads the image sheared. Rewriting a nonzero pitch keeps the header
    self-consistent whichever way the loader goes.

    Returns (new_bytes, note) or (None, reason) -- reason is 'not a TEX'
    or 'already within cap' when nothing changes.
    """
    t = parse(data)
    if t is None:
        return None, 'not a TEX'
    w, h, bypp = t['width'], t['height'], t['bytes_per_pixel']
    nw, nh = _cap_size(w, h, max_dim)
    if (nw, nh) == (w, h):
        return None, 'already within cap'
    new_pixels = _resample_raw(t['pixels'], w, h, bypp, nw, nh)
    hdr = bytearray(data[:HEADER_LEN])
    struct.pack_into('<I', hdr, O_WIDTH, nw)
    struct.pack_into('<I', hdr, O_HEIGHT, nh)
    pitch = _u32(data, O_PITCH)
    if pitch:
        struct.pack_into('<I', hdr, O_PITCH, nw * bypp)
    out = bytes(hdr) + t['palette'] + new_pixels
    kind = 'paletted' if t['palette_flag'] else f'{bypp * 8}-bit truecolor'
    note = (f'{w}x{h} -> {nw}x{nh} ({kind}, aspect preserved) '
            f'{len(data):,} -> {len(out):,} bytes')
    if pitch:
        note += f'; pitch {pitch} -> {nw * bypp}'
    return out, note


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
    # deliberately ignored. (Dimensions are a separate question from
    # palette layout -- see below -- and this paragraph is only about
    # the latter.)
    w, h, bypp = t['width'], t['height'], t['bytes_per_pixel']

    # Target dimensions. `vanilla_data` names the actual slot this texture
    # is replacing (e.g. LGP entry "oxae"), and its aspect ratio -- not
    # the mod's own source art -- is what the game's UV mapping for that
    # slot was built against. Prefer it when available: cap vanilla's own
    # (w, h) uniformly (see _cap_size docstring on why per-axis min() is
    # an anamorphic squash, not a downscale), then upscale that by
    # doubling in lockstep as long as the result still fits under `cap`
    # AND is actually resolvable from the source (no upscaling past what
    # the mod's own art provides). This matches the mod's own aspect in
    # the overwhelming majority of cases (a straight upscale preserves
    # proportions by construction) while remaining correct even when a
    # mod ships a differently-shaped replacement for one slot -- e.g.
    # Avalanche Arisen's "raac" is a 512x512 square replacing a 128x256
    # portrait vanilla texture; without this, the square source's own
    # aspect would still be used and the result would render stretched.
    van_t = parse(vanilla_data) if vanilla_data else None
    if van_t and van_t['width'] and van_t['height']:
        nw, nh = _cap_size(van_t['width'], van_t['height'], cap)
        # Scale up by the largest WHOLE factor that still fits the cap and is
        # still resolvable from the source. This used to double, which meant a
        # 256px vanilla tile could only ever reach 256, 512 or 1024 -- so
        # `Cap at 768px` silently produced 512 and the setting looked broken.
        # An integer factor keeps the aspect exact (both axes scale by the
        # same whole number) and is identical to doubling whenever the cap IS
        # a power of two, so no existing build changes.
        if nw > 0 and nh > 0:
            k = min(cap // nw, cap // nh, w // nw, h // nh)
            if k > 1:
                nw *= k
                nh *= k
    else:
        nw, nh = _cap_size(w, h, cap)
    rgba = _resample_rgba(t['pixels'], w, h, bypp, nw, nh)
    palette, mapping = _median_cut(rgba, 255)
    idx = bytearray(0 if p[3] < 128 else mapping[p] + 1 for p in rgba)
    # The reserved transparent entry gets the average of the opaque colours
    # that BORDER transparency, not black. Once the palette is expanded to
    # RGBA the GPU cannot tell index 0 apart, so it bilinear-filters straight
    # through it; with black there it draws a dark line along every boundary
    # in the atlas, which is what the seams down faces and shoulders were.
    # Alpha stays 0, transparency is still decided by the colour-key flag and
    # the index, and not one index byte below changes. See tex.debleed().
    clear = (0, 0, 0)
    _pal_bytes = bytearray()
    for r, g, b, a in palette:
        _pal_bytes += bytes((b, g, r, a))
    edge = _boundary_colour(idx, nw, nh, b'\x00\x00\x00\x00' + bytes(_pal_bytes),
                            len(palette) + 1, 0)
    if edge is not None:
        clear = edge
    entries = [(clear[0], clear[1], clear[2], 0)] + palette
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


# ==========================================================================
# COLOUR-KEY DE-FRINGING
# ==========================================================================
def _boundary_colour(px, w, h, palette, n_colors, pal_index=0):
    """
    Average of the opaque texels that orthogonally touch a transparent one.

    That set is exactly the set of colours bilinear filtering will mix with
    palette entry 0 along a boundary, so their mean is the single value that
    makes the mixing invisible. Returns None when the texture has no such
    boundary, i.e. nothing to fix.
    """
    base = pal_index * n_colors * 4
    tot_r = tot_g = tot_b = cnt = 0
    for y in range(h):
        row = y * w
        for x in range(w):
            if px[row + x]:
                continue
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                i = px[ny * w + nx]
                if not i:
                    continue
                o = base + i * 4
                if o + 3 >= len(palette):
                    continue
                tot_b += palette[o]
                tot_g += palette[o + 1]
                tot_r += palette[o + 2]
                cnt += 1
    if not cnt:
        return None
    return (tot_r // cnt, tot_g // cnt, tot_b // cnt)


def debleed(data):
    """
    Kill the black fringe at colour-key edges, WITHOUT touching a pixel.

    THE PROBLEM. FF7 character textures are atlases whose sub-regions are
    separated by transparent gutters, and the transparent palette entry is
    black. Measured on the vanilla archive:

        694 char.lgp TEX files, all paletted
        626 with colorkey=1, and in every one palette entry 0 is RGB(0,0,0)
        458 of 624 (73%) have a transparent texel touching an opaque one

    Once the palette is expanded to RGBA the GPU has no idea index 0 is
    special. It bilinear-filters, and along every one of those boundaries it
    interpolates toward black. A face whose halves sit either side of a gutter
    gets a thin dark line down the middle; a shoulder gets a vertical one.
    Point sampling on the original hardware never showed it; an upscaled
    model under a filtering renderer shows it clearly, because the fringe is
    one texel wide and the texel is now large.

    THE FIX. Give entry 0 the average colour of the opaque texels that border
    transparency, and leave its alpha at 0. Filtering then blends toward the
    art's own edge colour instead of black.

    WHY IT CANNOT COMPROMISE THE IMAGE. Transparency here is decided by the
    colorkey FLAG plus the index, not by the colour -- the same archive
    carries 68 textures with colorkey=0 whose entry 0 is opaque white and
    draws normally. So entry 0's RGB is read only when it is being blended
    toward, never when it is being drawn. This function enforces that rather
    than claiming it: it refuses unless colorkey is set, and it rewrites
    nothing but palette entry 0. Every index byte, every other palette entry,
    and the whole header come out identical, which `check_indices_unchanged`
    below verifies byte for byte.

    Returns (new_bytes, note) or (None, reason).
    """
    t = parse(data)
    if t is None:
        return None, 'not a TEX'
    if not t['palette_flag']:
        return None, 'not paletted'
    if not _u32(data, O_COLORKEY):
        # entry 0 is a real, drawable colour here -- changing it WOULD change
        # the image, which is the one case this must never touch
        return None, 'no colour key'
    n_colors = t['colors_per_palette']
    n_pal = max(1, t['num_palettes'])
    if not n_colors:
        return None, 'no palette'
    px = t['pixels']
    w, h = t['width'], t['height']
    if 0 not in px:
        return None, 'no transparent texels'

    pal = bytearray(t['palette'])
    changed = []
    for p in range(n_pal):
        rgb = _boundary_colour(px, w, h, pal, n_colors, p)
        if rgb is None:
            continue
        o = p * n_colors * 4
        if o + 3 >= len(pal):
            continue
        if pal[o + 3]:            # entry 0 is opaque in this palette; leave it
            continue
        if (pal[o + 2], pal[o + 1], pal[o]) == rgb:
            continue
        pal[o] = rgb[2]           # B
        pal[o + 1] = rgb[1]       # G
        pal[o + 2] = rgb[0]       # R
        changed.append(p)         # alpha at pal[o + 3] is untouched
    if not changed:
        return None, 'nothing to debleed'
    out = (bytes(data[:HEADER_LEN]) + bytes(pal)
           + bytes(data[HEADER_LEN + len(pal):]))
    return out, ('%d palette(s) de-fringed' % len(changed))


def check_indices_unchanged(before, after):
    """
    True when `after` differs from `before` in palette bytes only.

    `debleed`'s whole claim is that it cannot alter the image. This is that
    claim as an assertion: same header, same pixel indices, same length.
    """
    if len(before) != len(after):
        return False
    if before[:HEADER_LEN] != after[:HEADER_LEN]:
        return False
    t = parse(before)
    if t is None:
        return False
    pal_end = HEADER_LEN + t['palette_size'] * 4
    return before[pal_end:] == after[pal_end:]

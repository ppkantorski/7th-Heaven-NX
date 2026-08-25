#!/usr/bin/env python3
"""
test_fieldbg.py -- self-tests for ff7nx_fieldbg.py and field_bg_native.py.

The module tests need a stock `exefs/main`; point SEVENTH_NX_TEST_NSO at it
(or pass --nso). The data tests need a stock flevel.lgp; SEVENTH_NX_TEST_FLEVEL
or --flevel. Each group is skipped, loudly, if its input is missing.

    python3 test_fieldbg.py --nso dump/exefs/main --flevel flevel.lgp
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ff7nx_fieldbg as FB          # noqa: E402
import field_bg_native as FN        # noqa: E402

FAILED = []
KNOWN_UNPARSEABLE = ('blackbgb', 'blackbgb.xone')


def check(name, cond, detail=''):
    print('  %-58s %s%s' % (name, 'ok' if cond else 'FAIL',
                            '' if cond else '   ' + detail))
    if not cond:
        FAILED.append(name)


# ------------------------------------------------------------ encoding
def test_encoding():
    print('encoding')
    # every replacement must round-trip through capstone, not just through
    # our own encoder.
    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    except ImportError:
        print('  (capstone missing -- skipping disassembly round-trip)')
        return
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)

    def dis(word):
        for i in md.disasm(struct.pack('<I', word), 0):
            return '%s %s' % (i.mnemonic, i.op_str)
        return '<undefined>'

    want = {
        512: {
            FB.SITE_ALLOC_ELEM: 'mov w25, #8',
            FB.SITE_READ_BYTES: 'mov w27, #0x80000',
            FB.SITE_PREALLOC:   'mov w28, #8',
            FB.SITE_CVT_BOUND:  'sub w9, w8, #0x40, lsl #12',
            FB.SITE_SURF_WH:    'mov w19, #0x200',
            FB.SITE_SURF_PITCH: 'mov w8, #0x400',
        },
        1024: {
            FB.SITE_ALLOC_ELEM: 'mov w25, #0x20',
            FB.SITE_READ_BYTES: 'mov w27, #0x200000',
            FB.SITE_PREALLOC:   'mov w28, #0x20',
            FB.SITE_CVT_BOUND:  'sub w9, w8, #0x100, lsl #12',
            FB.SITE_SURF_WH:    'mov w19, #0x400',
            FB.SITE_SURF_PITCH: 'mov w8, #0x800',
        },
    }
    for px, expect in want.items():
        got = {k: v for k, v in FB.words(px).items()
               if k not in FB.FX_BLEND_SITES}
        check('%dpx: site count' % px, len(got) == len(expect),
              '%d vs %d' % (len(got), len(expect)))
        for off, text in expect.items():
            check('%dpx: +0x%X disassembles as %r' % (px, off, text),
                  off in got and dis(got[off][1]) == text,
                  dis(got[off][1]) if off in got else 'missing')
    # 256 needs no SIZE words, but the scoped truecolor FX pages still need
    # the additive depth-2 ladder.
    check('256px produces only the FX blend ladder',
          set(FB.words(256)) == set(FB.FX_BLEND_SITES))
    # the bleed word only appears when asked for
    check('bleed off by default', FB.SITE_BLEED not in FB.words(512, False))
    check('bleed on when asked', FB.SITE_BLEED in FB.words(512, True))
    b = FB.words(512, True)[FB.SITE_BLEED][1]
    check('bleed halves the exponent', b == 0x52B74C16, '%08X' % b)


# -------------------------------------------------------------- module
def test_module(nso):
    print('module (%s)' % nso)
    check('all 512px originals present and w23 intact',
          FB.verify_module(nso, lambda *a: None, 512))
    check('all 1024px originals present',
          FB.verify_module(nso, lambda *a: None, 1024))

    # the depth-1 constant must NOT be in any patch set, at any size
    for px in (512, 1024):
        check('%dpx leaves w23 (#0x10000) alone' % px,
              FB.SITE_W23_KEEP not in FB.words(px))

    # patch it for real and diff the .text word by word
    import tempfile
    import nxmap
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'main')
        ok = FB.apply_to_nso(nso, out, lambda *a: None, 512)
        check('apply_to_nso writes a module', ok and os.path.exists(out))
        if not ok:
            return
        a, b = nxmap.Main(nso), nxmap.Main(out)
        diff = [i for i in range(0, len(a.text), 4)
                if a.text[i:i + 4] != b.text[i:i + 4]]
        check('exactly the intended words changed',
              set(diff) == set(FB.words(512)),
              '%d changed' % len(diff))
        check('.rodata untouched', a.raw[1] == b.raw[1])
        check('.data untouched', a.raw[2] == b.raw[2])
        check('patched module refuses to be patched twice',
              not FB.verify_module(out, lambda *a: None, 512))


# ---------------------------------------------------------------- data
def test_data(flevel):
    print('data (%s)' % flevel)
    import lgp
    arc = lgp.Archive(flevel)
    fields = parsed = rt = walked = 0
    resized_pages = 0
    growth = 0
    promotable = 0
    tiles = 0
    for name in sorted(arc.index):
        e = arc.index[name]
        if not arc.is_field(e):
            continue
        try:
            secs = lgp.split_sections(arc.decompressed(e))
        except Exception:                                      # noqa: BLE001
            continue
        fields += 1
        s9 = secs[8]
        try:
            pages, s, en = FN.parse_texture_block(s9)
        except FN.Section9Error:
            continue
        parsed += 1
        if FN.replace_texture_block(s9, pages, s, en) == s9:
            rt += 1
        try:
            spans = FN._layer_tile_spans(s9, s9.find(b'BACK'), s)
            walked += 1
            tiles += len(spans)
        except FN.Section9Error:
            pass
        if FN.plan_promotion(pages) is not None:
            promotable += 1
        new, k = FN.resize_section9(s9, 512)
        resized_pages += k
        growth += len(new) - len(s9)
        if k:
            p2, s2, e2 = FN.parse_texture_block(new, 512)
            assert all(p.px == 512 for p in p2 if p and p.depth == 2)
            # nearest 2x must decimate exactly back to the original
            for p in p2:
                if p is None or p.depth != 2:
                    continue
                src = struct.unpack('<%dH' % (512 * 512), p.data)
                dec = [src[(2 * y) * 512 + 2 * x]
                       for y in range(256) for x in range(256)]
                orig = list(struct.unpack('<65536H', pages[p.slot].data))
                assert dec == orig, (name, p.slot)

    # blackbgb / blackbgb.xone leave 148 and 134 trailing bytes after the
    # 42nd slot instead of the <= 64 the walk allows. build.py's own
    # _bg_texture_pages rejects them too. They are the black transition
    # screens; leaving them vanilla costs nothing.
    check('every field file parsed as a TEXTURE block, bar the two known',
          parsed >= fields - len(KNOWN_UNPARSEABLE),
          '%d/%d' % (parsed, fields))
    check('TEXTURE round-trips byte for byte', rt == parsed,
          '%d/%d' % (rt, parsed))
    check('layer walk lands exactly on TEXTURE', walked == parsed,
          '%d/%d  (%d tiles)' % (walked, parsed, tiles))
    check('resize is exactly reversible by decimation', True)
    print('  ... %d depth-2 pages resized, +%.1f MB, %d/%d fields promotable'
          % (resized_pages, growth / 1048576.0, promotable, parsed))


# -------------------------------------------------------------- pixels
def test_pixels():
    print('pixels')
    check('white', FN.rgb_to_565(255, 255, 255) == 0xFFDF)
    check('red', FN.rgb_to_565(255, 0, 0) == 0xF800)
    check('green', FN.rgb_to_565(0, 255, 0) == 0x07C0)
    check('blue', FN.rgb_to_565(0, 0, 255) == 0x001F)
    check('transparent becomes the empty key',
          FN.rgb_to_565(9, 9, 9, 0) == FN.EMPTY)
    check('opaque black never becomes the empty key',
          FN.rgb_to_565(0, 0, 0, 255) == FN.NEAR_BLACK)
    check('near-black is below one 8-bit step',
          (FN.NEAR_BLACK >> 11) == 0 and ((FN.NEAR_BLACK >> 5) & 63) <= 2)

    # THE ONE THAT MATTERS. The engine's 565 -> 1555 converter, x86 0x63F350,
    # runs on every depth-2 pixel when the surface is not 5:6:5 -- and it
    # masks green with 0x07E0 instead of 0x07C0, so green's low bit is ORed
    # onto blue's top bit. Every value we emit must have that bit clear, or
    # blue gains 16/31 at random and the whole picture goes blue and noisy.
    conv = lambda v: ((v & 0xF800) >> 1) | ((v & 0x07E0) >> 1) | (v & 0x1F)
    bad = []
    for r in range(0, 256, 5):
        for g in range(0, 256, 5):
            for b in range(0, 256, 5):
                v = FN.rgb_to_565(r, g, b)
                if v & 0x0020:
                    bad.append((r, g, b, v))
                    break
                if v == FN.NEAR_BLACK:
                    # the deliberate nudge off 0x0000: opaque black has to
                    # stay non-zero or the engine reads it as transparent and
                    # it stops occluding. NEAR_BLACK is one unit of BLUE
                    # precisely because that is the dimmest way to do it, so
                    # blue legitimately differs from the source here.
                    continue
                # the quantiser ROUNDS now (onto the level*8 grid the
                # engine reconstructs), so the expectation is round(b/8),
                # not the old truncating b >> 3
                if (conv(v) & 0x1F) != min(31, int(b * 0.125 + 0.5)):
                    bad.append((r, g, b, v))
                    break
            if bad:
                break
        if bad:
            break
    check('green LSB is always clear, so the engine keeps blue intact',
          not bad, str(bad[:1]))
    check('near-black survives the engine 565->1555 step',
          conv(FN.NEAR_BLACK) != 0)
    # nearest resize on a tiny synthetic page
    src = struct.pack('<4H', 0x1111, 0x2222, 0x3333, 0x4444)   # 2x2
    got = FN.resize_depth2(src, 2, 4)
    want = struct.pack('<16H',
                       0x1111, 0x1111, 0x2222, 0x2222,
                       0x1111, 0x1111, 0x2222, 0x2222,
                       0x3333, 0x3333, 0x4444, 0x4444,
                       0x3333, 0x3333, 0x4444, 0x4444)
    check('2x nearest upscale', got == want)
    check('1x is identity', FN.resize_depth2(src, 2, 2) == src)


# -------------------------------------------------------------- remap
def test_remap():
    print('slot remap')
    mk = lambda slots, depth=1: [
        (FN.Page(i, 0, depth, b'\0' * (256 * 256 * depth), 256)
         if i in slots else None) for i in range(42)]
    # 5 opaque pages fit the 7 depth-2 opaque slots
    plan = FN.plan_promotion(mk({0, 1, 2, 3, 4}))
    check('5 opaque pages fit', plan == {0: 0x1A, 1: 0x1B, 2: 0x1C,
                                         3: 0x1D, 4: 0x1E}, str(plan))
    # 8 do not
    check('8 opaque pages do not fit',
          FN.plan_promotion(mk(set(range(8)))) is None)
    # additive group maps into 0x21..
    plan = FN.plan_promotion(mk({0, 0x0F, 0x10}))
    check('blend groups are preserved',
          plan == {0: 0x1A, 0x0F: 0x21, 0x10: 0x22}, str(plan))
    # an existing depth-2 page consumes capacity in its own group
    pages = mk({0, 1, 2, 3, 4, 5, 6})
    pages[0x1A] = FN.Page(0x1A, 0, 2, b'\0' * (256 * 256 * 2), 256)
    check('an existing depth-2 page takes a slot',
          FN.plan_promotion(pages) is None)


# --------------------------------------------------------------- repack
RP_DITHER_ENV = 'SEVENTH_NX_FIELD_BG_NO_DITHER'


def _packed(r, g, b, a=255):
    """
    Exactly what the packer writes for one pixel, so the round-trip test
    asserts the CURRENT contract instead of the old one.

    Two things differ from FN.rgb_to_565, and both were bugs it enshrined:

      * it ROUNDS onto the level*8 grid the engine reconstructs (0x63F350
        widens 565 -> 1555, and 5 -> 8 bits is << 3, so level 31 comes back as
        248). FN.rgb_to_565 truncates, biasing every page dark by ~3.5/255.
      * black stays 0x0000. FN.rgb_to_565 nudges it to NEAR_BLACK (0x0040 =
        R0 G8 B0, a dark GREEN) because EMPTY used to double as the
        transparency sentinel. It does not any more -- the opacity gate reads
        the art's alpha -- and that nudge was 17.4% of every truecolor pixel
        in nmkin_1.
    """
    if a < 8:
        return FN.EMPTY
    q = lambda c: min(31, max(0, int(c * 0.125 + 0.5)))
    return (q(r) << 11) | ((q(g) << 1) << 5) | q(b)


def test_repack(flevel):
    """
    End to end: synthesise an .iro of field art, run the real ArtProvider /
    IroReader / PageArt / repack over real fields, then check that EVERY
    rewritten tile samples the cell it used to sample.

    The BC7 decoder is stubbed -- it is texture2ddecoder's, already exercised
    by the battle background path -- but everything downstream of it is the
    shipping code.
    """
    print('repack (%s)' % flevel)
    # This test is about GEOMETRY -- does a relocated tile still sample the
    # cell it used to. Ordered dithering makes the packed value depend on the
    # pixel's Bayer position, which would make an exact colour comparison
    # meaningless, so it is switched off for the duration and asserted
    # separately below.
    import os as _os
    _saved_dither = _os.environ.get(RP_DITHER_ENV)
    _os.environ[RP_DITHER_ENV] = '1'
    import collections
    import lgp
    import iro
    import field_bg_repack as RP
    import dds_decode

    arc = lgp.Archive(flevel)
    names = [n for n in sorted(arc.index) if arc.is_field(arc.index[n])][:40]
    want = collections.defaultdict(set)
    for nm in names:
        try:
            s9 = lgp.split_sections(arc.decompressed(arc.index[nm]))[8]
            pages, ts, _te = FN.parse_texture_block(s9)
            spans = FN._layer_tile_spans(s9, s9.find(b'BACK'), ts)
        except Exception:                                      # noqa: BLE001
            continue
        pmap = {p.slot: p for p in pages if p}
        for off in spans:
            p = pmap.get(s9[off + RP.T_TEXID])
            if p and p.depth == 1 and not p.size_flag:
                want[nm].add((p.slot, s9[off + RP.T_PALETTE]))

    # --- a minimal but real .iro
    payloads = [('LIMIT BREAK\\field\\%s\\%s_%02d_%d.dds' % (nm, nm, pg, pal),
                 struct.pack('<HH', pg, pal))
                for nm, pairs in want.items() for pg, pal in sorted(pairs)]
    HDR = 20
    data = bytearray()
    recs = []
    for name, blob in payloads:
        recs.append((name, 0, HDR + len(data), len(blob)))
        data += blob
    out = bytearray(iro.SIG + struct.pack('<iiii', 0x10001, 0,
                                          HDR + len(data), len(recs)))
    out += data + b'\0\0\0\0'
    for name, fl, off, sz in recs:
        n = name.encode('utf-16-le')
        body = (struct.pack('<H', len(n)) + n + struct.pack('<I', fl)
                + struct.pack('<qi', off, sz))
        out += struct.pack('<H', len(body) + 2) + body
    import tempfile
    fd, iro_path = tempfile.mkstemp(suffix='.iro')
    os.write(fd, bytes(out))
    os.close(fd)

    def rgb(pg, pal, cx, cy):
        return ((pg * 11 + cx * 13) % 256, (pal * 17 + cy * 19) % 256,
                (cx * 7 + cy * 23) % 256)

    real_decode = dds_decode.decode_dds

    def stub(b):
        pg, pal = struct.unpack('<HH', b[:4])
        row = bytearray(1024 * 4)
        img = bytearray()
        for cy in range(16):
            for x in range(1024):
                r, g, bl = rgb(pg, pal, x // 64, cy)
                row[x * 4:x * 4 + 4] = bytes((r, g, bl, 255))
            img += bytes(row) * 64
        return bytes(img), 1024, 1024

    dds_decode.decode_dds = stub
    try:
        prov = RP.ArtProvider([(iro_path, None)], 512, lambda *a: None)
        dangling = dropped = 0
        check('provider indexed every slot',
              len(prov.slots) == len(payloads) and prov.ambiguous == 0,
              '%d/%d' % (len(prov.slots), len(payloads)))
        ok = mismatches = 0
        fields = pages_up = cells = 0
        for nm in sorted(want):
            s9 = lgp.split_sections(arc.decompressed(arc.index[nm]))[8]
            new, _k = FN.resize_section9(s9, 512)
            new, st = RP.repack_section9(new, nm, prov.open(nm), 512,
                                         src_px=512)
            prov.close()
            if not st:
                continue
            fields += 1
            pages_up += st.pages_upgraded
            cells += st.cells
            dropped += st.pages_dropped
            pages, ts, _ = FN.parse_texture_block(new, 512)
            pmap = {p.slot: p for p in pages if p}
            spans = FN._layer_tile_spans(new, new.find(b'BACK'), ts)
            for off in spans:
                if new[off + RP.T_TEXID] not in pmap:
                    dangling += 1
                fx = new[off + RP.T_FX_PAGE]
                if fx and fx not in pmap:
                    dangling += 1
            _op, ots, _ = FN.parse_texture_block(s9)
            o_spans = FN._layer_tile_spans(s9, s9.find(b'BACK'), ots)
            for off, ooff in zip(spans, o_spans):
                old_slot = s9[ooff + RP.T_TEXID]
                new_slot = new[off + RP.T_TEXID]
                if new_slot == old_slot:
                    continue
                ou, ov = struct.unpack_from('<II', s9, ooff + RP.T_SRC_X_BIG)
                ocx, ocy = round(ou / 1e7 * 16), round(ov / 1e7 * 16)
                pal = s9[ooff + RP.T_PALETTE]
                nu, nv = struct.unpack_from('<II', new, off + RP.T_SRC_X_BIG)
                if nu % 625000 or nv % 625000:
                    mismatches += 1
                    break
                ncx, ncy = nu // 625000, nv // 625000
                p = pmap[new_slot]
                if p.depth != 2 or p.px != 512:
                    mismatches += 1
                    break
                got, = struct.unpack_from(
                    '<H', p.data, ((ncy * 32) * 512 + ncx * 32) * 2)
                if got != _packed(*rgb(old_slot, pal, ocx, ocy)):
                    mismatches += 1
                    break
                ok += 1
        check('every rewritten tile samples its original cell',
              mismatches == 0 and ok > 5000,
              '%d verified, %d wrong' % (ok, mismatches))
        # A freed page must be freed only when it is truly unreferenced. A
        # dangling texture_id would point the draw at an absent page, which is
        # the very failure this whole pass exists to remove.
        check('no tile points at a page that was freed',
              dangling == 0, '%d dangling reference(s)' % dangling)
        check('freeing actually happened', dropped > 0,
              '%d page(s) freed' % dropped)
        print('  ... %d field(s), %d page(s) upgraded, %d cell(s)'
              % (fields, pages_up, cells))
    finally:
        dds_decode.decode_dds = real_decode
        os.unlink(iro_path)
        if _saved_dither is None:
            os.environ.pop(RP_DITHER_ENV, None)
        else:
            os.environ[RP_DITHER_ENV] = _saved_dither


def test_quantiser():
    """
    The 565 quantiser's contract. Three separate things were wrong here and
    each one is pinned:

      * it TRUNCATED, biasing every page dark;
      * it targeted level*255/31 when the engine reconstructs level*8, which
        no amount of dithering can correct because it is a scale error;
      * it nudged black to NEAR_BLACK (R0 G8 B0), which was 17.4% of every
        truecolor pixel in nmkin_1 and read as a grey-green wash over shadow.
    """
    print('565 quantiser')
    import field_bg_repack as RP
    try:
        import numpy as np
    except ImportError:
        print('  skipped -- needs numpy')
        return
    W = 256
    ramp = np.zeros((W, W, 4), np.uint8)
    ramp[..., 3] = 255
    for c in range(3):
        ramp[..., c] = np.linspace(0, 255, W, dtype=np.float32)[None, :]
    src = ramp[..., :3].astype(np.float32)

    def dec(buf):
        v = np.frombuffer(buf, dtype='<u2').reshape(W, W).astype(np.int32)
        return np.stack([(v >> 11) << 3, ((v >> 5) & 0x3F) << 2,
                         (v & 0x1F) << 3], -1).astype(np.float32)

    # DITHER_AMPLITUDE now gates this as well as the env var -- it is 0.0 by
    # default because the field is presented at 3x, where an 8x8 Bayer cell
    # becomes a solid 3x3 block and reads as a 6-pixel dot grid rather than as
    # dither. Force it on for the duration so the test still exercises the
    # code path it is about.
    saved = os.environ.get(RP_DITHER_ENV)
    saved_amp = RP.DITHER_AMPLITUDE
    RP.DITHER_AMPLITUDE = 1.0
    os.environ[RP_DITHER_ENV] = '1'
    flat = dec(RP.rgba_to_565_buf(ramp.tobytes(), W * W, W, black_ok=True))
    os.environ[RP_DITHER_ENV] = '0'
    dith = dec(RP.rgba_to_565_buf(ramp.tobytes(), W * W, W, black_ok=True))
    RP.DITHER_AMPLITUDE = saved_amp
    if saved is None:
        os.environ.pop(RP_DITHER_ENV, None)
    else:
        os.environ[RP_DITHER_ENV] = saved

    # Rounding: never more than half a step (4/255) off -- EXCEPT at the very
    # top, where the engine's <<3 expansion makes level 31 reconstruct as 248,
    # so pure white is unreachable by 7/255. That ceiling is the port's, not
    # the packer's, so the check excludes it and states it instead.
    e = flat - src
    below = np.abs(e[src <= 248.0]).max()
    check('rounds onto the level*8 grid (max err <= 4 below the ceiling)',
          below <= 4.0, '%.1f' % below)
    check('the only larger error is the engine ceiling (255 -> 248)',
          np.abs(e).max() <= 7.0, '%.1f' % np.abs(e).max())
    check('no systematic darkening (|bias| < 0.6)',
          abs(e.mean()) < 0.6, '%+.2f' % e.mean())

    # dithering: still within one step, and closer once the eye/upscale
    # averages a neighbourhood -- which is the whole point of it
    check('dither stays within one step (<= 8)',
          np.abs(dith - src).max() <= 8.0,
          '%.1f' % np.abs(dith - src).max())
    k = 4
    sm = lambda q: q[:W // k * k, :W // k * k].reshape(
        W // k, k, W // k, k, 3).mean(axis=(1, 3))
    p_flat = np.sqrt(((sm(flat) - sm(src)) ** 2).mean())
    p_dith = np.sqrt(((sm(dith) - sm(src)) ** 2).mean())
    check('dither beats plain rounding once averaged',
          p_dith < p_flat, 'dithered %.2f vs rounded %.2f' % (p_dith, p_flat))

    # black is black, and green's LSB is clear for every input
    # Opaque black must NOT pack as 0x0000: x86 0x6470E0 maps pixel 0 to 0,
    # i.e. transparent, and a transparent background pixel writes no
    # occlusion -- field models then draw straight through it. The engine
    # nudges opaque black to 0x421/0x821 for exactly this reason. What we
    # control is WHICH non-zero value, and it should be the dimmest one.
    blk = np.frombuffer(
        RP.rgba_to_565_buf(bytes([0, 0, 0, 255]) * 64, 64, 8), dtype='<u2')
    check('opaque black stays non-zero, so it still occludes',
          bool((blk != 0).all()), hex(int(blk[0])))
    lum = lambda v: (0.299 * (((v >> 11) & 0x1F) << 3)
                     + 0.587 * (((v >> 5) & 0x3F) << 2)
                     + 0.114 * ((v & 0x1F) << 3))
    check('NEAR_BLACK is the dimmest non-zero colour the format has',
          lum(FN.NEAR_BLACK) <= min(lum(1 << 11), lum(1 << 5), lum(1)) + 1e-9,
          '%.1f/255' % lum(FN.NEAR_BLACK))
    check('and much dimmer than the green one it replaced (0x0040)',
          lum(FN.NEAR_BLACK) * 4 < lum(0x0040),
          '%.1f vs 4.7' % lum(FN.NEAR_BLACK))
    rnd = np.random.default_rng(3).integers(0, 256, (64 * 64, 4), dtype=np.uint8)
    rnd[:, 3] = 255
    v = np.frombuffer(RP.rgba_to_565_buf(rnd.tobytes(), 64 * 64, 64),
                      dtype='<u2')
    check('green LSB clear for every input (the 0x07E0 engine bug)',
          int((((v >> 5) & 0x3F) & 1).sum()) == 0)


def test_art_opacity():
    """A cell the mod's art leaves transparent must NOT be upgraded."""
    print('art opacity')
    import field_bg_repack as RP
    try:
        import numpy as np
    except ImportError:
        np = None
    px = 512
    # Transparency is the ART'S ALPHA now, not a reserved colour value. One
    # cell transparent, and -- the case that motivated the change -- one cell
    # of OPAQUE PURE BLACK, which must still be upgraded. Under the old
    # EMPTY-as-sentinel rule black had to be nudged to NEAR_BLACK (0x0040,
    # R0 G8 B0) to avoid reading as transparent, and that dark-green nudge was
    # 17.4% of every truecolor pixel in nmkin_1.
    if np is not None:
        a = np.full((px, px), 0x1234, dtype='<u2')
        a[9 * 32:10 * 32, 7 * 32:8 * 32] = FN.NEAR_BLACK  # genuine black
        buf = a.tobytes()
        tm = np.zeros((px, px), dtype=bool)
        tm[5 * 32:6 * 32, 3 * 32:4 * 32] = True          # genuine transparent
    else:
        buf = bytes(struct.pack('<H', 0x1234) * (px * px))
        tm = bytearray(px * px)
        for y in range(5 * 32, 6 * 32):
            for x in range(3 * 32, 4 * 32):
                tm[y * px + x] = 1
        tm = bytes(tm)
    art = RP.PageArt.__new__(RP.PageArt)
    if np is not None:
        bm = np.zeros((px, px), dtype=bool)
        bm[9 * 32:10 * 32, 7 * 32:8 * 32] = True     # the pure-black cell
    else:
        bm = bytearray(px * px)
        for y in range(9 * 32, 10 * 32):
            for x in range(7 * 32, 8 * 32):
                bm[y * px + x] = 1
        bm = bytes(bm)
    art.px, art.buf, art.tmask, art.bmask, art._op = px, buf, tm, bm, {}
    _sv = os.environ.get(RP.TRUE_BLACK_ENV)
    os.environ[RP.TRUE_BLACK_ENV] = '0'      # isolate from the black rule
    check('a transparent cell is detected', not art.cell_opaque(3, 5, 16))
    check('an opaque cell is detected', art.cell_opaque(0, 0, 16))
    check('an opaque PURE BLACK cell is upgradable when the black rule is off',
          art.cell_opaque(7, 9, 16))
    # ... and is REFUSED once it is on, so it keeps its paletted page where
    # black is exact rather than taking a NEAR_BLACK lift
    os.environ[RP.TRUE_BLACK_ENV] = '0.25'
    art._op = {}
    check('a mostly-black cell keeps its paletted page (threshold on)',
          not art.cell_opaque(7, 9, 16))
    os.environ[RP.TRUE_BLACK_ENV] = '0'
    art._op = {}
    n = sum(0 if art.cell_opaque(cx, cy, 16) else 1
            for cy in range(16) for cx in range(16))
    check('exactly one cell is transparent', n == 1, str(n))
    # the pure-Python fallback must agree with numpy
    saved = RP._np
    RP._np = None
    art2 = RP.PageArt.__new__(RP.PageArt)
    tm2 = (bytes(bytearray(tm.reshape(-1).tolist())) if np is not None else tm)
    bm2 = (bytes(bytearray(bm.reshape(-1).tolist())) if np is not None else bm)
    art2.px, art2.buf, art2.tmask, art2.bmask, art2._op = \
        px, buf, tm2, bm2, {}
    same = all(art.cell_opaque(cx, cy, 16) == art2.cell_opaque(cx, cy, 16)
               for cy in range(16) for cx in range(16))
    RP._np = saved
    check('numpy and pure-Python paths agree', same)
    if _sv is None:
        os.environ.pop(RP.TRUE_BLACK_ENV, None)
    else:
        os.environ[RP.TRUE_BLACK_ENV] = _sv


def test_unkeyed_overlay_alpha():
    """Cosmos alpha survives when margin art consumed the old index-0 key."""
    print('unkeyed overlay alpha')
    import numpy as np
    import field_bg_dense as FD
    import field_bg_repack as RP

    class Page:
        depth = 1

    px = 512
    src = np.ones((256, 256), np.uint8)  # no index 0: rec['key'] is false
    src[0, 1] = 2
    pal = np.zeros((1, 256), np.uint16)
    pal[0, 1] = FN.NEAR_BLACK
    pal[0, 2] = np.uint16(0x7BEF)        # real colour must not become a hole

    art = RP.PageArt.__new__(RP.PageArt)
    buf = np.full((px, px), np.uint16(0x1234), np.uint16)
    tm = np.zeros((px, px), bool)
    # Two texels Cosmos leaves completely clear. The first falls back to an
    # already-black palette colour; the second falls back to real colour.
    buf[0, (0, 2)] = FN.EMPTY
    tm[0, (0, 2)] = True
    art.px, art.buf = px, buf.tobytes()
    art.tmask = tm
    art.bmask = np.zeros_like(tm)
    art.hmask = ~tm
    art.alpha = None
    art.amax = art.cmax = None
    art._op = {}

    rec = {'pal': 0, 'l2': True, 'key': False}
    st = FD.Stats()
    out = FD.source_cell((1, 0, 0, 0), rec, {1: Page()}, {1: src},
                         pal, lambda _page, _pal: art, None, st, scale=2)
    check('unkeyed layer-2 clear black texel regains the key',
          int(out[0, 0]) == FN.EMPTY, hex(int(out[0, 0])))
    check('unkeyed alpha cannot turn real colour into a hole without cover',
          int(out[0, 2]) != FN.EMPTY, hex(int(out[0, 2])))
    check('unkeyed alpha leaves opaque Cosmos art untouched',
          int(out[1, 1]) != FN.EMPTY, hex(int(out[1, 1])))
    cover = np.zeros((32, 32), bool)
    cover[0, 2] = True
    rec2 = dict(rec, cover=cover)
    out2 = FD.source_cell((1, 0, 0, 0), rec2, {1: Page()}, {1: src},
                          pal, lambda _page, _pal: art, None, FD.Stats(),
                          scale=2)
    check('per-texel cover licenses a clear non-black overlay texel',
          int(out2[0, 2]) == FN.EMPTY, hex(int(out2[0, 2])))
    check('unkeyed alpha is counted separately for the build log',
          getattr(FD.dense_repack, 'modclear_unkeyed_texels', 0) >= 1)


def test_stale_key_units():
    """Tiny atlas-edge keys yield to hard-opaque replacement coverage."""
    print('stale atlas-edge key units')
    import numpy as np
    import field_bg_dense as FD
    import field_bg_repack as RP

    class Page:
        depth = 1

    src = np.ones((256, 256), np.uint8)
    # The onna_5 signature: two joined key units on one cell edge.
    src[15:17, 0] = 0
    # A genuine clear unit must remain keyed.
    src[5, 5] = 0
    pal = np.zeros((1, 256), np.uint16)
    pal[0, 1] = np.uint16(0x1234)

    px = 768
    alpha = np.full((px, px), 255, np.uint8)
    alpha[15:18, 15:18] = 0
    art = RP.PageArt.__new__(RP.PageArt)
    art.px = px
    art.buf = np.full((px, px), np.uint16(0x1234)).tobytes()
    art.alpha = alpha
    art.tmask = alpha < 8
    art.hmask = alpha >= 128
    art.bmask = np.zeros((px, px), bool)
    art.amax = art.cmax = None
    art._op = {}

    rec = {'pal': 0, 'l2': True, 'l4': True, 'key': True}
    keep = FD.STALE_KEY_UNITS
    try:
        FD.STALE_KEY_UNITS = False
        old = FD.source_cell((1, 0, 0, 0), rec, {1: Page()}, {1: src},
                             pal, lambda _page, _pal: art, None, FD.Stats(),
                             scale=3, edge=32)
        st = FD.Stats()
        FD.STALE_KEY_UNITS = True
        new = FD.source_cell((1, 0, 0, 0), rec, {1: Page()}, {1: src},
                             pal, lambda _page, _pal: art, None, st,
                             scale=3, edge=32)
    finally:
        FD.STALE_KEY_UNITS = keep
    check('old path preserves the 3x6 vanilla rectangle',
          bool((old[45:51, 0:3] == FN.EMPTY).all()))
    check('hard-opaque art closes the two-unit seam sliver',
          bool((new[45:51, 0:3] != FN.EMPTY).all()))
    check('a source-transparent unit remains a real cut-out',
          bool((new[15:18, 15:18] == FN.EMPTY).all()))
    check('repair reports exactly two units / eighteen texels',
          (st.stale_key_cells, st.stale_key_units, st.stale_key_texels)
          == (1, 2, 18),
          repr((st.stale_key_cells, st.stale_key_units,
                st.stale_key_texels)))


# WITHDRAWN WITH THE WAIVER IT TESTED. FINDINGS-281.
#
# `test_opaque_parallax_atlas` covered the build-148 candidate that kept
# Cosmos's opaque DDS colour on all-zero 32-unit cells referenced only by
# layer 3, on the theory that fship_2's black edge bands were mis-keyed
# atlas placeholders. Hardware said the bands were unchanged: fship_2's
# layer 3 is authored x -160..160 and simply has no art in the 16:9 margin.
# The waiver and `field_bg_dense.parallax_backdrop_keys` are reverted, so
# the test goes with them rather than being left to fail.


def test_compact(flevel):
    """
    Compaction must free textures and change NOTHING a tile can see.

    The check is per tile and it is the whole claim: the block of pixels a
    tile samples after compaction must be byte-identical to the block it
    sampled before, its palette_ID must be unchanged, and if it draws from an
    fx page then the fx block must match too AND still sit under the same
    u,v. That last one is the constraint that would show up on hardware as an
    animated effect landing on the wrong square, so it is asserted rather
    than assumed.
    """
    print('compact (%s)' % flevel)
    import lgp
    import verify_compact as VC
    import field_bg_compact as FC

    arc = lgp.Archive(flevel)
    names = [n for n in sorted(arc.index) if arc.is_field(arc.index[n])]
    checked = compacted = saved = 0
    bad = []
    for nm in names[::7]:                        # every 7th: ~100 fields
        try:
            s9 = lgp.split_sections(arc.decompressed(arc.index[nm]))[8]
        except Exception:                                      # noqa: BLE001
            continue
        try:
            verdict, st, why = VC.check(nm, s9)
        except FN.Section9Error:
            continue                             # blackbgb, pre-existing
        except Exception as exc:                               # noqa: BLE001
            bad.append('%s: %r' % (nm, exc))
            continue
        checked += 1
        if verdict == 'FAIL':
            bad.append('%s: %s' % (nm, why))
        elif verdict == 'ok':
            compacted += 1
            saved += st.saved
    check('every tile samples identical pixels after compaction',
          not bad, '; '.join(bad[:3]))
    check('compaction actually happened somewhere', compacted > 0,
          '%d of %d fields' % (compacted, checked))

    # A page count can never go UP -- every cell came from a page in its own
    # bucket, so the bucket's own slots are always enough.
    grew = []
    for nm in names[::11]:
        try:
            s9 = lgp.split_sections(arc.decompressed(arc.index[nm]))[8]
            out, st = FC.compact_section9(s9)
        except Exception:                                      # noqa: BLE001
            continue
        if st.pages_after and st.pages_after > st.pages_before:
            grew.append(nm)
    check('compaction never increases the page count', not grew,
          ', '.join(grew[:5]))

    # The kill switch has to actually kill it.
    import os as _os
    _saved = _os.environ.get(FC.COMPACT_ENV)
    _os.environ[FC.COMPACT_ENV] = '0'
    try:
        check('SEVENTH_NX_FIELD_BG_COMPACT=0 disables it', not FC.enabled())
    finally:
        if _saved is None:
            _os.environ.pop(FC.COMPACT_ENV, None)
        else:
            _os.environ[FC.COMPACT_ENV] = _saved
    check('compaction is on by default', FC.enabled())
    print('  ... %d field(s) compacted, %d page(s) freed, %d checked'
          % (compacted, saved, checked))


def test_growth_mode():
    """The one GUI control maps onto the two switches, both directions."""
    print('growth mode')
    import field_bg_repack as RP
    env = {}
    RP.apply_growth_mode(0, env)
    check('Off sets neither', env[RP.REPLACE_ONLY_ENV] == '0'
          and env[RP.NO_GROWTH_ENV] == '0', repr(env))
    RP.apply_growth_mode(1, env)
    check('Replace only sets replace-only alone',
          env[RP.REPLACE_ONLY_ENV] == '1' and env[RP.NO_GROWTH_ENV] == '0',
          repr(env))
    RP.apply_growth_mode(2, env)
    check('No growth sets no-growth alone',
          env[RP.REPLACE_ONLY_ENV] == '0' and env[RP.NO_GROWTH_ENV] == '1',
          repr(env))
    RP.apply_growth_mode('nonsense', env)
    check('a junk value falls back to Off',
          env[RP.REPLACE_ONLY_ENV] == '0' and env[RP.NO_GROWTH_ENV] == '0',
          repr(env))


def test_resolve_base_dump():
    """A slot with several dumps of one page takes the BASE state."""
    print('ambiguous slot resolution')
    import field_bg_repack as RP
    F = 'md8_1'
    d = 'CosmosLimitBreak/field/%s/' % F
    idx = {
        (F, 3, 0): [d + F + '_03_0_9f3c2a1b.dds',
                    d + F + '_03_0.dds',
                    d + F + '_03_0_1a2b3c4d.dds'],
        # No base dump anywhere -- the old sort still has to decide.
        (F, 4, 0): [d + F + '_04_0_ff00ff00.dds',
                    d + F + '_04_0_00ff00ff.dds'],
        # The base dump is in an option folder that sorts LATER. This is the
        # case the old `sorted(v)[0]` got wrong: the path prefix dominated
        # the sort, so an animation frame beat the base state.
        (F, 6, 0): ['Aerith Upscales/field/%s/%s_06_0_9f3c2a1b.dds' % (F, F),
                    'Zack Upscales/field/%s/%s_06_0.dds' % (F, F)],
        (F, 5, 0): [d + F + '_05_0.dds'],
    }
    st = {}
    out = RP.resolve(idx, True, st)
    check('the base dump wins over animated states',
          out[(F, 3, 0)].endswith('_03_0.dds'), out[(F, 3, 0)])
    check('no base dump -> first by sorted name, as before',
          out[(F, 4, 0)].endswith('_04_0_00ff00ff.dds'), out[(F, 4, 0)])
    check('the base dump wins across option folders too',
          out[(F, 6, 0)].startswith('Zack'), out[(F, 6, 0)])
    check('an unambiguous slot is untouched',
          out[(F, 5, 0)].endswith('_05_0.dds'), out[(F, 5, 0)])
    check('stats count both outcomes',
          st == {'base': 2, 'arbitrary': 1}, repr(st))
    dropped = RP.resolve(idx, False)
    check('keep_ambiguous=False still drops every contested slot',
          set(dropped) == {(F, 5, 0)}, repr(sorted(dropped)))
    check('_is_base_dump rejects a hashed name',
          not RP._is_base_dump(d + F + '_03_0_1a2b3c4d.dds', F))
    check('_is_base_dump accepts the base name',
          RP._is_base_dump(d + F + '_03_0.dds', F))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', default=os.environ.get('SEVENTH_NX_TEST_NSO'))
    ap.add_argument('--flevel',
                    default=os.environ.get('SEVENTH_NX_TEST_FLEVEL'))
    a = ap.parse_args()
    test_encoding()
    test_pixels()
    test_quantiser()
    test_art_opacity()
    test_unkeyed_overlay_alpha()
    test_stale_key_units()

    test_remap()
    test_growth_mode()
    test_resolve_base_dump()
    if a.nso and os.path.exists(a.nso):
        test_module(a.nso)
    else:
        print('module tests SKIPPED -- pass --nso /path/to/exefs/main')
    if a.flevel and os.path.exists(a.flevel):
        test_data(a.flevel)
        test_repack(a.flevel)
        test_compact(a.flevel)
    else:
        print('data tests SKIPPED -- pass --flevel /path/to/flevel.lgp')
    print()
    if FAILED:
        print('%d FAILED: %s' % (len(FAILED), ', '.join(FAILED)))
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

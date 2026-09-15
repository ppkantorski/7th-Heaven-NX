#!/usr/bin/env python3
"""
Regression tests for the world_us.lgp truecolor -> native indexed pass.

The pass exists because vanilla world_us.lgp has no truecolor entry at all
(415 TEX files, every one 8-bit with a 16-colour palette) and this port's own
Gaia terrain converter emits 8-bit/256 for the same reason. The thirteen
truecolor entries in the shipped archive are all NinoStyle Chibi world model
skins, and `hw1.tex` -- the Highwind's -- is the only one that is ever large
on screen.

What these tests pin is that the conversion can only ever change the pixel
FORMAT:

  * dimensions are untouched (the cap already ran),
  * the keyed set is EXACTLY the engine's own truecolor colour-key test
    (colour bits all zero) -- no texel that is drawn stops being drawn and
    none that is transparent becomes visible,
  * a 32-bit source carrying graded alpha is REFUSED rather than flattened,
  * the switch is honoured, and "off" is byte-for-byte the old behaviour.

Run: python3 test_world_truecolor.py
"""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build                                                  # noqa: E402
import tex                                                    # noqa: E402

FAILED = []


def check(label, cond):
    print(('ok: ' if cond else 'FAIL: ') + label)
    if not cond:
        FAILED.append(label)


def make_truecolor(w, h, pixels_bgr, colorkey=1, bypp=3, alpha=None):
    """A minimal but real truecolor TEX, laid out like the NinoStyle ones."""
    hdr = bytearray(tex.HEADER_LEN)
    def put(off, val):
        struct.pack_into('<I', hdr, off, val)
    put(tex.O_VERSION, 1)
    put(tex.O_COLORKEY, colorkey)
    put(tex.O_MIN_BPC, 8); put(tex.O_MAX_BPC, 8)
    put(tex.O_MIN_BPP, bypp * 8); put(tex.O_MAX_BPP, bypp * 8)
    put(tex.O_WIDTH, w); put(tex.O_HEIGHT, h); put(tex.O_PITCH, w * bypp)
    put(tex.O_PAL_FLAG, 0)
    put(tex.O_BITS_PER_PIXEL, bypp * 8)
    put(tex.O_BYTES_PER_PIXEL, bypp)
    put(0x6C, 8); put(0x70, 8); put(0x74, 8)
    put(0x78, 8 if bypp == 4 else 0)
    put(0x7C, 0x00FF0000)
    # The NinoStyle exporter writes 0xFFFFFF00 here. FF7's own converter --
    # and this port's ARM64 copy of it -- happen to be immune to that, so it
    # is reproduced verbatim rather than "corrected" behind the mod's back.
    put(0x80, 0xFFFFFF00)
    put(0x84, 0x000000FF)
    put(0x88, 0xFF000000 if bypp == 4 else 0)
    put(0x8C, 16); put(0x90, 8); put(0x94, 0); put(0x98, 24)
    put(0xAC, 255); put(0xB0, 255); put(0xB4, 255)
    put(0xB8, 255 if bypp == 4 else 0)
    body = bytearray()
    for i, (b, g, r) in enumerate(pixels_bgr):
        body += bytes((b, g, r))
        if bypp == 4:
            body += bytes((255 if alpha is None else alpha[i],))
    return bytes(hdr) + bytes(body)


def grey_atlas(w, h):
    """Grey metal with a black (colour-keyed) border, like a real model skin."""
    px = []
    for y in range(h):
        for x in range(w):
            if x < 2 or y < 2 or x >= w - 2 or y >= h - 2:
                px.append((0, 0, 0))
            else:
                v = 40 + (x * 3 + y * 5) % 180
                px.append((v, v, min(255, v + (x % 7))))
    return px


def run_pass(files, env=None):
    """{name: bytes} -> ({name: bytes}, log lines)."""
    lines = []
    old = os.environ.get(build.WORLD_TRUECOLOR_ENV)
    old_cache = build.WORLD_TRUECOLOR_CACHE
    with tempfile.TemporaryDirectory() as d:
        build.WORLD_TRUECOLOR_CACHE = os.path.join(d, 'cache')
        if env is None:
            os.environ.pop(build.WORLD_TRUECOLOR_ENV, None)
        else:
            os.environ[build.WORLD_TRUECOLOR_ENV] = env
        try:
            mod = {}
            for low, data in files.items():
                p = os.path.join(d, low)
                with open(p, 'wb') as f:
                    f.write(data)
                mod[low] = (p, 'test')
            out = build._convert_world_truecolor(dict(mod), lines.append)
            result = {low: open(src, 'rb').read()
                      for low, (src, _) in out.items()}
        finally:
            build.WORLD_TRUECOLOR_CACHE = old_cache
            if old is None:
                os.environ.pop(build.WORLD_TRUECOLOR_ENV, None)
            else:
                os.environ[build.WORLD_TRUECOLOR_ENV] = old
    return result, lines


def run_probe(files, env):
    """Same harness, for the BUILD-413 flat-colour diagnostic."""
    lines = []
    old = os.environ.get(build.WORLD_TEX_PROBE_ENV)
    old_cache = build.WORLD_TRUECOLOR_CACHE
    with tempfile.TemporaryDirectory() as d:
        build.WORLD_TRUECOLOR_CACHE = os.path.join(d, 'cache')
        if env is None:
            os.environ.pop(build.WORLD_TEX_PROBE_ENV, None)
        else:
            os.environ[build.WORLD_TEX_PROBE_ENV] = env
        try:
            mod = {}
            for low, data in files.items():
                p = os.path.join(d, low)
                with open(p, 'wb') as f:
                    f.write(data)
                mod[low] = (p, 'test')
            out = build._convert_world_tex_probe(dict(mod), lines.append)
            result = {low: open(src, 'rb').read()
                      for low, (src, _) in out.items()}
        finally:
            build.WORLD_TRUECOLOR_CACHE = old_cache
            if old is None:
                os.environ.pop(build.WORLD_TEX_PROBE_ENV, None)
            else:
                os.environ[build.WORLD_TEX_PROBE_ENV] = old
    return result, lines


def make_p(vertextype=0, numverts=4, numvertcolors=None, numnormals=None,
           numtexcoords=None, colour=0x80FFFFFF):
    """
    A minimal but structurally real version-1 `.p` part.

    Header is the game's own 16 uint32s; the body is laid out in the order
    `load_p_file` reads it, so the offsets the pass would have to respect
    are all present even though it only ever touches word 2.
    """
    numvertcolors = numverts if numvertcolors is None else numvertcolors
    numnormals = numverts if numnormals is None else numnormals
    numtexcoords = numverts if numtexcoords is None else numtexcoords
    numpolys = max(1, numverts // 3)
    hdr = [1, 1, vertextype, numverts, numnormals, 0, numtexcoords,
           numvertcolors, 0, numpolys, 0, 0, 1, 1, 1, 0]
    out = bytearray(struct.pack('<16I', *hdr))
    out += b'\0' * (128 - len(out))
    out += struct.pack('<%df' % (numverts * 3),
                       *[float(i) for i in range(numverts * 3)])
    out += struct.pack('<%df' % (numnormals * 3),
                       *([0.0, 1.0, 0.0] * numnormals))
    out += struct.pack('<%df' % (numtexcoords * 2), *([0.25, 0.75] *
                                                      numtexcoords))
    out += struct.pack('<%dI' % numvertcolors, *([colour] * numvertcolors))
    out += struct.pack('<%dI' % numpolys, *([0xFFFFFFFF] * numpolys))
    out += b'\0' * (numpolys * 24)
    out += b'\0' * 100                                  # one p_hundred
    out += b'\0' * 56                                   # one p_group
    out += b'\0' * 28                                   # one bounding box
    return bytes(out)


def run_prelit(files, env=None):
    """{name: bytes} -> ({name: bytes}, log lines) for the pre-lit pass."""
    lines = []
    old = os.environ.get(build.WORLD_PRELIT_ENV)
    old_cache = build.WORLD_PRELIT_CACHE
    with tempfile.TemporaryDirectory() as d:
        build.WORLD_PRELIT_CACHE = os.path.join(d, 'cache')
        if env is None:
            os.environ.pop(build.WORLD_PRELIT_ENV, None)
        else:
            os.environ[build.WORLD_PRELIT_ENV] = env
        try:
            mod = {}
            for low, data in files.items():
                p = os.path.join(d, low)
                with open(p, 'wb') as f:
                    f.write(data)
                mod[low] = (p, 'test')
            out = build._convert_world_prelit(dict(mod), lines.append)
            result = {low: open(src, 'rb').read()
                      for low, (src, _) in out.items()}
        finally:
            build.WORLD_PRELIT_CACHE = old_cache
            if old is None:
                os.environ.pop(build.WORLD_PRELIT_ENV, None)
            else:
                os.environ[build.WORLD_PRELIT_ENV] = old
    return result, lines


def vertextype_of(data):
    return struct.unpack_from('<I', data, 8)[0]


def test_prelit():
    unlit = make_p(vertextype=0, numverts=6)
    prelit = make_p(vertextype=1, numverts=6)
    skin = make_truecolor(8, 8, grey_atlas(8, 8))

    out, lines = run_prelit({'cha.p': unlit, 'cib.p': prelit,
                             'hw1.tex': skin, 'cgd.hrc': b'\x01\0\0\0junk'})
    check('unset is ON: an unlit world part is re-flagged pre-lit',
          vertextype_of(out['cha.p']) == 1)
    check('the pre-lit pass changes ONE field and nothing else',
          len(out['cha.p']) == len(unlit)
          and out['cha.p'][:8] == unlit[:8]
          and out['cha.p'][12:] == unlit[12:])
    check('a part that is already pre-lit is left byte-for-byte alone',
          out['cib.p'] == prelit)
    check('the pre-lit pass does not touch .tex entries',
          out['hw1.tex'] == skin)
    check('the pre-lit pass does not touch .hrc entries',
          out['cgd.hrc'] == b'\x01\0\0\0junk')
    check('the pre-lit pass reports the count it changed',
          any('re-flagged PRE-LIT' in ln for ln in lines))

    out, lines = run_prelit({'cha.p': unlit}, env='off')
    check('off leaves the unlit part exactly as the mod shipped it',
          out['cha.p'] == unlit and vertextype_of(out['cha.p']) == 0)
    check('off says so in the log',
          any('is off' in ln for ln in lines))

    # A part with no colour for every vertex cannot be pre-lit: the engine
    # would draw the vertex buffer's own contents instead of the artist's.
    short = make_p(vertextype=0, numverts=6, numvertcolors=0)
    out, lines = run_prelit({'cha.p': short})
    check('a part with no vertex colours is REFUSED, not converted',
          out['cha.p'] == short and vertextype_of(out['cha.p']) == 0)
    check('the refusal names the reason',
          any('cannot be pre-lit' in ln for ln in lines))

    mismatch = make_p(vertextype=0, numverts=6, numvertcolors=3)
    out, _ = run_prelit({'cha.p': mismatch})
    check('a part with a SHORT vertex colour array is refused too',
          out['cha.p'] == mismatch)

    # TLVERTEX (2) is a 2D format. Only 0 is the unlit one.
    tl = make_p(vertextype=2, numverts=6)
    out, _ = run_prelit({'cha.p': tl})
    check('vertextype 2 is left alone', out['cha.p'] == tl)

    # Numbered parts are real entries: `cha.1.p`, `elb.4.p`.
    out, _ = run_prelit({'cha.1.p': unlit, 'elb.4.p': unlit})
    check('numbered .p entries are recognised',
          vertextype_of(out['cha.1.p']) == 1
          and vertextype_of(out['elb.4.p']) == 1)

    # Idempotent: running it twice must not differ from running it once.
    once, _ = run_prelit({'cha.p': unlit})
    twice, _ = run_prelit({'cha.p': once['cha.p']})
    check('the pass is idempotent', twice['cha.p'] == once['cha.p'])

    # Scope. char.lgp's unlit parts are lit correctly by the field, so the
    # pass must be reachable only from the world archive.
    check('the pass is scoped to world_us.lgp',
          build.WORLD_PRELIT_ARCHIVE == 'world_us.lgp')
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'build.py'), 'r').read()
    check('nothing calls the pre-lit pass for char.lgp',
          src.count('_convert_world_prelit(') == 2
          and 'if name == WORLD_PRELIT_ARCHIVE:' in src)


def main():
    test_prelit()

    hw = make_truecolor(64, 64, grey_atlas(64, 64))
    other = make_truecolor(32, 32, grey_atlas(32, 32))

    # --- BUILD 412 answered this pass's question with NO, so it is off -----
    out, _ = run_pass({'hw1.tex': hw, 'cl.tex': other})
    check('unset is off: nothing is converted by default',
          out['hw1.tex'] == hw and out['cl.tex'] == other)

    # --- "probe" converts the Highwind skin and nothing else ---------------
    out, _ = run_pass({'hw1.tex': hw, 'cl.tex': other}, env='probe')
    got = tex.parse(out['hw1.tex'])
    kept = tex.parse(out['cl.tex'])
    check('probe converts hw1.tex to 8-bit indexed',
          got['bytes_per_pixel'] == 1 and got['palette_flag'] == 1)
    check('probe leaves every other truecolor entry alone',
          out['cl.tex'] == other and kept['bytes_per_pixel'] == 3)

    # --- format only: dimensions and the drawn/keyed split are preserved ---
    src = tex.parse(hw)
    check('converted texture keeps its exact dimensions',
          (got['width'], got['height']) == (src['width'], src['height']))
    w, h = src['width'], src['height']
    raw = bytes(src['pixels'])
    black = {i for i in range(w * h)
             if not (raw[i * 3] or raw[i * 3 + 1] or raw[i * 3 + 2])}
    keyed = {i for i, v in enumerate(bytes(got['pixels'])) if v == 0}
    check('the keyed set is exactly the engine\'s own colour-key test '
          '(%d texels)' % len(black), keyed == black and bool(black))
    check('the colour key flag survives the conversion',
          struct.unpack_from('<I', out['hw1.tex'], tex.O_COLORKEY)[0] == 1)

    # --- and the drawn texels still look like the source --------------------
    pal = bytes(got['palette'])
    err = n = 0
    for i, idx in enumerate(bytes(got['pixels'])):
        if idx == 0:
            continue
        o = idx * 4
        for c, s in enumerate((pal[o + 2], pal[o + 1], pal[o])):
            err += abs(s - raw[i * 3 + (2 - c)])
            n += 1
    check('drawn texels survive quantisation (mean error %.2f/255 < 4)'
          % (err / max(1, n)), err / max(1, n) < 4.0)

    # --- "all" and "off" ----------------------------------------------------
    out, _ = run_pass({'hw1.tex': hw, 'cl.tex': other}, env='all')
    check('"all" converts every truecolor entry',
          all(tex.parse(out[k])['bytes_per_pixel'] == 1 for k in out))
    out, _ = run_pass({'hw1.tex': hw, 'cl.tex': other}, env='off')
    check('"off" is byte-for-byte the behaviour before this pass',
          out['hw1.tex'] == hw and out['cl.tex'] == other)
    out, _ = run_pass({'hw1.tex': hw, 'cl.tex': other}, env='cl.tex')
    check('an explicit name list converts exactly that list',
          out['hw1.tex'] == hw
          and tex.parse(out['cl.tex'])['bytes_per_pixel'] == 1)

    # --- never touch what it cannot carry, and never touch indexed input ----
    px = grey_atlas(16, 16)
    graded = make_truecolor(16, 16, px, colorkey=0, bypp=4,
                            alpha=[(i * 7) % 256 for i in range(len(px))])
    out, lines = run_pass({'hw1.tex': graded}, env='all')
    check('a 32-bit source with graded alpha is refused, not flattened',
          out['hw1.tex'] == graded
          and any('graded alpha' in ln for ln in lines))

    indexed, _ = tex.convert_for_battle(hw, cap=64)
    out, _ = run_pass({'hw1.tex': indexed}, env='all')
    check('an already-indexed entry is passed straight through',
          out['hw1.tex'] == indexed)

    # --- the BUILD-413 flat-colour probe -----------------------------------
    out, _ = run_probe({'hw1.tex': hw, 'cl.tex': other}, None)
    check('the probe is off unless it is asked for',
          out['hw1.tex'] == hw and out['cl.tex'] == other)

    for label, source in (('a truecolor source', hw),
                          ('an indexed source', indexed)):
        out, lines = run_probe({'hw1.tex': source}, 'hw1.tex')
        flat = tex.parse(out['hw1.tex'])
        src = tex.parse(source)
        idx = bytes(flat['pixels'])
        pal = bytes(flat['palette'])
        check('the probe flattens %s to one indexed colour' % label,
              set(idx) <= {0, 1} and flat['bytes_per_pixel'] == 1
              and (pal[4], pal[5], pal[6]) == (255, 255, 255))
        check('the probe keeps %s\'s dimensions' % label,
              (flat['width'], flat['height'])
              == (src['width'], src['height']))
        # The silhouette on screen must not move, or the probe is measuring
        # a different picture than the one being diagnosed.
        if src['bytes_per_pixel'] == 3:
            raw = bytes(src['pixels'])
            want = [1 if (raw[i * 3] or raw[i * 3 + 1] or raw[i * 3 + 2])
                    else 0 for i in range(src['width'] * src['height'])]
        else:
            want = [0 if v == 0 else 1 for v in bytes(src['pixels'])]
        check('the probe preserves %s\'s exact silhouette' % label,
              list(idx) == want)
        check('the probe says out loud that it is a diagnostic',
              any('DIAGNOSTIC' in ln for ln in lines))

    out, _ = run_probe({'hw1.tex': hw}, 'hw1:ff0000')
    pal = bytes(tex.parse(out['hw1.tex'])['palette'])
    check('the probe honours an explicit colour and a bare name',
          (pal[4], pal[5], pal[6]) == (0, 0, 255))   # BGRA on disk

    out, lines = run_probe({'cl.tex': other}, 'nosuch.tex')
    check('a probe name that is not in the archive is reported, not fatal',
          out['cl.tex'] == other
          and any('nothing replaced' in ln for ln in lines))

    print('')
    if FAILED:
        print('%d FAILED' % len(FAILED))
        for f in FAILED:
            print('  ' + f)
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())

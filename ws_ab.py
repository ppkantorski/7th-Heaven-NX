#!/usr/bin/env python3
"""
ws_ab.py -- flip the widescreen framing on and off on an already-built card.

    python3 ws_ab.py status      # what is on the card right now
    python3 ws_ab.py off         # widescreen OFF, everything else untouched
    python3 ws_ab.py on          # widescreen back ON
    python3 ws_ab.py save NAME   # snapshot the current module + shaders
    python3 ws_ab.py load NAME   # put a snapshot back

WHY THIS EXISTS
===============
Answering "is this artefact ours?" needs an A/B, and every A/B so far has
meant hand-editing several files and remembering what was where. This is one
command each way, and it never needs a rebuild.

WHAT IT WILL NOT DO
===================
It will NOT swap in the stock `dump/exefs/main`. That module is 26 words of
widescreen on top of ~110 word patches and 27 code caves from the 60 FPS
pass, plus the 512px field-background set -- and the 512px half rewrites
`flevel.lgp` to match. The build log says it outright:

    this patches exefs/main AND rewrites flevel.lgp to match; both halves
    are needed, so do not mix an flevel from one setting with a module
    from another.

A stock module against a 512px flevel is the "scattered black squares"
failure, not a clean baseline. So `off` reverts exactly the widescreen
words -- the four in `gfx_drv_init`, the eight tile-window sites -- and sets
`WS_SCALE` to 1.0 in the two vertex shaders. 60 FPS, the field-background
set and everything else stay exactly as built.

That makes `off` a real single-variable control: the ONLY difference from
`on` is the widescreen framing.

SAFETY
======
Every word is verified against its expected value before anything is
written (`nso_patcher`), and the first run snapshots the untouched module
and shaders into `ws_ab_snapshots/original/` so there is always a way back.
"""
import argparse
import os
import re
import shutil
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TITLE_ID = '0100A5B00BDC6000'
SHADERS = ('tlmain_vv.glsl', 'lmain_vv.glsl')
SNAPDIR = os.path.join(_HERE, 'ws_ab_snapshots')

# --------------------------------------------------------------------------
# the supersample factor
# --------------------------------------------------------------------------
# gfx_drv_init builds the render target as `logical * 1.5` -- ONE `fmov s0,
# #1.5` at +0x10D528C, multiplied into the width at +0x10D52C0 and into the
# height at +0x10D52EC. At 720p that is a 1920x1080 target presented into a
# 1280x720 swapchain: a 3:2 downscale.
#
# 3:2 is the problem. Three source pixels collapse into two output pixels,
# so the sampling phase repeats every 2 output pixels and every output pixel
# is built from a different number of source pixels than its neighbour. That
# is a FIXED pattern in screen space with period 2 and a harmonic at 4 --
# which is exactly the pair measured in both capture clips, unchanged when
# WS_SCALE moved. It hits detailed pixel art hard and smooth-shaded 3D models
# barely at all, which is why field backgrounds band and characters do not.
#
# 1.0 makes the target the swapchain size: no resample at all.
# 2.0 makes it an exact 2:1 box: even, also no fixed pattern, but 1.78x the
#     pixels of today, so it is the one to try second.
SUPER_VA = 0x10D528C
SUPER_WORDS = {1.0: 0x1E2E1000, 1.5: 0x1E2F1000, 2.0: 0x1E201000}


# The port's own dump keeps shaders at romfs/ff7/shaders, but HANDOFF-48 §3
# and ws_quickfix both say romfs/shaders, and at least one of those is what
# the running console is actually reading. Rather than pick one and be
# silently wrong, every candidate is searched and the one that HOLDS the
# files wins. `--shader-dir` overrides.
SHADER_CANDIDATES = (
    os.path.join('romfs', 'ff7', 'shaders'),
    os.path.join('romfs', 'shaders'),
)
SHADER_DIR_OVERRIDE = None

# Which of the two vertex shaders a flip touches. They feed DIFFERENT draw
# paths (HANDOFF-48 §1.2): tlmain_vv is the TLVERTEX/2D ortho path -- field
# backgrounds, menus, text -- and lmain_vv is the 3D path the character
# models take. Flipping one at a time is therefore a real experiment: it
# says which path an artefact belongs to.
ONLY = None

# `on` normally applies BOTH halves of widescreen: the framing (the four
# gfx_drv_init words + the shader scale) and the widened field tile window.
# --no-tiles applies the framing only, leaving the tile window at stock 4:3.
# That is the bisect between "the framing resamples wrongly" and "the wider
# window is drawing tiles that were not meant to be drawn".
NO_TILES = False


def _wanted(on_value):
    """
    {shader: scale} for a flip.

    `--only tl` means the 2D path keeps the widescreen scale and the 3D path
    is neutralised, and vice versa -- BOTH files are written every time. An
    earlier version only touched the selected file and left the other at
    whatever it already held, so `--only l` on a freshly built card set
    lmain 0.75 -> 0.75 and changed nothing at all. That is a silent no-op
    dressed as an experiment, which is the exact failure HANDOFF-48 §10.4
    is about.
    """
    if ONLY is None:
        return {s: on_value for s in SHADERS}
    keep = 'tlmain_vv.glsl' if ONLY == 'tl' else 'lmain_vv.glsl'
    return {s: (on_value if s == keep else 1.0) for s in SHADERS}


def shader_dirs(sdout):
    """Every candidate directory, existing or not."""
    base = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID)
    if SHADER_DIR_OVERRIDE:
        return [SHADER_DIR_OVERRIDE]
    return [os.path.join(base, c) for c in SHADER_CANDIDATES]


SRC_SHADERS = os.path.join(_HERE, 'custom_shaders', 'wide_screen')


def live_shader_dir(sdout):
    """The candidate that actually holds our two files, or None."""
    for d in shader_dirs(sdout):
        if all(os.path.exists(os.path.join(d, s)) for s in SHADERS):
            return d
    for d in shader_dirs(sdout):
        if any(os.path.exists(os.path.join(d, s)) for s in SHADERS):
            return d
    return None


def ensure_shaders(sdout, log=print):
    """
    Make sure sdout carries the two vertex shaders, copying them from
    custom_shaders/wide_screen if it does not.

    The build has never installed these -- they have been hand-copied to the
    card every time, which is why `status` found none in sdout. Hand-copying
    is exactly how the module half and the shader half drift apart, and a
    module patched for 16:9 with an unscaled shader is stretched rather than
    obviously broken, so the drift is easy to miss. Putting them in sdout
    means the normal "copy sdout to the card" step carries them.
    """
    d = live_shader_dir(sdout)
    if d:
        return d
    d = shader_dirs(sdout)[0]
    missing = [s for s in SHADERS
               if not os.path.exists(os.path.join(SRC_SHADERS, s))]
    if missing:
        log('  ! %s not found in %s -- cannot install shaders'
            % (', '.join(missing), os.path.relpath(SRC_SHADERS, _HERE)))
        return d
    os.makedirs(d, exist_ok=True)
    for s in SHADERS:
        shutil.copy2(os.path.join(SRC_SHADERS, s), os.path.join(d, s))
    log('  installed %s into %s (from custom_shaders/wide_screen)'
        % (' + '.join(SHADERS), os.path.relpath(d, sdout)))
    return d


def paths(sdout):
    base = os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID)
    return (os.path.join(base, 'exefs', 'main'),
            live_shader_dir(sdout) or shader_dirs(sdout)[0])


def find_sdout(explicit=None):
    if explicit:
        return explicit
    for cand in ('sdout', os.path.join('..', 'sdout')):
        p = os.path.join(_HERE, cand)
        if os.path.isdir(os.path.join(p, 'atmosphere', 'contents', TITLE_ID)):
            return os.path.abspath(p)
    return None


# --------------------------------------------------------------------------
# shaders
# --------------------------------------------------------------------------
_DEF = re.compile(r'^([ \t]*#define[ \t]+WS_SCALE[ \t]+)([0-9.]+)',
                  re.MULTILINE)


def shader_scale(path):
    if not os.path.exists(path):
        return None
    m = _DEF.search(open(path).read())
    return float(m.group(2)) if m else None


def set_shader_scale(path, value):
    """Rewrite `#define WS_SCALE`. Returns the old value, or None."""
    text = open(path).read()
    m = _DEF.search(text)
    if not m:
        return None
    old = float(m.group(2))
    if abs(old - value) < 1e-9:
        return old
    # %.2f would silently turn a diagnostic 0.6667 into 0.67, and 0.67 has a
    # different beat period from 2/3 -- which is the whole point of setting
    # it. Keep enough digits to round-trip, and ALWAYS keep a decimal point:
    # `%g` renders 1.0 as `1`, and `gl_Position.x *= 1` is an int/float
    # mismatch that GLSL ES refuses to compile. A shader that fails to build
    # does not fall back to something sane, it takes the draw with it.
    lit = '%.6g' % value
    if '.' not in lit and 'e' not in lit:
        lit += '.0'
    new = _DEF.sub(lambda mm: mm.group(1) + lit, text, count=1)
    # HANDOFF-48 §8: a str.replace that matches nothing returns the file
    # unchanged, silently. That happened twice. Assert instead.
    assert new != text, 'WS_SCALE substitution in %s changed nothing' % path
    tmp = path + '.tmp'
    open(tmp, 'w').write(new)
    os.replace(tmp, path)
    return old


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------
def snapshot(sdout, name, quiet=False):
    main, shdir = paths(sdout)
    dest = os.path.join(SNAPDIR, name)
    os.makedirs(dest, exist_ok=True)
    shutil.copy2(main, os.path.join(dest, 'main'))
    n = 1
    for s in SHADERS:
        src = os.path.join(shdir, s)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, s))
            n += 1
    if not quiet:
        print('  snapshot "%s": %d file(s) -> %s'
              % (name, n, os.path.relpath(dest, _HERE)))
    return dest


def restore(sdout, name):
    main, shdir = paths(sdout)
    src = os.path.join(SNAPDIR, name)
    if not os.path.isdir(src):
        print('! no snapshot named %r. have: %s'
              % (name, ', '.join(sorted(os.listdir(SNAPDIR)))
                 if os.path.isdir(SNAPDIR) else '(none)'))
        return 2
    shutil.copy2(os.path.join(src, 'main'), main)
    print('  main     <- %s' % name)
    for s in SHADERS:
        f = os.path.join(src, s)
        if os.path.exists(f):
            os.makedirs(shdir, exist_ok=True)
            shutil.copy2(f, os.path.join(shdir, s))
            print('  %-8s <- %s  (WS_SCALE %.2f)'
                  % (s.split('_')[0], name, shader_scale(f)))
    return 0


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------
def status(sdout):
    import nxmap
    import ff7nx_widescreen
    import ff7nx_wsclamp as C
    main, shdir = paths(sdout)
    for d in shader_dirs(sdout):
        have = [s for s in SHADERS if os.path.exists(os.path.join(d, s))]
        print('  shaders in %-24s : %s'
              % (os.path.relpath(d, sdout),
                 ', '.join(have) if have else '(none)'))
    print('sdout   %s' % sdout)
    print('module  %s' % main)
    img = nxmap.Main(main).img

    def word(va):
        return struct.unpack_from('<I', img, va)[0]

    def hx(s):
        return struct.unpack('<I', bytes(int(b, 16) for b in s.split()))[0]

    on = off = 0
    for p in ff7nx_widescreen.spec()['patches']:
        w = word(p['va'])
        if w == hx(p['set']):
            on += 1
        elif w == hx(p['expect']):
            off += 1
    print('  gfx_drv_init 16:9 words : %d patched, %d stock, %d unrecognised'
          % (on, off, 4 - on - off))
    ot = read_ortho(img)
    print('  2D ortho (projection)   : %s%s'
          % (ot or 'UNRECOGNISED',
             '   <- spans 854 units itself' if ot == 'wide' else ''))
    bl = read_bleed(img)
    print('  tile-edge bleed guard   : %s%s'
          % ('sized for %dpx pages' % bl if bl else 'UNRECOGNISED',
             '   <- 2x over-inset if your pages are 512px' if bl == 256
             else ''))
    ss = read_super(img)
    print('  render-target supersample: %s%s'
          % ('%.1fx' % ss if ss else 'UNRECOGNISED',
             '   (stock)' if ss == 1.5 else
             '   <- 1:1 with the swapchain, no resample' if ss == 1.0 else
             '   <- exact 2:1 downscale' if ss == 2.0 else ''))
    try:
        C.check_all(img)
        vals = {n: v for n, v, _s, _si in C.report(img)}
        wide = any(v for n, v in vals.items()
                   if n.startswith(('right', 'bottom'))) or \
            vals.get('left1', 336) != 336
        print('  field tile window       : %s'
              % ('WIDE' if wide else 'stock 4:3'))
        for n in ('left1', 'left2', 'top1', 'top2',
                  'right1', 'right2', 'bottom1', 'bottom2'):
            print('      %-8s %s' % (n, vals.get(n)))
    except C.SiteMismatch as exc:
        print('  field tile window       : UNRECOGNISED -- %s' % exc)
    for s in SHADERS:
        v = shader_scale(os.path.join(shdir, s))
        print('  %-22s : %s'
              % (s, 'WS_SCALE %.2f' % v if v is not None
                 else 'MISSING or no #define'))
    if os.path.isdir(SNAPDIR):
        print('  snapshots: %s' % ', '.join(sorted(os.listdir(SNAPDIR))))
    return 0


# --------------------------------------------------------------------------
# on / off
# --------------------------------------------------------------------------
def set_state(sdout, want_on):
    from pathlib import Path
    import nso_patcher
    import nxmap
    import ff7nx_widescreen
    import ff7nx_wsclamp as C

    main, _ = paths(sdout)
    shdir = ensure_shaders(sdout)
    os.makedirs(SNAPDIR, exist_ok=True)
    if not os.path.isdir(os.path.join(SNAPDIR, 'original')):
        snapshot(sdout, 'original')

    # Turning back ON: prefer restoring the snapshot over re-patching.
    #
    # `off` only un-hooks the caves, it does not free their words -- so a
    # second `on` cuts NEW caves out of fresh padding and the old bodies
    # stay behind as dead words. Functionally identical, but toggling ten
    # times would eat ten caves' worth of the pool for nothing. Restoring
    # the snapshot is byte-exact and costs nothing, so a flip is truly
    # reversible.
    orig = os.path.join(SNAPDIR, 'original')
    if want_on and not NO_TILES and os.path.isdir(orig):
        snap_img = nxmap.Main(os.path.join(orig, 'main')).img
        try:
            C.check_all(snap_img)
            vals = {n: v for n, v, _s, _si in C.report(snap_img)}
            snap_is_on = any(vals.get(n) for n in
                             ('right1', 'right2', 'bottom1', 'bottom2'))
        except C.SiteMismatch:
            snap_is_on = False
        if snap_is_on:
            print('  restoring the original (widescreen-on) module '
                  'byte-for-byte rather than re-cutting caves')
            # The snapshot predates any `super` change, so a plain restore
            # would silently put the supersample back to whatever was
            # captured -- undoing an experiment the user did not ask to
            # undo, and doing it in a line of output that says "restoring".
            # Carry the current factor across instead.
            keep = read_super(nxmap.Main(main).img)
            shutil.copy2(os.path.join(orig, 'main'), main)
            back = read_super(nxmap.Main(main).img)
            if keep is not None and back is not None and keep != back:
                print('  keeping the %.1fx supersample you set (the snapshot '
                      'holds %.1fx)' % (keep, back))
                set_super(sdout, keep, quiet=True)
            for s, sc in _wanted(0.75).items():
                p = os.path.join(shdir, s)
                old = set_shader_scale(p, sc)
                if old is not None:
                    print('  %-16s WS_SCALE %.2f -> %.4f' % (s, old, sc))
            print()
            print('  widescreen is now ON. Copy sdout to the SD card and '
                  'reboot.')
            return 0

    img = nxmap.Main(main).img
    nso = nso_patcher.read_nso(Path(main))
    applied = []

    fw = ff7nx_widescreen.spec()
    patches = []
    for p in fw['patches']:
        cur = struct.unpack_from('<I', img, p['va'])[0]

        def hx(s):
            return struct.unpack('<I', bytes(int(b, 16) for b in s.split()))[0]
        want = hx(p['set']) if want_on else hx(p['expect'])
        if cur == want:
            continue
        patches.append({'name': p['name'], 'va': p['va'],
                        'expect': ' '.join('%02X' % b
                                           for b in struct.pack('<I', cur)),
                        'set': ' '.join('%02X' % b
                                        for b in struct.pack('<I', want))})
    if patches:
        applied += nso_patcher.apply_spec(
            nso, {'name': 'gfx_drv_init 16:9', 'patches': patches})

    if want_on and not NO_TILES:
        try:
            C.check_all(img)
            m = nxmap.Main(main)
            sp = C.spec(img, C.defaults(), starts=set(m.arm_starts))
            if sp['patches']:
                applied += nso_patcher.apply_spec(nso, sp)
        except C.SiteMismatch as exc:
            print('  tile window left alone: %s' % exc)
    elif want_on and NO_TILES:
        sp = C.revert_spec(img)
        if sp['patches']:
            applied += nso_patcher.apply_spec(nso, sp)
        print('  tile window: stock 4:3 (--no-tiles)')
    else:
        sp = C.revert_spec(img)
        if sp['patches']:
            applied += nso_patcher.apply_spec(nso, sp)

    if applied:
        tmp = main + '.abtmp'
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        os.replace(tmp, main)
    print('  module: %d word(s) changed' % len(applied))

    want = _wanted(0.75 if want_on else 1.0)
    for s, scale in want.items():
        p = os.path.join(shdir, s)
        old = set_shader_scale(p, scale)
        if old is None:
            print('  ! %s missing or has no #define WS_SCALE -- the module '
                  'half is now %s WITHOUT its shader half, which will look '
                  'stretched. Fix this before testing.'
                  % (s, 'on' if want_on else 'off'))
        else:
            print('  %-16s WS_SCALE %.2f -> %.2f' % (s, old, scale))
    print()
    print('  widescreen is now %s. Copy sdout to the SD card and reboot.'
          % ('ON' if want_on else 'OFF'))
    return 0


# --------------------------------------------------------------------------
# the tile-edge bleed guard
# --------------------------------------------------------------------------
# +0xA09680 holds `movz w22, #0xBAE0, lsl #16` == -0.4375/256, a NORMALISED
# UV inset applied to every field background tile so bilinear taps stay
# inside the tile. 0.4375/256 is 0.4375 of a texel on a 256px page -- just
# under half, which is the point.
#
# On a 512px page the same UV constant is 0.875 of a texel: exactly twice the
# intended inset. Each 16-texel tile is then sampled from 14.25 texels
# stretched over its whole footprint, and neighbouring tiles do not share the
# stretch phase -- so there is a discontinuity at every tile boundary. One
# line per tile, on both axes, independent of WS_SCALE and of the render
# target size, and only on field backgrounds because they are the only thing
# drawn from a tile atlas.
BLEED_VA = 0xA09680
BLEED_WORDS = {256: 0x52B75C16, 512: 0x52B74C16, 1024: 0x52B73C16}


# --------------------------------------------------------------------------
# where the 2D widescreen scale comes from
# --------------------------------------------------------------------------
# Two ways to make the 2D path span 854 game units instead of 640:
#
#   shader  gl_Position.x *= 0.75 in tlmain_vv/lmain_vv, ortho left stock
#   ortho   patch the ortho's _11 (2/640 -> 2/854) and _41, shader left at 1.0
#
# They are the same transform on paper. MEASURED on hardware they are not:
# the shader route puts a band on every field background tile boundary and
# the ortho route -- with the SAME render target and the SAME 2.25 px per
# game unit -- is clean. Bisected in four reboots: 1920x1080 target with
# WS_SCALE 1.0 has no bands; the same target with WS_SCALE 0.75 has them.
#
# Why the two differ is not established. What IS established is which one to
# ship.
ORTHO_VAS = (0x10DA018, 0x10DA01C, 0x10DA038)
ORTHO_STOCK = {0x10DA018: 0x529999A8, 0x10DA01C: 0x72A76988,
               0x10DA038: 0xD2F7F008}
ORTHO_WIDE = {0x10DA018: 0x528F5CE8, 0x10DA01C: 0x72A76328,
              0x10DA038: 0xD2F7E808}


def read_ortho(img):
    """'stock', 'wide', or None if the three words are not a known set."""
    cur = {va: struct.unpack_from('<I', img, va)[0] for va in ORTHO_VAS}
    if cur == ORTHO_STOCK:
        return 'stock'
    if cur == ORTHO_WIDE:
        return 'wide'
    return None


def set_ortho(sdout, want):
    """Move the 2D widescreen scale into (or out of) the projection matrix."""
    from pathlib import Path
    import nso_patcher
    import nxmap
    main, _ = paths(sdout)
    img = nxmap.Main(main).img
    cur = read_ortho(img)
    if cur is None:
        print('! the 2D ortho words are neither stock nor wide -- refusing')
        return 2
    if cur == want:
        print('  2D ortho already %s -- nothing to do' % want)
        return 0
    os.makedirs(SNAPDIR, exist_ok=True)
    if not os.path.isdir(os.path.join(SNAPDIR, 'original')):
        snapshot(sdout, 'original')
    src = ORTHO_STOCK if cur == 'stock' else ORTHO_WIDE
    dst = ORTHO_WIDE if want == 'wide' else ORTHO_STOCK

    def hx(w):
        return ' '.join('%02X' % b for b in struct.pack('<I', w))
    nso = nso_patcher.read_nso(Path(main))
    for line in nso_patcher.apply_spec(nso, {
            'name': '2D ortho -> %s' % want,
            'patches': [{'name': '+0x%07X' % va, 'va': va,
                         'expect': hx(src[va]), 'set': hx(dst[va])}
                        for va in ORTHO_VAS if src[va] != dst[va]]}):
        print('  ' + line)
    tmp = main + '.ortmp'
    Path(tmp).write_bytes(nso_patcher.rebuild(nso))
    os.replace(tmp, main)
    if want == 'wide':
        print()
        print('  the 2D projection now spans 854 game units itself, so the')
        print('  shaders must go back to WS_SCALE 1.0 or the scale is')
        print('  applied twice:')
        print('      python3 ws_ab.py scale --tl 1.0 --l 1.0')
    print()
    print('  Copy sdout to the SD card and reboot.')
    return 0


def read_bleed(img):
    """The page size the tile-edge guard is currently sized for, or None."""
    w = struct.unpack_from('<I', img, BLEED_VA)[0]
    for px, word in BLEED_WORDS.items():
        if w == word:
            return px
    return None


def set_bleed(sdout, px):
    """Resize the tile-edge UV guard to match the field background pages."""
    from pathlib import Path
    import nso_patcher
    import nxmap
    main, _ = paths(sdout)
    if px not in BLEED_WORDS:
        print('! --page must be one of %s'
              % ', '.join(str(k) for k in sorted(BLEED_WORDS)))
        return 2
    img = nxmap.Main(main).img
    cur = read_bleed(img)
    if cur is None:
        print('! +0x%07X does not hold a recognised guard -- refusing'
              % BLEED_VA)
        return 2
    if cur == px:
        print('  guard already sized for %dpx pages -- nothing to do' % px)
        return 0
    os.makedirs(SNAPDIR, exist_ok=True)
    if not os.path.isdir(os.path.join(SNAPDIR, 'original')):
        snapshot(sdout, 'original')

    def hx(w):
        return ' '.join('%02X' % b for b in struct.pack('<I', w))
    nso = nso_patcher.read_nso(Path(main))
    for line in nso_patcher.apply_spec(nso, {
            'name': 'tile bleed guard -0.4375/%d -> -0.4375/%d' % (cur, px),
            'patches': [{'name': 'movz w22, #imm, lsl #16', 'va': BLEED_VA,
                         'expect': hx(BLEED_WORDS[cur]),
                         'set': hx(BLEED_WORDS[px])}]}):
        print('  ' + line)
    tmp = main + '.bltmp'
    Path(tmp).write_bytes(nso_patcher.rebuild(nso))
    os.replace(tmp, main)
    # The guard is a normalised UV, so what it is WORTH in texels depends on
    # the page it lands on. Report both, because the trade is the whole
    # reason this was off by default.
    uv = 0.4375 / px
    print('  truecolor %dpx pages : %.4f texel(s)   (intended 0.4375)'
          % (px, uv * px))
    print('  paletted 256px pages : %.4f texel(s)   %s'
          % (uv * 256,
             '' if abs(uv * 256 - 0.4375) < 1e-6 else
             '<- under-inset, but those are point-sampled 8-bit indices, '
             'so there is nothing for a filter to bleed'))
    print()
    print('  Copy sdout to the SD card and reboot.')
    return 0


def read_super(img):
    """The supersample factor currently encoded, or None."""
    w = struct.unpack_from('<I', img, SUPER_VA)[0]
    for f, word in SUPER_WORDS.items():
        if w == word:
            return f
    return None


def set_super(sdout, factor, quiet=False):
    """Rewrite the one `fmov` that sizes the render target."""
    from pathlib import Path
    import nso_patcher
    import nxmap
    main, _ = paths(sdout)
    if factor is None:
        print('! super needs --factor, one of %s'
              % ', '.join('%.1f' % f for f in sorted(SUPER_WORDS)))
        return 2
    if factor not in SUPER_WORDS:
        print('! --factor must be one of %s (those are the values FMOV can '
              'encode as an immediate; anything else needs a literal pool '
              'and is not worth it for a diagnostic)'
              % ', '.join('%.1f' % f for f in sorted(SUPER_WORDS)))
        return 2
    img = nxmap.Main(main).img
    cur = read_super(img)
    if cur is None:
        print('! +0x%07X does not hold a recognised `fmov s0, #imm` -- '
              'refusing to write' % SUPER_VA)
        return 2
    if cur == factor:
        print('  already %.1f -- nothing to do' % factor)
        return 0
    os.makedirs(SNAPDIR, exist_ok=True)
    if not os.path.isdir(os.path.join(SNAPDIR, 'original')):
        snapshot(sdout, 'original')

    def hx(w):
        return ' '.join('%02X' % b for b in struct.pack('<I', w))
    nso = nso_patcher.read_nso(Path(main))
    for line in nso_patcher.apply_spec(nso, {
            'name': 'render-target supersample %.1f -> %.1f' % (cur, factor),
            'patches': [{'name': 'fmov s0, #%.1f' % factor, 'va': SUPER_VA,
                         'expect': hx(SUPER_WORDS[cur]),
                         'set': hx(SUPER_WORDS[factor])}]}):
        if not quiet:
            print('  ' + line)
    tmp = main + '.sstmp'
    Path(tmp).write_bytes(nso_patcher.rebuild(nso))
    os.replace(tmp, main)
    if quiet:
        return 0
    for h in (720, 1080):
        print('  %dp handheld/docked: render target %dx%d -> %dx%d, '
              'presented into %dx%d (%.2fx)'
              % (h, int(h * 16 / 9 * cur), int(h * cur),
                 int(h * 16 / 9 * factor), int(h * factor),
                 int(h * 16 / 9), h, factor))
    print()
    print('  Copy sdout to the SD card and reboot.')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command',
                    choices=('status', 'on', 'off', 'save', 'load',
                             'scale', 'super', 'bleed', 'ortho'))
    ap.add_argument('name', nargs='?')
    ap.add_argument('--sdout', help='path to your sdout/ (auto-detected)')
    ap.add_argument('--shader-dir', help='where the card reads .glsl from')
    ap.add_argument('--mode', choices=('stock', 'wide'), default=None,
                    help='with `ortho`: put the 2D widescreen scale in the '
                         'projection matrix (wide) or not (stock)')
    ap.add_argument('--page', type=int, default=None,
                    help='field background page size the tile-edge bleed '
                         'guard should be sized for: 256 (stock), 512, 1024')
    ap.add_argument('--factor', type=float, default=None,
                    help='render-target supersample: 1.0, 1.5 (stock) or 2.0')
    ap.add_argument('--tl', type=float,
                    help='WS_SCALE for tlmain_vv.glsl (2D: field '
                         'backgrounds, menus, text)')
    ap.add_argument('--l', type=float,
                    help='WS_SCALE for lmain_vv.glsl (3D: character models)')
    ap.add_argument('--no-tiles', action='store_true',
                    help='with `on`: apply the 16:9 framing but leave the '
                         'field tile window at stock 4:3')
    ap.add_argument('--only', choices=('tl', 'l', 'both'), default='both',
                    help='flip only tlmain (2D: field backgrounds, menus) '
                         'or only lmain (3D: character models)')
    a = ap.parse_args(argv)

    global SHADER_DIR_OVERRIDE, ONLY, NO_TILES
    NO_TILES = a.no_tiles
    SHADER_DIR_OVERRIDE = a.shader_dir
    ONLY = None if a.only == 'both' else a.only
    sdout = find_sdout(a.sdout)
    if not sdout:
        print('! could not find sdout/. Run this from your 7th_heaven_nx '
              'folder, or pass --sdout <path>.')
        return 2
    main_path, _ = paths(sdout)
    if not os.path.exists(main_path):
        print('! %s does not exist -- build first.' % main_path)
        return 2

    if a.command == 'ortho':
        return set_ortho(sdout, a.mode or 'wide')
    if a.command == 'bleed':
        return set_bleed(sdout, a.page)
    if a.command == 'super':
        return set_super(sdout, a.factor)
    if a.command == 'scale':
        if a.tl is None and a.l is None:
            print('! scale needs --tl and/or --l, e.g.  '
                  'ws_ab.py scale --tl 0.6667 --l 0.75')
            return 2
        shdir = ensure_shaders(sdout)
        for s, v in (('tlmain_vv.glsl', a.tl), ('lmain_vv.glsl', a.l)):
            if v is None:
                continue
            old = set_shader_scale(os.path.join(shdir, s), v)
            if old is None:
                print('  ! %s missing or has no #define WS_SCALE' % s)
            else:
                print('  %-16s WS_SCALE %.4f -> %.4f' % (s, old, v))
        print()
        print('  Copy sdout to the SD card and reboot.')
        return 0
    if a.command == 'status':
        return status(sdout)
    if a.command == 'save':
        if not a.name:
            print('! save needs a name')
            return 2
        os.makedirs(SNAPDIR, exist_ok=True)
        snapshot(sdout, a.name)
        return 0
    if a.command == 'load':
        if not a.name:
            print('! load needs a name')
            return 2
        return restore(sdout, a.name)
    return set_state(sdout, a.command == 'on')


if __name__ == '__main__':
    sys.exit(main())

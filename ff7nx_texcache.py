#!/usr/bin/env python3
"""
ff7nx_texcache.py -- stop the port's texture cache from keeping dead surfaces.

THE DEFECT
----------
`gfx_free_texture` (+0x42D0) does not destroy a texture. It parks it in a
multimap hanging off the slot table, keyed on the surface's
**(width, height) and nothing else**, and lets a later creation adopt it:

    +00004348  mov  w8, #0x2070          ; the cache lives at table+0x2070
    +00004358  mov  x0, x20              ; equal_range(cache, {w, h})
    +0000435C  bl   #0x5150
    +00004360  cmp  x0, #9
    +00004364  b.hi #0x43d4              ; MORE THAN TEN ALREADY -> destroy
    ...        allocate a 0x30-byte node, link it, ++size at table+0x2080

`gfx_create_texture` (+0x4620) looks the key up at +0x4694 and, on a hit at
+0x47AC, adopts the cached surface, erases the node, and skips the create
call entirely.

**Nothing else ever removes an entry.** The tree root (`table+0x2078`) and
its size (`table+0x2080`) are written in exactly three places in the whole
module -- the constructor at +0x4058, the insert above, and the erase in the
creator -- so the only way a cached surface is ever released is for the game
to create another texture of the *same pixel dimensions*. Leave an area whose
sizes you never revisit and its surfaces stay resident for the rest of the
session.

That is bounded, not infinite: ten surfaces per distinct (w, h). Which is
fine for the game this was written for, and is not fine for this build.

WHY IT IS OURS AND NOT THE PORT'S
---------------------------------
Worst-case resident cache, counted off the archives (`--census`):

    battle   vanilla   8.3 MB  ->  BUILT  135.8 MB
    char     vanilla   4.1 MB  ->  BUILT   16.4 MB
    world    vanilla   4.1 MB  ->  BUILT   14.4 MB
    -----------------------------------------------
    TOTAL   vanilla  16.5 MB  ->  BUILT  166.5 MB      pool: 256 MB

The mod does not merely make textures bigger. It makes them bigger AND
spreads them over more distinct sizes -- battle goes from 17 keys to 32 --
and the cache bound is *per key*, so both axes multiply. 16.5 MB of dead
weight in a 256 MB pool is a rounding error. 166.5 MB is most of it.

WHAT THIS PREDICTS, AND WHY IT MATCHES
--------------------------------------
* **The framerate decays as you play.** The pool fills with surfaces nothing
  will ever draw again; everything live gets squeezed. This is the symptom
  that named the mechanism -- exhaustion fails, it does not sag.
* **Corruption arrives late, after enough places have been visited.** It
  takes real play to accumulate.
* **It heals when you walk back and forth.** Re-entering an area creates
  textures whose (w, h) matches the corpses, and each hit CONSUMES one.
* **Pool size barely moves it.** 128, 256 and 384 MB all get filled by the
  same 166 MB of corpses; only the time to get there changes. Measured:
  128 MB neither fixed it nor obviously worsened it.
* **Battle is the worst of it** -- 135.8 of the 166.5 MB -- and raising the
  battle background cap to 1024 makes battle corrupt almost immediately.

THE PATCH
---------
One word. `b.hi #0x43d4` at +0x4364 becomes an unconditional `b #0x43d4`, so
the cap check always fails and `free` always takes its destroy path.

That destroy path is not new code and it is not a guess: it is the branch the
stock game already takes every time a key reaches ten, so it runs hundreds of
times in an ordinary session. It destroys both surfaces, nulls both pointers,
frees the container and clears the slot (+0x43D4..+0x4430) -- complete.

With `free` never inserting, the tree stays empty, so the creator's lookup at
+0x4690 always takes its `cbz` and every texture is created fresh. The
cache-hit path becomes unreachable rather than wrong.

Cost: texture creation stops being able to recycle, so scene loads do a
little more allocator work. That is the trade -- a little load time against
166 MB of pool.

    python3 ff7nx_texcache.py <main> --show
    python3 ff7nx_texcache.py <main> --census
    python3 ff7nx_texcache.py <main> --mode nocache --out <main.patched>
    python3 ff7nx_texcache.py <main> --mode off     --out <main.patched>
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

# ONLY this directory. The parent puts any superseded loose copy beside the
# project folder ahead of the real module, and the shadowing cascades. Same
# note as ff7nx_heap, ff7nx_glerror and ff7nx_fieldbuf.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ---------------------------------------------------------------- constants
# The cap test and the branch it controls.
CMP_VA = 0x4360             # cmp x0, #9
CMP_WORD = 0xF100241F

GATE_VA = 0x4364            # b.hi #0x43d4   (stock)  /  b #0x43d4  (patched)
GATE_STOCK = 0x54000388     # b.hi +0x43D4
GATE_NOCACHE = 0x1400001C   # b    +0x43D4
DESTROY_VA = 0x43D4         # where both words branch to

# Anchors either side, so a module that is not this port fails verify()
# instead of getting a word written into the middle of something else.
ANCHORS = [
    (0x4348, 0x52840E08, 'mov w8, #0x2070   -- the cache base'),
    (0x4358, 0xAA1403E0, 'mov x0, x20       -- equal_range(cache, key)'),
    (0x435C, 0x9400037D, 'bl  #0x5150       -- equal_range'),
    (CMP_VA, CMP_WORD,   'cmp x0, #9        -- ten per key'),
    (0x43D4, 0xF8757A60, 'ldr x0, [x19, x21, lsl #3]  -- destroy path entry'),
    (0x4430, 0xF8357A7F, 'str xzr, [x19, x21, lsl #3] -- slot cleared'),
    (0x4478, 0xF9503E60, 'ldr x0, [x19, #0x2078]      -- tree root, insert'),
    (0x4488, 0xF9104268, 'str x8, [x19, #0x2080]      -- ++size'),
]

MODES = ('nocache', 'off')

# DEFAULT IS OFF, and that is deliberate rather than timid.
#
# The mechanism here is measured and the patched branch is one the stock game
# already takes -- this is a much better-supported change than the graphics
# pool ever was. It is still not on by default, because the graphics pool was
# ALSO believed on good grounds, shipped as a default, and then became the
# baseline that three builds of evidence were collected through. See
# FINDINGS-304 §6. One switch, thrown deliberately, with a before and after.
MODE = 'off'
MODE_ENV = 'SEVENTH_NX_TEX_CACHE'


def mode(env=None) -> str:
    raw = (os.environ if env is None else env).get(MODE_ENV)
    if raw is None or str(raw).strip() == '':
        return MODE
    v = str(raw).strip().lower()
    if v in ('1', 'on', 'nocache', 'disable', 'disabled'):
        return 'nocache'
    if v in ('0', 'off', 'stock', 'keep'):
        return 'off'
    return MODE


# ------------------------------------------------------------------ helpers
def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def verify(img) -> list[str]:
    """Everything that must hold before a word is written. Empty == clean."""
    bad = []
    for va, want, what in ANCHORS:
        got = _word(img, va)
        if got != want:
            bad.append('+0x%07X is %08X, expected %08X (%s)'
                       % (va, got, want, what))
    g = _word(img, GATE_VA)
    if g not in (GATE_STOCK, GATE_NOCACHE):
        bad.append('+0x%07X is %08X, which is neither the stock b.hi (%08X) '
                   'nor the patched b (%08X)'
                   % (GATE_VA, g, GATE_STOCK, GATE_NOCACHE))
    return bad


def read_state(img) -> str | None:
    """'off' / 'nocache' / None if the site is not recognised."""
    if verify(img):
        return None
    return 'nocache' if _word(img, GATE_VA) == GATE_NOCACHE else 'off'


def word_for(m: str) -> int:
    return GATE_NOCACHE if m == 'nocache' else GATE_STOCK


def _hex(w):
    return struct.pack('<I', w).hex()


def patches(img, m: str = None) -> list[dict]:
    """The nso_patcher patch list, or [] when there is nothing to do."""
    m = mode() if m is None else m
    want = word_for(m)
    cur = _word(img, GATE_VA)
    if cur == want:
        return []
    return [{'name': 'texture cache @ +0x%07X (%s)'
                     % (GATE_VA, 'free() always destroys' if m == 'nocache'
                        else 'stock: free() caches'),
             'va': GATE_VA, 'expect': _hex(cur), 'set': _hex(want)}]


def spec(img, m: str = None) -> dict | None:
    ps = patches(img, m)
    if not ps:
        return None
    m = mode() if m is None else m
    return {'name': 'texture cache %s' % m, 'patches': ps}


def selftest(log=print) -> bool:
    """The encoder against the branch it has to reproduce."""
    ok = True
    off = (DESTROY_VA - GATE_VA) // 4
    built = (0x05 << 26) | (off & 0x3FFFFFF)
    for label, want, got in (
            ('unconditional B encodes to the patched word',
             GATE_NOCACHE, built),
            ('stock b.hi targets the destroy path', DESTROY_VA,
             GATE_VA + (((GATE_STOCK >> 5) & 0x7FFFF) -
                        (0x80000 if (GATE_STOCK >> 5) & 0x40000 else 0)) * 4),
            ('both words branch to the same place', DESTROY_VA,
             GATE_VA + off * 4),
            ('modes', 2, len(MODES)),
            ('the default is off', 'off', MODE)):
        if want != got:
            log('  FAIL %s: %r != %r' % (label, want, got))
            ok = False
    return ok


# -------------------------------------------------------------------- apply
def apply_to_nso(src, dest, log=lambda *_: None, m: str = None) -> bool:
    """Write `dest` from `src`. False when there was nothing to change."""
    from pathlib import Path as _P
    import nxmap
    try:
        import nso_patcher
    except ImportError as exc:                             # pragma: no cover
        log('! texture cache: cannot import nso_patcher (%s)' % exc)
        return False

    img = nxmap.Main(str(src)).img
    bad = verify(img)
    if bad:
        log('! texture cache: module does not match this port; skipped')
        for b in bad:
            log('    %s' % b)
        log('  nothing was written; the module is unchanged')
        return False

    m = mode() if m is None else m
    if m not in MODES:
        m = MODE
    s = spec(img, m)
    if s is None:
        log('  texture cache: already %s; nothing to write'
            % ('disabled' if m == 'nocache' else 'stock'))
        return False

    try:
        nso = nso_patcher.read_nso(_P(str(src)))
        applied = nso_patcher.apply_spec(nso, s)
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        log('! texture cache: %s' % exc)
        log('  nothing was written; the module is unchanged')
        return False

    os.makedirs(os.path.dirname(os.path.abspath(str(dest))), exist_ok=True)
    with open(str(dest), 'wb') as f:
        f.write(data)
    for line in applied:
        log('  ' + line)
    log('  texture cache DISABLED: free() destroys instead of parking up to '
        'ten surfaces per (width, height). Worth up to ~166 MB of the 256 MB '
        'graphics pool on this build; see --census.'
        if m == 'nocache' else
        '  texture cache restored to stock.')
    return True


# ------------------------------------------------------------------- census
def census(log=print) -> dict:
    """Worst-case resident cache, per archive, vanilla against built.

    Counts min(10, textures at that size) * w * h * 4 over every distinct
    (w, h) -- the bound the multimap enforces. It is a ceiling, not a
    prediction: only textures with two surfaces and type 1 are cacheable
    (the guards at +0x4304..+0x431C), and a session has to actually visit
    the content. It is the right number for comparing vanilla to this build,
    which is what it is for.
    """
    import collections
    import lgp
    import tex

    CAP = 10
    S = os.path.join(_HERE, 'sdout', 'atmosphere', 'contents',
                     '0100A5B00BDC6000', 'romfs', 'ff7', 'workingdir', 'data')
    V = os.path.join(_HERE, 'cache', '_vanilla')

    def from_dir(d):
        c = collections.Counter()
        for root, _, fs in os.walk(d):
            for f in fs:
                try:
                    t = tex.parse(open(os.path.join(root, f), 'rb').read())
                except Exception:                              # noqa: BLE001
                    t = None
                if t:
                    c[(t['width'], t['height'])] += 1
        return c

    def from_lgp(p):
        c = collections.Counter()
        for e in lgp.Archive(p).entries:
            try:
                t = tex.parse(e['payload'])
            except Exception:                                  # noqa: BLE001
                t = None
            if t:
                c[(t['width'], t['height'])] += 1
        return c

    def mb(c):
        return sum(min(CAP, n) * w * h * 4 for (w, h), n in c.items()) / 1048576

    out = {}
    tv = tb = 0.0
    log('worst-case resident texture cache (10 surfaces per distinct size)')
    log('')
    for nm, van, blt in (
            ('battle', os.path.join(V, 'battle.lgp'),
             os.path.join(S, 'battle', 'battle.lgp')),
            ('char', os.path.join(V, 'char.lgp'),
             os.path.join(S, 'field', 'char.lgp')),
            ('world', os.path.join(V, 'world_us.lgp'),
             os.path.join(S, 'wm', 'world_us.lgp'))):
        if not os.path.exists(van) or not os.path.exists(blt):
            log('  %-8s  skipped (need %s and %s)' % (nm, van, blt))
            continue
        cv = from_dir(van) if os.path.isdir(van) else from_lgp(van)
        cb = from_dir(blt) if os.path.isdir(blt) else from_lgp(blt)
        v, b = mb(cv), mb(cb)
        tv += v
        tb += b
        out[nm] = {'vanilla_mb': v, 'built_mb': b,
                   'vanilla_keys': len(cv), 'built_keys': len(cb)}
        log('  %-8s vanilla %6.1f MB (%2d sizes)   built %6.1f MB (%2d sizes)'
            % (nm, v, len(cv), b, len(cb)))
    log('')
    log('  %-8s vanilla %6.1f MB                built %6.1f MB'
        % ('TOTAL', tv, tb))
    log('  the graphics pool this comes out of is 256 MB')
    out['total'] = {'vanilla_mb': tv, 'built_mb': tb}
    return out


# --------------------------------------------------------------------- show
def show(main, log=print) -> None:
    import nxmap
    img = nxmap.Main(str(main)).img
    bad = verify(img)
    if bad:
        log('texture cache site: NOT RECOGNISED')
        for b in bad:
            log('  %s' % b)
        return
    st = read_state(img)
    log('texture cache: %s'
        % ('DISABLED -- free() always destroys' if st == 'nocache'
           else 'stock -- free() parks up to 10 surfaces per (w, h)'))
    log('  +0x%07X  %08X' % (GATE_VA, _word(img, GATE_VA)))
    log('  build default: %s' % MODE)
    log('  environment:   %s=%s' % (MODE_ENV, os.environ.get(MODE_ENV, '')))


def main_(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('main', nargs='?', help='exefs/main')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--census', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--mode', choices=MODES)
    ap.add_argument('--out')
    a = ap.parse_args(argv)

    if a.selftest:
        return 0 if selftest() else 1
    if a.census:
        census()
        return 0
    if not a.main:
        ap.error('need exefs/main (or --census / --selftest)')
    if a.show or not a.out:
        show(a.main)
        return 0
    return 0 if apply_to_nso(a.main, a.out, print, a.mode) else 1


if __name__ == '__main__':
    raise SystemExit(main_())

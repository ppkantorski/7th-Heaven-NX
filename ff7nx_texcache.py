#!/usr/bin/env python3
"""
ff7nx_texcache.py -- stop the port's texture cache from hoarding big surfaces.

THE DEFECT
----------
`gfx_free_texture` (+0x42D0) does not destroy a texture. It parks it in a
multimap hanging off the slot table, keyed on the surface's
**(width, height) and nothing else**, and lets a later creation adopt it:

    +00004354  add  x1, sp, #8           ; &key
    +00004358  mov  x0, x20              ; the cache
    +0000435C  bl   #0x5150              ; equal_range -> how many at this size
    +00004360  cmp  x0, #9
    +00004364  b.hi #0x43d4              ; TEN ALREADY -> destroy instead
    +00004368  ...                       ; else allocate a node, link it, ++size

`gfx_create_texture` (+0x4620) looks the key up at +0x4694 and, on a hit at
+0x47AC, adopts the cached surface and skips creating one.

**Nothing else ever empties it.** The tree root (`table+0x2078`) and its size
(`table+0x2080`) are written in exactly three places in the whole module --
the constructor at +0x4058, that insert, and that erase -- so the only way a
cached surface is released is for the game to create another texture of
exactly those pixel dimensions. Leave an area whose sizes you never revisit
and its surfaces stay resident for the rest of the session.

Bounded, not infinite: ten per distinct (w, h). Fine for the game it was
written for, and not fine for this build -- 16.5 MB of dead weight in vanilla
becomes 137.6 MB here, out of a 256 MB pool. CONFIRMED ON HARDWARE: with the
cache off, the texture corruption stops.

THE THREE MODES, AND WHY 'small' IS THE ONE
-------------------------------------------
Turning the cache off entirely costs framerate, because recycling is a real
optimisation for short-lived textures that churn -- Wutai's water, spell
effects, animation frames. Measured on the built archives (`--census`):

    mode        worst-case resident      recycling available for
    off (stock)      137.6 MB            everything          <- corrupts
    nocache            0.0 MB            nothing             <- costs FPS
    small              8.1 MB            <=64 total, <=10 per small size

The first implementation of `small` replaced the count test with the size
test. That was a mistake: it removed the ten-per-size bound, so admitted
surfaces could accumulate without limit. Hardware reproduced corruption.

The corrected implementation keeps every dimension of the policy: cache only
surfaces no larger than 256x256, keep the port's stock limit of ten at any one
`(w,h)`, and keep at most 64 surfaces globally. That includes the 256x256
surfaces Wutai churns (the first version excluded them), while reducing the
archive-backed ceiling from 12.9 MB to 8.1 MB and imposing a 16 MiB absolute
runtime bound. Build 195 proved the same policy at four per key corruption-free;
restoring ten improves reuse without changing that global memory bound.

    at cap=1, where the 18.8 MB sits:
        1024x1024   4.00 MB (21%)     768x768   2.25 MB (12%)
        1000x1000   3.81 MB (20%)     768x384   1.12 MB ( 6%)

THE PATCHES
-----------
`nocache` -- one word. The `b.hi` at +0x4364 becomes unconditional, so free
always destroys. This is what was validated on hardware.

`small` -- one hook into verified inter-function padding. The cave tests
width <= 256, height <= 256, total cache population <= 63, calls the port's
original `equal_range`, then destroys when four surfaces already exist at that
size. The original call and container logic are preserved rather than
bypassed. The cave returns to
+0x4368, the stock insertion path, and every reject branches to the stock
destroy path at +0x43D4.

Every mode branches to the SAME destroy path at +0x43D4, which is not new
code: it is the branch the stock game already takes whenever a size fills up.
It releases both surfaces, nulls both pointers, frees the container and
clears the slot (+0x43D4..+0x4430).

    python3 ff7nx_texcache.py <main> --show
    python3 ff7nx_texcache.py <main> --census
    python3 ff7nx_texcache.py <main> --mode small   --out <main.patched>
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
# The stock decision and the one place every rejected surface goes.
CALL_VA = 0x435C            # bl #0x5150  (equal_range)  /  ldr x0, [sp, #8]
CMP_VA = 0x4360             # cmp x0, #9                 /  tst x0, #mask
GATE_VA = 0x4364            # b.hi / b / b.ne  -- all to DESTROY_VA
DESTROY_VA = 0x43D4

# The largest surface worth recycling. Inclusive: 256x256 is the important
# Wutai/FX class that the first `small` implementation accidentally excluded.
SMALL_MAX = 256
SMALL_PER_KEY = 10
SMALL_GLOBAL = 64
# Build 195's first safe bounded policy. Recognised so a no-rebuild patch can
# migrate it to the same-memory, stock-per-key revision instead of refusing it.
SMALL_PER_KEY_V1 = 4
RETURN_VA = 0x4368
EQUAL_RANGE_VA = 0x5150

SITE = {
    'off': {                                   # the port's own words
        CALL_VA: 0x9400037D,                   # bl   #0x5150
        CMP_VA:  0xF100241F,                   # cmp  x0, #9
        GATE_VA: 0x54000388,                   # b.hi #0x43d4
    },
    'nocache': {                               # never cache anything
        CALL_VA: 0x9400037D,                   # (unchanged)
        CMP_VA:  0xF100241F,                   # (unchanged)
        GATE_VA: 0x1400001C,                   # b    #0x43d4
    },
    # Build 193's broken implementation, accepted only so it can be migrated.
    'small_legacy': {
        CALL_VA: 0xF94007E0,                   # ldr  x0, [sp, #8]
        CMP_VA:  0xF2185C1F,                   # tst  x0, #0xFFFFFF00FFFFFF00
        GATE_VA: 0x54000381,                   # b.ne #0x43d4
    },
}

MODES = ('off', 'nocache', 'small')

# Anchors either side, so a module that is not this port fails verify()
# instead of getting words written into the middle of something else. These
# are the ones NO mode touches.
ANCHORS = [
    (0x4348, 0x52840E08, 'mov w8, #0x2070   -- the cache base'),
    (0x4354, 0x910023E1, 'add x1, sp, #8    -- &key'),
    (0x4358, 0xAA1403E0, 'mov x0, x20       -- the cache'),
    (0x4368, 0xF94007F6, 'ldr x22, [sp, #8] -- the insert reloads the key'),
    (0x43D4, 0xF8757A60, 'ldr x0, [x19, x21, lsl #3]  -- destroy path entry'),
    (0x4430, 0xF8357A7F, 'str xzr, [x19, x21, lsl #3] -- slot cleared'),
    (0x4478, 0xF9503E60, 'ldr x0, [x19, #0x2078]      -- tree root, insert'),
    (0x4488, 0xF9104268, 'str x8, [x19, #0x2080]      -- ++size'),
]

# DEFAULT. `small` is the shipping value: `nocache` was confirmed on hardware
# to stop the corruption, `small` keeps that property by construction (it
# admits only surfaces two orders of magnitude smaller than the ones that
# filled the pool) and gives back the recycling that `nocache` cost in
# framerate. It is still a one-switch change in the GUI, and `nocache`
# remains available as the known-good fallback.
MODE = 'small'
MODE_ENV = 'SEVENTH_NX_TEX_CACHE'

_ALIASES = {
    '1': 'nocache', 'on': 'nocache', 'disable': 'nocache',
    'disabled': 'nocache', 'none': 'nocache', 'nocache': 'nocache',
    '0': 'off', 'off': 'off', 'stock': 'off', 'keep': 'off', 'all': 'off',
    'small': 'small', 'smallonly': 'small', '2': 'small',
}


def mode(env=None) -> str:
    raw = (os.environ if env is None else env).get(MODE_ENV)
    if raw is None or str(raw).strip() == '':
        return MODE
    return _ALIASES.get(str(raw).strip().lower(), MODE)


# ------------------------------------------------------------------ helpers
def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _hex(w):
    return struct.pack('<I', w).hex()


def _branch_target(word, va):
    """Target of AArch64 B, BL, or B.cond at ``va``."""
    if (word & 0xFC000000) in (0x14000000, 0x94000000):
        imm = word & 0x03FFFFFF
        if imm & 0x02000000:
            imm -= 0x04000000
        return va + imm * 4
    imm = (word >> 5) & 0x7FFFF
    if imm & 0x40000:
        imm -= 0x80000
    return va + imm * 4


def _small_cave(img, per_key=SMALL_PER_KEY):
    """(payload, physical_addresses), or None when no current cave exists."""
    hook = _word(img, CALL_VA)
    if (hook & 0xFC000000) != 0x14000000:
        return None
    pc = _branch_target(hook, CALL_VA)
    payload, physical, seen = [], [], set()
    for _ in range(64):
        if pc in seen or pc < 0 or pc + 4 > len(img):
            return None
        seen.add(pc)
        physical.append(pc)
        word = _word(img, pc)
        if (word & 0xFC000000) == 0x14000000:
            target = _branch_target(word, pc)
            if target == RETURN_VA:
                payload.append((pc, word))
                break
            if target == DESTROY_VA:
                payload.append((pc, word))
                pc += 4
                continue
            pc = target                         # inter-hole chain link
            continue
        payload.append((pc, word))
        pc += 4
    if len(payload) != 17:
        return None
    p = payload
    exact = (
        p[0][1] == 0xB9400BE8 and               # ldr w8, [sp, #8]
        p[1][1] == 0x7104011F and               # cmp w8, #256
        (p[2][1] & 0xFF00001F) == 0x54000009 and
        _branch_target(p[2][1], p[2][0]) == p[4][0] and
        (p[3][1] & 0xFC000000) == 0x14000000 and
        _branch_target(p[3][1], p[3][0]) == DESTROY_VA and
        p[4][1] == 0xB9400FE8 and               # ldr w8, [sp, #12]
        p[5][1] == 0x7104011F and               # cmp w8, #256
        (p[6][1] & 0xFF00001F) == 0x54000009 and
        _branch_target(p[6][1], p[6][0]) == p[8][0] and
        (p[7][1] & 0xFC000000) == 0x14000000 and
        _branch_target(p[7][1], p[7][0]) == DESTROY_VA and
        p[8][1] == 0xB9401288 and               # ldr w8, [x20, #0x10]
        p[9][1] == 0x7100FD1F and               # cmp w8, #63
        (p[10][1] & 0xFF00001F) == 0x54000009 and
        _branch_target(p[10][1], p[10][0]) == p[12][0] and
        (p[11][1] & 0xFC000000) == 0x14000000 and
        _branch_target(p[11][1], p[11][0]) == DESTROY_VA and
        (p[12][1] & 0xFC000000) == 0x94000000 and
        _branch_target(p[12][1], p[12][0]) == EQUAL_RANGE_VA and
        p[13][1] == (0xF100001F | ((per_key - 1) << 10)) and
        (p[14][1] & 0xFF00001F) == 0x54000009 and
        _branch_target(p[14][1], p[14][0]) == p[16][0] and
        (p[15][1] & 0xFC000000) == 0x14000000 and
        _branch_target(p[15][1], p[15][0]) == DESTROY_VA and
        (p[16][1] & 0xFC000000) == 0x14000000 and
        _branch_target(p[16][1], p[16][0]) == RETURN_VA)
    return (payload, physical) if exact else None


def verify(img) -> list[str]:
    """Everything that must hold before a word is written. Empty == clean."""
    bad = []
    for va, want, what in ANCHORS:
        got = _word(img, va)
        if got != want:
            bad.append('+0x%07X is %08X, expected %08X (%s)'
                       % (va, got, want, what))
    # The mutable block must spell one complete mode, never a mixture.
    if read_state(img, _anchors_ok=True) is None:
        bad.append('+0x%07X/%07X/%07X are %08X/%08X/%08X, which is not any '
                   'of %s'
                   % (CALL_VA, CMP_VA, GATE_VA, _word(img, CALL_VA),
                      _word(img, CMP_VA), _word(img, GATE_VA),
                      '/'.join(MODES + ('small_bounded4', 'small_legacy'))))
    return bad


def read_state(img, _anchors_ok=False) -> str | None:
    """Current mode, `small_legacy`, or None when unrecognised."""
    if not _anchors_ok:
        if any(_word(img, va) != want for va, want, _ in ANCHORS):
            return None
    cur = (_word(img, CALL_VA), _word(img, CMP_VA), _word(img, GATE_VA))
    for m in ('off', 'nocache', 'small_legacy'):
        if cur == (SITE[m][CALL_VA], SITE[m][CMP_VA], SITE[m][GATE_VA]):
            return m
    if (_word(img, CMP_VA) == SITE['off'][CMP_VA]
            and _word(img, GATE_VA) == SITE['off'][GATE_VA]):
        if _small_cave(img) is not None:
            return 'small'
        if _small_cave(img, SMALL_PER_KEY_V1) is not None:
            return 'small_bounded4'
    return None


def _small_words(addr):
    import a64 as A
    LS = 0x9                         # unsigned lower-or-same (C clear or Z set)
    return [
        A.ldr(8, A.SP, 8),
        A.cmp_imm(8, SMALL_MAX),
        A.bcond(addr(2), addr(4), LS),
        A.b(addr(3), DESTROY_VA),
        A.ldr(8, A.SP, 12),
        A.cmp_imm(8, SMALL_MAX),
        A.bcond(addr(6), addr(8), LS),
        A.b(addr(7), DESTROY_VA),
        A.ldr(8, 20, 0x10),                    # global tree population
        A.cmp_imm(8, SMALL_GLOBAL - 1),
        A.bcond(addr(10), addr(12), LS),
        A.b(addr(11), DESTROY_VA),
        A.bl(addr(12), EQUAL_RANGE_VA),
        0xF100001F | ((SMALL_PER_KEY - 1) << 10),  # cmp x0, #3
        A.bcond(addr(14), addr(16), LS),
        A.b(addr(15), DESTROY_VA),
        A.b(addr(16), RETURN_VA),
    ]


def patches(img, m: str = None, starts=None, log=lambda *_: None) -> list[dict]:
    """The complete inline/cave patch list, or [] when already correct."""
    m = mode() if m is None else m
    if m not in MODES:
        m = MODE
    state = read_state(img)
    if state == m:
        return []
    words = {}
    cave = (_small_cave(img, SMALL_PER_KEY_V1)
            if state == 'small_bounded4'
            else (_small_cave(img) if state == 'small' else None))
    if cave:
        for va in cave[1]:
            words[va] = 0

    if m in ('off', 'nocache'):
        for va in (CALL_VA, CMP_VA, GATE_VA):
            words[va] = SITE[m][va]
    else:
        if starts is None:
            raise ValueError('small mode needs ARM function starts for its '
                             'verified padding cave')
        import a64 as A
        import cave_space
        import ff7nx_cave
        work = bytearray(img)
        for va in words:
            struct.pack_into('<I', work, va, 0)
        # Conditional branches only skip over a local long-range `b`, so the
        # cave itself merely has to fit one +/-1 MiB window. Its hook, BL,
        # destroy branches and return are ordinary +/-128 MiB instructions.
        holes, _ = cave_space.find_holes_in(work, set(starts))
        pool = ff7nx_cave.HolePool(work, holes=holes)
        entry, laid = ff7nx_cave.emit_laid_out(
            pool, lambda _entry, addr: _small_words(addr), span=0x80000)
        words.update(laid)
        words[CALL_VA] = A.b(CALL_VA, entry)
        words[CMP_VA] = SITE['off'][CMP_VA]
        words[GATE_VA] = SITE['off'][GATE_VA]
        log('  bounded texture-cache cave: 17 words in verified padding, '
            'entry +0x%07X' % entry)

    out = []
    for va, want in sorted(words.items()):
        cur = _word(img, va)
        if cur != want:
            out.append({'name': 'texture cache @ +0x%07X (%s)' % (va, m),
                        'va': va, 'expect': _hex(cur), 'set': _hex(want)})
    return out


def spec(img, m: str = None, starts=None, log=lambda *_: None) -> dict | None:
    ps = patches(img, m, starts, log)
    if not ps:
        return None
    return {'name': 'texture cache %s' % (mode() if m is None else m),
            'patches': ps}


def describe(m: str) -> str:
    return {
        'off': 'stock -- free() parks up to ten surfaces per (w, h)',
        'nocache': 'DISABLED -- free() always destroys',
        'small': 'BOUNDED SMALL -- <=%dx%d, at most %d per size and %d '
                 'globally; larger/excess surfaces are destroyed'
                 % (SMALL_MAX, SMALL_MAX, SMALL_PER_KEY, SMALL_GLOBAL),
    }[m]


# ---------------------------------------------------------------- selftest
def selftest(log=print) -> bool:
    """The encodings against what they have to mean."""
    ok = True

    def want(label, a, b):
        nonlocal ok
        if a != b:
            log('  FAIL %s: %r != %r' % (label, a, b))
            ok = False

    # The two inline modes branch to the stock destroy path. Small has three
    # reject branches in its cave, checked independently below.
    for m in ('off', 'nocache'):
        want('%s branches to the destroy path' % m,
             _branch_target(SITE[m][GATE_VA], GATE_VA), DESTROY_VA)
    base = 0x10000
    cave = _small_words(lambda i: base + 4 * i)
    for i in (3, 7, 11, 15):
        want('small reject %d branches to the destroy path' % i,
             _branch_target(cave[i], base + 4 * i), DESTROY_VA)
    want('small calls the original equal_range',
         _branch_target(cave[12], base + 48), EQUAL_RANGE_VA)
    want('small returns to the original insertion path',
         _branch_target(cave[16], base + 64), RETURN_VA)
    want('inclusive small threshold', SMALL_MAX, 256)
    want('stock ten cached surfaces per key', SMALL_PER_KEY, 10)
    want('Build 195 bounded-four migration value', SMALL_PER_KEY_V1, 4)
    want('sixty-four cached surfaces globally', SMALL_GLOBAL, 64)
    want('seventeen cave payload words',
         len(_small_words(lambda i: 0x1000+4*i)), 17)
    want('three modes', len(MODES), 3)
    want('the default is a real mode', MODE in MODES, True)
    want('legacy small is retained only for migration',
         set(SITE['small_legacy']) == {CALL_VA, CMP_VA, GATE_VA}, True)
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

    module = nxmap.Main(str(src))
    img = module.img
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
    try:
        s = spec(img, m, set(module.arm_starts), log)
    except Exception as exc:                                   # noqa: BLE001
        log('! texture cache: cannot allocate the bounded cache cave (%s)'
            % exc)
        log('  nothing was written; the module is unchanged')
        return False
    if s is None:
        log('  texture cache: already %s; nothing to write' % m)
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
    log('  texture cache: %s' % describe(m))
    if m == 'small':
        log('    surfaces <=%dx%d keep being recycled, but only %d per '
            'exact size and %d globally. Larger/excess surfaces are '
            'destroyed on free. ~8.1 MiB archive-backed ceiling, 16 MiB '
            'absolute runtime bound, against 137.6 MiB stock. See --census.'
            % (SMALL_MAX, SMALL_MAX, SMALL_PER_KEY, SMALL_GLOBAL))
    return True


# ------------------------------------------------------------------- census
def census(log=print) -> dict:
    """Worst-case resident cache per mode, vanilla against built.

    A ceiling, not a prediction: only textures with two surfaces and type 1
    are cacheable at all (the guards at +0x4304..+0x431C), and a session has
    to actually visit the content. It is the right number for comparing the
    modes to each other, which is what it is for.
    """
    import collections
    import lgp
    import tex

    S = os.path.join(_HERE, 'sdout', 'atmosphere', 'contents',
                     '0100A5B00BDC6000', 'romfs', 'ff7', 'workingdir', 'data')
    V = os.path.join(_HERE, 'cache', '_vanilla')

    def counts(p):
        c = collections.Counter()
        if os.path.isdir(p):
            for root, _, fs in os.walk(p):
                for f in fs:
                    try:
                        t = tex.parse(open(os.path.join(root, f), 'rb').read())
                    except Exception:                          # noqa: BLE001
                        t = None
                    if t:
                        c[(t['width'], t['height'])] += 1
        else:
            for e in lgp.Archive(p).entries:
                try:
                    t = tex.parse(e['payload'])
                except Exception:                              # noqa: BLE001
                    t = None
                if t:
                    c[(t['width'], t['height'])] += 1
        return c

    def mb(c, m):
        if m == 'nocache':
            return 0.0
        if m == 'small':
            surfaces = []
            for (w, h), n in c.items():
                if w <= SMALL_MAX and h <= SMALL_MAX:
                    surfaces.extend([w * h * 4] * min(SMALL_PER_KEY, n))
            # Worst archive-backed choice under the global population cap.
            return sum(sorted(surfaces, reverse=True)[:SMALL_GLOBAL]) / 1048576
        return sum(min(10, n) * w * h * 4
                   for (w, h), n in c.items()) / 1048576

    tot_v, tot_b = collections.Counter(), collections.Counter()
    rows = []
    for nm, van, blt in (
            ('battle', os.path.join(V, 'battle.lgp'),
             os.path.join(S, 'battle', 'battle.lgp')),
            ('char', os.path.join(V, 'char.lgp'),
             os.path.join(S, 'field', 'char.lgp')),
            ('world', os.path.join(V, 'world_us.lgp'),
             os.path.join(S, 'wm', 'world_us.lgp'))):
        if not os.path.exists(van) or not os.path.exists(blt):
            log('  %-8s skipped (need %s and %s)' % (nm, van, blt))
            continue
        cv, cb = counts(van), counts(blt)
        tot_v.update(cv)
        tot_b.update(cb)
        rows.append((nm, cv, cb))

    log('worst-case resident texture cache')
    log('')
    log('  %-8s  %-24s  %-24s' % ('', 'VANILLA', 'THIS BUILD'))
    log('  %-8s  %7s %7s %7s   %7s %7s %7s'
        % ('archive', 'stock', 'small', 'none', 'stock', 'small', 'none'))
    for nm, cv, cb in rows:
        log('  %-8s  %6.1fM %6.1fM %6.1fM   %6.1fM %6.1fM %6.1fM'
            % (nm, mb(cv, 'off'), mb(cv, 'small'), 0.0,
               mb(cb, 'off'), mb(cb, 'small'), 0.0))
    log('  %-8s  %6.1fM %6.1fM %6.1fM   %6.1fM %6.1fM %6.1fM'
        % ('TOTAL', mb(tot_v, 'off'), mb(tot_v, 'small'), 0.0,
           mb(tot_b, 'off'), mb(tot_b, 'small'), 0.0))
    log('')
    small_n = sum(n for (w, h), n in tot_b.items()
                  if w <= SMALL_MAX and h <= SMALL_MAX)
    retained_n = min(SMALL_GLOBAL,
                     sum(min(SMALL_PER_KEY, n)
                         for (w, h), n in tot_b.items()
                         if w <= SMALL_MAX and h <= SMALL_MAX))
    log('  recycling is available to %d of %d qualifying textures; the '
        'archive-backed resident ceiling retains at most %d surfaces'
        % (small_n, sum(tot_b.values()), retained_n))
    log('  the graphics pool all of this comes out of is 256 MB')
    return {'built': {m: mb(tot_b, m) for m in MODES},
            'vanilla': {m: mb(tot_v, m) for m in MODES}}


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
    if st == 'small_legacy':
        log('texture cache: LEGACY UNBOUNDED SMALL -- rebuild or migrate')
    elif st == 'small_bounded4':
        log('texture cache: BUILD 195 BOUNDED FOUR -- migrate for more reuse')
    else:
        log('texture cache: %s' % describe(st))
    for va in (CALL_VA, CMP_VA, GATE_VA):
        log('  +0x%07X  %08X' % (va, _word(img, va)))
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

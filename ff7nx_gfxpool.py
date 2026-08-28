#!/usr/bin/env python3
"""
ff7nx_gfxpool.py -- raise the GRAPHICS pool the port hardcoded at 256 MB.

This is a DIFFERENT pool from `ff7nx_heap`'s and the two are easy to
confuse, so read this paragraph before touching either. `ff7nx_heap` sizes
the guest heap at 0x02000000 -- the Win32 `HeapAlloc` arena FF7's own code
allocates *pixels* out of. THIS module sizes the block handed to
`nv::InitializeGraphics`, which is where the NVN/GL driver puts everything
the GPU has to be able to read: textures, render targets, vertex buffers,
command memory. Raising one does nothing for the other.

WHERE IT IS -- MEASURED FROM `exefs/main`, NOT ASSUMED
=====================================================
The port's graphics bring-up is one straight-line block at module
+0x113BD30. Disassembled:

    +113BD50  adrp x0/x1/x2, #0x12ce000        the three allocator callbacks
    +113BD5C  ldr  x0, [x0, #0xc18]            alloc
    +113BD60  ldr  x1, [x1, #0xc20]            free
    +113BD64  ldr  x2, [x2, #0xc28]            realloc
    +113BD68  mov  x3, xzr
    +113BD6C  bl   #0x11524a0                  nv::SetGraphicsAllocator
    +113BD70  mov  w0, #0x10000000             <- 256 MB, the size to malloc
    +113BD74  bl   #0x1150c10                  the allocator (returns NULL
                                               and sets errno 12 on failure)
    +113BD78  mov  w1, #0x10000000             <- 256 MB, the size to declare
    +113BD7C  bl   #0x11524b0                  nv::InitializeGraphics(p, n)
    +113BD80  bl   #0x11524c0

Both `bl`s at +113BD6C and +113BD7C encode to the same word (0x940059CD)
because the sites and their targets are both 0x10 apart -- that coincidence
is part of the signature and is what makes it certain the hook landed here.

The two PLT stubs were resolved through `.rela.plt` against the dynamic
symbol table, not by pattern-matching a name:

    GOT 0x12CDFD8 -> _ZN2nv20SetGraphicsAllocatorE...
    GOT 0x12CDFE0 -> _ZN2nv18InitializeGraphicsEPvm
    GOT 0x12CE0A0 -> _ZN2nn2os17SetMemoryHeapSizeEm

`nv::InitializeGraphics` has exactly ONE caller in the whole module
(+0x113BD7C). There is no second pool and no other size to keep in step.

WHY THIS PROJECT IN PARTICULAR PRESSURES IT
===========================================
256 MB is what the port shipped, and this repository has never touched it.
The stock game's demands on it are small. This build's are not, and the
build's own log has been saying so for a hundred builds:

    field render targets: 28.12 MB (8 of them), +25.78 MB vs stock
    ! that comes out of the same pool the field background PAGES allocate
      from

plus, from the field-background pass:

    768px TRUECOLOR pages ... 3.38 MB once the engine builds its 32bpp
    surface   (a vanilla 256px paletted page is 0.31 MB)

So relative to the game the pool was sized for, this build adds ~26 MB of
permanent render target and multiplies the per-page texture cost by about
eleven, on top of a 768px world-map texture cap, a 512px char cap and a
768px battle background cap. Every one of those lands here.

RESULT: THIS WAS THE WRONG LEVER, AND A HARMFUL ONE
===================================================
Everything below was the reasoning for raising this pool. It is kept
because the disassembly in it is correct and worth having. The CONCLUSION
was not: raising the pool does not fix the end-of-frame fault (that was the
GL error reporter -- see ff7nx_glerror), and it introduces permanent
texture corruption of its own. See the hardware result on DEFAULT_MB below.

WHAT THIS DOES AND DOES NOT CLAIM
=================================
It does NOT claim to have named the abort in the crash reports. What is
established (see FINDINGS-302) is that all six FF7 reports are identical at
every module offset, and that the faulting call is the LAST call of
`gfx_drv_flip`:

    +10DAB50  ldr x0, [x21]            the renderer singleton, *(0x12CF4E8)
    +10DAB54  bl  #0x1132190           -> (**renderer)[+0x40]
    +10DAB58  ...                      <- ReturnAddress[04] in every report

reached through `nn::os::detail::UserExceptionHandler`, i.e. a real CPU
fault taken inside the renderer at end-of-frame, not a deliberate assert.
"A graphics resource the renderer needed was not there, after churn" is the
shape of that, and a fixed pool this build overspends is the first
candidate that can be tested in one command.

This is therefore a LEVER, not a diagnosis. It is worth shipping as one
because it is cheap, its failure mode is loud, and it is reversible without
a rebuild:

    python3 patch_gfxpool_sdout.py --mb 384     # no rebuild, ~1 second
    python3 patch_gfxpool_sdout.py --stock      # put 256 back

RISK, HONESTLY
==============
The allocation at +0x113BD74 is a plain `malloc` out of nnSdk's heap, which
+0x1150DE0 sizes to ALL available application memory rounded down to 2 MB.
It returns NULL and sets errno 12 on failure -- it does not abort. What
aborts is `nv::InitializeGraphics(NULL, n)` immediately after. So asking
for more than the console can give does not corrupt anything and does not
produce a subtle bug: the game fails to boot, every time, on the first
frame. That is a good failure mode, but it is why the ladder stops at
512 MB by default and why `--mb` refuses anything over `MAX_MB`.

For scale: this build's other reservations are the 256 MB guest heap and
the 272 MB host arena (`ff7nx_heap`), against roughly 3.2 GB of application
memory. 384 MB here takes the total to about 912 MB.

SIZES
=====
Unlike the guest heap, the size here is NOT constrained to a contiguous run
of bits. Both sites are single `mov Wd, #imm` slots and a MOVZ with a lsl
#16 shift covers every whole multiple of 64 KB, so any whole number of MB
is writable. `encodable()` is still the authority and `selftest()` proves
the encoder reproduces the stock words before anything is written.

    python3 ff7nx_gfxpool.py <main> --show
    python3 ff7nx_gfxpool.py <main> --mb 384 --out <main.patched>
    python3 ff7nx_gfxpool.py <main> --mb 256 --out <main.patched>   # stock
    python3 ff7nx_gfxpool.py --selftest
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

# ONLY this directory -- the same rule, and for the same reason, as
# ff7nx_heap: inserting the parent lets a stale `build.py` beside the
# project folder shadow the real one.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_heap                                              # noqa: E402

MB = 1 << 20

# ---------------------------------------------------------------- constants
STOCK_MB = 256                     # what the port ships: mov w?, #0x10000000
STOCK_BYTES = STOCK_MB * MB

# The ceiling this module will write. Not a hardware limit -- a
# deliberately conservative one. Above this the failure mode is a game that
# will not boot, and there is no evidence yet that any size helps, so there
# is no reason to hand out a bigger foot-gun than the experiment needs.
MAX_MB = 512

# The floor. Sizes BELOW stock exist for one reason and it is a diagnostic
# one: the 384/256 hardware result is a DOSE-RESPONSE, not an on/off switch.
# 384 MB corrupted textures constantly; 256 MB corrupts them rarely, after
# enough field and battle churn (build 191, 2026-08-27). More pool, sooner.
#
# Two mechanisms fit "more pool, sooner" and they make OPPOSITE predictions
# about a SMALLER pool:
#
#   used-extent      something (an offset field, an alignment assumption, a
#                    neighbouring mapping) stops being correct once the
#                    allocator's high-water mark passes a fixed address. A
#                    bigger block crosses it immediately; the stock block
#                    only crosses it after enough churn. A SMALLER block
#                    can never cross it  ->  corruption should STOP.
#
#   plain exhaustion the pool simply runs out and the failure is silent.
#                    A SMALLER block runs out sooner  ->  corruption should
#                    get MUCH WORSE, fast.
#
# One playtest at 192 MB separates them, and nothing else on the table does.
# That is worth a floor below stock. It is NOT worth shipping: anything here
# under STOCK_MB is a size the port never asks for, and the honest failure
# mode is a game that will not boot.
MIN_MB = 128

# Environment override, so the GUI and a headless build set it the same way
# every other cap in this tree is set.
POOL_MB_ENV = 'SEVENTH_NX_GFX_POOL_MB'

# The default a plain build ships: STOCK. This pass writes nothing unless
# somebody asks for a different number, and they should not.
#
# ==================== HARDWARE RESULT, 2026-08-27 ====================
# RAISING THIS POOL CAUSES TEXTURE CORRUPTION. Do not ship a raised value.
#
# It was defaulted to 384 for three builds (187, 188, 189) on the reasoning
# in the header above -- that this build overspends a pool the port sized
# for vanilla. That reasoning was plausible, it was wrong, and it was
# expensive: every one of those builds showed permanent texture corruption
# across field models, world map and battle, and 512 was worse rather than
# better. Setting it back to 256 made all of it stop, in Wutai, in lower
# Junon, in battle and on the world map, with nothing else changed.
#
# So the pool is not a headroom knob. 256 MB is a value the port and the
# NVN/GL driver agree about, and a larger one is not simply "more room" --
# whatever the driver does with that block stops being correct. The exact
# mechanism is NOT established (an internal offset width, an alignment
# assumption, and a mapping limit are all consistent with the symptom), and
# nothing here should be read as if it were.
#
# The A/B is preserved in patch_gfxpool_sdout.py for anyone who wants to
# reproduce it. It is a diagnostic, not a setting.
# =====================================================================
DEFAULT_MB = STOCK_MB


def pool_mb(env=None) -> int:
    """The size to build, from the environment, falling back to DEFAULT_MB.

    An unparseable or out-of-range value reads back as STOCK_MB rather than
    as DEFAULT_MB. A setting nobody can read is not a licence to change the
    binary.
    """
    raw = (os.environ if env is None else env).get(POOL_MB_ENV)
    if raw is None or str(raw).strip() == '':
        return DEFAULT_MB
    try:
        mb = int(str(raw).strip())
    except (TypeError, ValueError):
        return STOCK_MB
    if mb == 0:                     # explicit "off" -> leave the port's value
        return STOCK_MB
    return mb if encodable(mb) is None else STOCK_MB


def encodable(mb: int) -> str | None:
    """None if this size can be written, else why it cannot."""
    if not isinstance(mb, int) or isinstance(mb, bool):
        return 'not an integer'
    if mb <= 0:
        return 'must be a positive whole number of MB'
    if mb < MIN_MB:
        return ('%d MB is under the %d MB floor this module will write '
                '(ff7nx_gfxpool.MIN_MB)' % (mb, MIN_MB))
    if mb > MAX_MB:
        return ('%d MB is over the %d MB ceiling this module will write '
                '(ff7nx_gfxpool.MAX_MB)' % (mb, MAX_MB))
    value = mb * MB
    if value & 0xFFFF:
        return '0x%08X is not a whole multiple of 64 KB' % value
    if (value >> 16) > 0xFFFF:
        return '0x%08X does not fit a MOVZ hi16' % value
    return None


def sizes() -> list[int]:
    """Every size the GUI should offer, smallest first. STOCK_MB is 'off'."""
    return [m for m in (128, 160, 192, 224, 256, 320, 384, 448, 512)
            if encodable(m) is None]


# ------------------------------------------------------------- encoding
def encode_size(rd: int, value: int) -> int:
    """`mov Wd, #value`. ORR-immediate when it fits, else MOVZ hi16.

    Preferring ORR is not cosmetic: it reproduces the STOCK word byte for
    byte at 256 MB, which is what lets `selftest()` check the encoder
    against the binary instead of against itself.
    """
    w = ff7nx_heap.orr_xzr32(rd, value)
    if w is not None:
        return w
    return ff7nx_heap.movz32(rd, value >> 16, 16)


def decode_size(word: int, rd: int) -> int | None:
    """Bytes named by `mov Wd, #imm`, or None if that is not what it is."""
    # MOVZ Wd, #imm16, lsl #16
    if (word & 0xFFE0001F) == (0x52A00000 | rd):
        return ((word >> 5) & 0xFFFF) << 16
    # ORR Wd, WZR, #imm  -- decode by re-encoding every candidate rather
    # than inverting the logical-immediate maths by hand.
    if (word & 0xFF8003FF) == (0x32000000 | (31 << 5) | rd):
        for mb in range(1, 4096):
            if ff7nx_heap.orr_xzr32(rd, mb * MB) == word:
                return mb * MB
    return None


def _hex(word: int) -> str:
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


# --------------------------------------------------------------- the site
# ONE site, seven words, two of them ours. Every other word must match the
# stock module exactly before anything is written. `mov w0, #0x10000000` on
# its own is a common enough encoding to land somewhere harmless by
# accident; the pair of identical `bl`s around it is not.
SITE = {
    'name': 'nv::InitializeGraphics: the graphics pool',
    'va': 0x113BD68,
    'words': [
        0xAA1F03E3,   # mov  x3, xzr                 4th allocator arg
        0x940059CD,   # bl   #0x11524a0              nv::SetGraphicsAllocator
        0x320403E0,   # mov  w0, #0x10000000         <- SIZE (to malloc)
        0x940053A7,   # bl   #0x1150c10              malloc
        0x320403E1,   # mov  w1, #0x10000000         <- SIZE (to declare)
        0x940059CD,   # bl   #0x11524b0              nv::InitializeGraphics
        0x940059D0,   # bl   #0x11524c0
    ],
    # index -> (register, what it is)
    'fields': {2: (0, 'pool size passed to the allocator'),
               4: (1, 'pool size passed to nv::InitializeGraphics')},
}

# Where `read_mb` looks. Kept separate from SITE so a signature edit cannot
# silently move the reader somewhere else.
SIZE_VA = SITE['va'] + 4 * 4        # +0x113BD78, the w1 store
SIZE_RD = 1


def _img(main):
    if isinstance(main, (bytes, bytearray)):
        return main
    import nxmap
    return nxmap.Main(str(main)).img


def verify_site(main) -> list[str]:
    """Complaints about the module. Empty means the hook landed.

    The two SIZE words are checked as "a decodable `mov Wd, #n MB`" rather
    than as an exact value, so this passes on a module an earlier run
    already raised. Everything else must be stock.
    """
    img = _img(main)
    bad = []
    for i, expect in enumerate(SITE['words']):
        va = SITE['va'] + 4 * i
        if va + 4 > len(img):
            bad.append('+0x%07X is past the end of the module' % va)
            continue
        have = struct.unpack_from('<I', img, va)[0]
        if i in SITE['fields']:
            rd, what = SITE['fields'][i]
            value = decode_size(have, rd)
            if value is None:
                bad.append('+0x%07X holds %08X, which does not decode as '
                           '`mov w%d, #<size>` -- %s'
                           % (va, have, rd, what))
            elif value % MB:
                bad.append('+0x%07X holds %08X = 0x%08X, not a whole number '
                           'of MB' % (va, have, value))
            continue
        if have != expect:
            bad.append('+0x%07X holds %08X, expected the stock %08X -- the '
                       'graphics bring-up block does not match this module'
                       % (va, have, expect))
    return bad


def read_mb(main) -> int | None:
    """The pool size the module is set to, or None if it is not decodable."""
    img = _img(main)
    if SIZE_VA + 4 > len(img):
        return None
    value = decode_size(struct.unpack_from('<I', img, SIZE_VA)[0], SIZE_RD)
    if value is None or value % MB:
        return None
    return value // MB


def patches(img, mb: int = None) -> list[dict]:
    """The nso_patcher patch list, or [] when there is nothing to do."""
    mb = pool_mb() if mb is None else mb
    why = encodable(mb)
    if why:
        raise ValueError('graphics pool %r MB: %s' % (mb, why))
    out = []
    for i, (rd, what) in sorted(SITE['fields'].items()):
        va = SITE['va'] + 4 * i
        cur = struct.unpack_from('<I', img, va)[0]
        new = encode_size(rd, mb * MB)
        if cur == new:
            continue
        out.append({'name': 'graphics pool @ +0x%07X (%s)' % (va, what),
                    'va': va, 'expect': _hex(cur), 'set': _hex(new)})
    return out


def spec(img, mb: int = None) -> dict | None:
    ps = patches(img, mb)
    if not ps:
        return None
    mb = pool_mb() if mb is None else mb
    return {'name': 'graphics pool %d MB' % mb, 'patches': ps}


def report(mb: int = None, log=print) -> None:
    mb = pool_mb() if mb is None else mb
    log('  graphics pool  %d MB  (stock %d MB, +%d MB)'
        % (mb, STOCK_MB, mb - STOCK_MB))
    log('  this is the NVN/GL pool -- textures, render targets, vertex and '
        'command memory. It is NOT the guest heap ff7nx_heap raises; the '
        'two are independent and both matter.')
    log('  the block is one malloc out of nnSdk\'s heap, which +0x1150DE0 '
        'sizes to ALL available application memory. If it does not fit, '
        'nv::InitializeGraphics aborts on the FIRST FRAME -- the game will '
        'not boot at all rather than fail subtly. Revert with '
        'patch_gfxpool_sdout.py --stock.')


def apply_to_nso(src, dest, log=lambda *_: None, mb: int = None) -> bool:
    """Patch `main` at `src` -> `dest`. Nothing is written on failure."""
    mb = pool_mb() if mb is None else mb
    why = encodable(mb)
    if why:
        log('! graphics pool: %s' % why)
        return False
    try:
        import nso_patcher
    except ImportError as exc:                                 # pragma: no cover
        log('! graphics pool: cannot import nso_patcher (%s)' % exc)
        return False
    img = _img(src)
    if not patches(img, mb):
        log('  already at %d MB; nothing to write' % mb)
        report(mb, log)
        return False
    bad = verify_site(src)
    if bad:
        for line in bad:
            log('! graphics pool: ' + line)
        log('  nothing was written; the module is unchanged')
        return False
    try:
        from pathlib import Path as _P
        nso = nso_patcher.read_nso(_P(str(src)))
        s = spec(img, mb)
        if s is None:
            return False
        applied = nso_patcher.apply_spec(nso, s)
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        log('! graphics pool: %s' % exc)
        log('  nothing was written; the module is unchanged')
        return False
    os.makedirs(os.path.dirname(os.path.abspath(str(dest))), exist_ok=True)
    with open(str(dest), 'wb') as f:
        f.write(data)
    for line in applied:
        log('  ' + line)
    report(mb, log)
    return True


# -------------------------------------------------------------- selftest
def selftest(log=print) -> bool:
    """Re-encode and re-decode the stock words before writing new ones."""
    ok = True
    checks = [
        ('mov w0, #0x10000000', 0x320403E0, encode_size(0, STOCK_BYTES)),
        ('mov w1, #0x10000000', 0x320403E1, encode_size(1, STOCK_BYTES)),
    ]
    for label, want, got in checks:
        good = (got == want)
        ok = ok and good
        log('  %-28s want %08X  got %s  %s'
            % (label, want, ('%08X' % got) if got is not None else 'None',
               'ok' if good else 'MISMATCH'))
    # every size we will ever write must survive encode -> decode
    for mb in sizes():
        for rd in (0, 1):
            w = encode_size(rd, mb * MB)
            back = decode_size(w, rd)
            good = (back == mb * MB)
            ok = ok and good
            if not good:
                log('  %-28s %d MB -> %08X -> %s  MISMATCH'
                    % ('encode/decode round trip', mb, w, back))
    log('  %-28s %s' % ('encode/decode round trip',
                        'ok for ' + ', '.join('%d' % m for m in sizes())
                        + ' MB' if ok else 'FAILED'))
    return ok


# ------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('nso', nargs='?', help='exefs/main (stock or patched)')
    ap.add_argument('--out')
    ap.add_argument('--mb', type=int, default=None)
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)

    if a.selftest or not a.nso:
        print('== encoder selftest')
        ok = selftest()
        print('== sizes this module can write:',
              ', '.join('%d' % m for m in sizes()), 'MB')
        return 0 if ok else 1

    if not selftest(lambda *_: None):
        print('! encoder selftest FAILED -- refusing to write anything')
        selftest()
        return 1

    have = read_mb(a.nso)
    print('module holds: %s'
          % ('%d MB' % have if have is not None else 'UNDECODABLE'))
    for line in verify_site(a.nso):
        print('! ' + line)
    if a.show:
        return 0

    mb = pool_mb() if a.mb is None else a.mb
    why = encodable(mb)
    if why:
        print('! %s' % why)
        return 1
    if not a.out:
        print('nothing written (no --out). Would set %d MB.' % mb)
        return 0
    ok = apply_to_nso(a.nso, a.out, print, mb)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())

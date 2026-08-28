#!/usr/bin/env python3
"""
ff7nx_glerror.py -- keep OpenGL error handling, remove only its fatal trap.

The port checks thirteen GL calls in UtilityPlatform.cpp.  Each wrapper is:

    bl  <GL call>
    bl  glGetError
    cbz w0, <continue>
    ... log the error ...
    udf #0xdefe                  ; 0xE7FFDEFE -- deliberate fatal trap
    bl  glGetError
    cbnz w0, <log/drain again>

The last three wrappers run in the presentation path reached by the tail call
from gfx_drv_flip.  That explains the otherwise-identical crash reports whose
last MaterialSX return address is +0x10DAB58.

The inherited build-187/188 patch changed the *first* glGetError at those
sites to ``mov w0, wzr``.  That suppressed the crash, but it also removed the
driver call and permanently bypassed the logger/drain loop.  Hardware then
showed persistent cross-mode texture corruption instead of a crash.  The old
claim that leaving a sticky GL error set "costs nothing" was incorrect.

This implementation patches only the explicit fatal instruction to NOP.  It
keeps both glGetError calls, the error log, and the complete drain loop.  On a
clean frame no patched instruction is reached.  On an error frame the port
reports and clears the error, then continues at the exact convergence address
the original cbz targets.  Existing modules with the legacy gate-skip patch
are migrated by restoring each stock ``bl glGetError`` while NOPing the trap.

``flip`` (the default) changes only the three hardware-observed end-of-frame
reporters.  ``all`` applies the same non-fatal treatment to all thirteen.
``off`` restores every gate and every fatal trap to stock.

    python3 ff7nx_glerror.py <main> --show
    python3 ff7nx_glerror.py <main> --mode flip --out <main.patched>
    python3 ff7nx_glerror.py <main> --mode all  --out <main.patched>
    python3 ff7nx_glerror.py <main> --mode off  --out <main.patched>
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

# ONLY this directory -- see the note in ff7nx_heap about what inserting
# the parent does to a working copy whose parent holds stale modules.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ---------------------------------------------------------------- constants
LEGACY_QUIET = 0x2A1F03E0   # mov w0, wzr -- build 187/188, must be removed
FATAL = 0xE7FFDEFE          # the deliberate undefined instruction
NOP = 0xD503201F

# Every gate: (va, stock `bl glGetError`, the `bl <gl call>` before it, name)
#
# The stock words differ per site because a BL encodes its own displacement,
# which is exactly what makes this table a fingerprint rather than a pattern
# match: thirteen different words, each only correct at its own address.
GATES = [
    (0x113C03C, 0x94005959, 0x9400580E, 'glGenFramebuffers'),
    (0x113C0AC, 0x9400593D, 0x94005822, 'glBindFramebuffer'),
    (0x113C11C, 0x94005921, 0x9400585A, 'glGenRenderbuffers'),
    (0x113C188, 0x94005906, 0x940057E7, 'glBindRenderbuffer'),
    (0x113C1F8, 0x940058EA, 0x94005827, 'glRenderbufferStorage'),
    (0x113C268, 0x940058CE, 0x94005787, 'glFramebufferRenderbuffer'),
    (0x113C320, 0x940058A0, 0x940057D9, 'glGenRenderbuffers'),
    (0x113C390, 0x94005884, 0x94005765, 'glBindRenderbuffer'),
    (0x113C400, 0x94005868, 0x940057A5, 'glRenderbufferStorage'),
    (0x113C474, 0x9400584B, 0x94005704, 'glFramebufferRenderbuffer'),
    (0x113C6C4, 0x940057B7, 0x9400569C, 'glBindFramebuffer  (end of frame)'),
    (0x113C75C, 0x94005791, 0x94005722, 'glBlitFramebuffer  (present)'),
    (0x113C7CC, 0x94005775, 0x9400565A, 'glBindFramebuffer  (end of frame)'),
]

# The fatal instruction belonging to each gate.  These were disassembled
# individually; the two additional UDFs at +0x113C30C/+0x113C51C are
# framebuffer-completeness assertions and deliberately are NOT in this table.
REPORT_TRAPS = {
    0x113C03C: 0x113C08C,
    0x113C0AC: 0x113C0FC,
    0x113C11C: 0x113C168,
    0x113C188: 0x113C1D8,
    0x113C1F8: 0x113C244,
    0x113C268: 0x113C2B4,
    0x113C320: 0x113C370,
    0x113C390: 0x113C3E0,
    0x113C400: 0x113C450,
    0x113C474: 0x113C4C4,
    0x113C6C4: 0x113C714,
    0x113C75C: 0x113C7AC,
    0x113C7CC: 0x113C81C,
}

# The three that were reproduced and fixed on hardware.
FLIP_VAS = (0x113C6C4, 0x113C75C, 0x113C7CC)

MODES = ('all', 'flip', 'off')

# A defect fix, not a quality preference.  The environment override is for
# controlled A/B work; the GUI intentionally does not expose it.
MODE = 'flip'
MODE_ENV = 'SEVENTH_NX_GL_ERROR_MODE'


def mode(env=None) -> str:
    raw = (os.environ if env is None else env).get(MODE_ENV)
    if raw is None or str(raw).strip() == '':
        return MODE
    v = str(raw).strip().lower()
    return v if v in MODES else MODE


def gates_for(m: str):
    if m == 'off':
        return []
    if m == 'flip':
        return [g for g in GATES if g[0] in FLIP_VAS]
    return list(GATES)


def _hex(word: int) -> str:
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def _img(main):
    if isinstance(main, (bytes, bytearray)):
        return main
    import nxmap
    return nxmap.Main(str(main)).img


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def is_cbz_w0(word: int) -> bool:
    """`cbz w0, <label>` -- 32-bit CBZ, Rt == 0. The whole safety argument."""
    return (word & 0xFF00001F) == 0x34000000


def is_cbnz_w0(word: int) -> bool:
    """`cbnz w0, <label>` -- the reporter's error-drain back edge."""
    return (word & 0xFF00001F) == 0x35000000


def is_bl(word: int) -> bool:
    return (word & 0xFC000000) == 0x94000000


def verify(main) -> list[str]:
    """Complaints about the module. Empty means every gate is ours to patch.

    Six things are checked at each site, and all six matter:
      * the `bl` BEFORE it is the exact GL call this gate belongs to, which
        is what proves the address is the right one;
      * the gate itself is stock or the legacy build-187/188 `mov`;
      * the word AFTER it is `cbz w0`;
      * the mapped fatal word is stock UDF or our NOP;
      * the second `glGetError` and its `cbnz w0` drain back-edge remain.
    """
    img = _img(main)
    bad = []
    for va, stock, before, name in GATES:
        if va + 8 > len(img):
            bad.append('+0x%07X is past the end of the module' % va)
            continue
        have_before = _word(img, va - 4)
        if have_before != before:
            bad.append('+0x%07X holds %08X, expected the `bl %s` %08X -- the '
                       'gate at +0x%07X is not where this table says'
                       % (va - 4, have_before, name, before, va))
        have = _word(img, va)
        if have not in (stock, LEGACY_QUIET):
            bad.append('+0x%07X holds %08X, expected the stock %08X or our '
                       'legacy %08X' % (va, have, stock, LEGACY_QUIET))
        after = _word(img, va + 4)
        if not is_cbz_w0(after):
            bad.append('+0x%07X holds %08X, which is not `cbz w0` -- without '
                       'that this patch would not be inert and it is refused'
                       % (va + 4, after))
        trap = REPORT_TRAPS[va]
        have_trap = _word(img, trap)
        if have_trap not in (FATAL, NOP):
            bad.append('+0x%07X holds %08X, expected reporter fatal %08X or '
                       'our NOP %08X' % (trap, have_trap, FATAL, NOP))
        drain = _word(img, trap + 4)
        if not is_bl(drain):
            bad.append('+0x%07X holds %08X, expected the second '
                       '`bl glGetError` drain call' % (trap + 4, drain))
        back = _word(img, trap + 12)
        if not is_cbnz_w0(back):
            bad.append('+0x%07X holds %08X, expected the `cbnz w0` error '
                       'drain back-edge' % (trap + 12, back))
    return bad


def read_state(main):
    """(nonfatal_count, total). None if a gate/trap is undecodable.

    A legacy gate-skip is intentionally not counted as the target state.
    """
    img = _img(main)
    n = 0
    for va, stock, _b, _n in GATES:
        if va + 4 > len(img):
            return None
        have = _word(img, va)
        if have not in (stock, LEGACY_QUIET):
            return None
        trap = _word(img, REPORT_TRAPS[va])
        if trap == NOP and have == stock:
            n += 1
        elif trap != FATAL or have != stock:
            return None
    return n, len(GATES)


def read_legacy_state(main):
    """Number of obsolete build-187/188 gate skips, or None if unknown."""
    img = _img(main)
    n = 0
    for va, stock, _b, _n in GATES:
        have = _word(img, va)
        if have == LEGACY_QUIET:
            n += 1
        elif have != stock:
            return None
    return n


def patches(img, m: str = None) -> list[dict]:
    m = mode() if m is None else m
    if m not in MODES:
        raise ValueError('unknown mode %r; expected one of %s'
                         % (m, ', '.join(MODES)))
    want_nonfatal = {g[0] for g in gates_for(m)}
    out = []
    for va, stock, _b, name in GATES:
        # Always restore legacy modules to the real glGetError call.
        cur = _word(img, va)
        if cur != stock:
            out.append({'name': 'restore glGetError after %s @ +0x%07X'
                                % (name, va),
                        'va': va, 'expect': _hex(cur), 'set': _hex(stock)})
        trap = REPORT_TRAPS[va]
        cur_trap = _word(img, trap)
        new_trap = NOP if va in want_nonfatal else FATAL
        if cur_trap != new_trap:
            out.append({'name': 'GL reporter after %s @ +0x%07X -> %s'
                                % (name, trap,
                                   'non-fatal' if new_trap == NOP else 'fatal'),
                        'va': trap, 'expect': _hex(cur_trap),
                        'set': _hex(new_trap)})
    return out


def spec(img, m: str = None) -> dict | None:
    ps = patches(img, m)
    if not ps:
        return None
    return {'name': 'OpenGL error reporter (%s)' % (mode() if m is None else m),
            'patches': ps}


def report(m: str = None, log=print) -> None:
    m = mode() if m is None else m
    picked = gates_for(m)
    log('  mode %s -- %d of %d reporter fatal trap(s) disabled'
        % (m, len(picked), len(GATES)))
    if m == 'off':
        log('  stock fatal behavior restored; GL checks and drain remain live')
        return
    if m == 'flip':
        log('  the three hardware-observed end-of-frame reporters are '
            'non-fatal; their glGetError checks, logs, and drain loops remain')
    else:
        log('  mode all also makes the ten render-target reporters non-fatal; '
            'all thirteen still log and drain every GL error')
    log('  legacy gate skips are removed automatically')


def apply_to_nso(src, dest, log=lambda *_: None, m: str = None) -> bool:
    m = mode() if m is None else m
    if m not in MODES:
        log('! GL error reporter: unknown mode %r' % m)
        return False
    try:
        import nso_patcher
    except ImportError as exc:                                 # pragma: no cover
        log('! GL error reporter: cannot import nso_patcher (%s)' % exc)
        return False
    img = _img(src)
    if not patches(img, m):
        log('  already in mode %s; nothing to write' % m)
        report(m, log)
        return False
    bad = verify(src)
    if bad:
        for line in bad:
            log('! GL error reporter: ' + line)
        log('  nothing was written; the module is unchanged')
        return False
    try:
        from pathlib import Path as _P
        nso = nso_patcher.read_nso(_P(str(src)))
        s = spec(img, m)
        if s is None:
            return False
        applied = nso_patcher.apply_spec(nso, s)
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        log('! GL error reporter: %s' % exc)
        log('  nothing was written; the module is unchanged')
        return False
    os.makedirs(os.path.dirname(os.path.abspath(str(dest))), exist_ok=True)
    with open(str(dest), 'wb') as f:
        f.write(data)
    for line in applied:
        log('  ' + line)
    report(m, log)
    return True


# -------------------------------------------------------------- selftest
def selftest(log=print) -> bool:
    """The encoder and the safety predicate, before anything is written."""
    ok = True
    checks = [
        ('legacy mov w0, wzr', 0x2A1F03E0, LEGACY_QUIET),
        ('reporter fatal word', 0xE7FFDEFE, FATAL),
        ('nop word', 0xD503201F, NOP),
        ('cbz w0 recognised', True, is_cbz_w0(0x340002E0)),
        ('cbz w1 rejected', False, is_cbz_w0(0x340002E1)),
        ('cbnz w0 rejected', False, is_cbz_w0(0x350002E0)),
        ('drain cbnz w0 recognised', True, is_cbnz_w0(0x350002E0)),
        ('cbz x0 rejected', False, is_cbz_w0(0xB40002E0)),
        ('13 gates', 13, len(GATES)),
        ('3 flip gates', 3, len(gates_for('flip'))),
        ('all == 13', 13, len(gates_for('all'))),
        ('off == 0', 0, len(gates_for('off'))),
        ('gate addresses unique', 13, len({g[0] for g in GATES})),
        ('stock words unique', 13, len({g[1] for g in GATES})),
        ('13 mapped traps', 13, len(REPORT_TRAPS)),
        ('trap addresses unique', 13, len(set(REPORT_TRAPS.values()))),
    ]
    for label, want, got in checks:
        good = (got == want)
        ok = ok and good
        log('  %-28s want %-10s got %-10s %s'
            % (label, want, got, 'ok' if good else 'MISMATCH'))
    return ok


# ------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('nso', nargs='?', help='exefs/main (stock or patched)')
    ap.add_argument('--out')
    ap.add_argument('--mode', choices=MODES, default=None)
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)

    if a.selftest or not a.nso:
        print('== selftest')
        return 0 if selftest() else 1
    if not selftest(lambda *_: None):
        print('! selftest FAILED -- refusing to write anything')
        selftest()
        return 1

    img = _img(a.nso)
    st = read_state(img)
    print('module: %s' % ('%d of %d fatal trap(s) disabled' % st
                          if st else 'UNDECODABLE'))
    for va, stock, _b, name in GATES:
        gate_state = ('legacy-skip' if _word(img, va) == LEGACY_QUIET
                      else 'live')
        trap_state = ('non-fatal' if _word(img, REPORT_TRAPS[va]) == NOP
                      else 'fatal')
        print('   +0x%07X  gate %-11s trap %-9s after %s'
              % (va, gate_state, trap_state, name))
    for line in verify(a.nso):
        print('! ' + line)
    if a.show:
        return 0
    m = mode() if a.mode is None else a.mode
    if not a.out:
        print('nothing written (no --out). Would set mode %s.' % m)
        return 0
    return 0 if apply_to_nso(a.nso, a.out, print, m) else 1


if __name__ == '__main__':
    raise SystemExit(main())

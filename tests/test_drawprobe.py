#!/usr/bin/env python3
"""BUILD 689 draw-probe check:
    python3 tests/test_drawprobe.py <main before the probe> <probe main>

Emulated (unicorn), 200 random states each:
  * every hooked driver entry: reaches entry+4 with the displaced word run,
    every register but x9..x17 unchanged, x30 restored
  * the menu wrapper: the original callee is entered with phase 1 and
    returns to the site; phase 2 after; x9/x10 as the callee left them,
    x19..x29, sp and NZCV unchanged
  * phase-2 calls land in the ring and the unique table with the right
    native caller, guest [esp], pass and frame
  * flip: the frame summary, the reset, then the booster edge dumps the
    registers / stack / TLS that drawprobe_read.py decodes back

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ['SEVENTH_NX_FRAME_PROBE'] = 'draw'
import nxmap  # noqa: E402
import ff7nx_drawprobe as D  # noqa: E402
import ff7nx_frameprobe as FP  # noqa: E402
import drawprobe_read as R  # noqa: E402
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_INTR  # noqa: E402
from unicorn.arm64_const import *  # noqa: E402,F403

X = [globals()['UC_ARM64_REG_X%d' % r] for r in range(29)] + [
    UC_ARM64_REG_X29, UC_ARM64_REG_X30]
STACK = 0x7000_0000
GLOB = 0x7100_0000
TLS = 0x7200_0000
GPAGE = 0x7300_0000
ESP = 0x00ABC010


class Emu:
    def __init__(self, img, base):
        self.mu = mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        size = (len(img) + 0xFFFF) & ~0xFFFF
        mu.mem_map(0, size)
        mu.mem_write(0, img)
        lo = base & ~0xFFF
        mu.mem_map(lo, ((base + D.BSS_BYTES + 0xFFF) & ~0xFFF) - lo + 0x1000)
        self.base = base
        mu.mem_map(STACK - 0x10000, 0x20000)
        mu.mem_map(GLOB, 0x10000)
        mu.mem_map(TLS, 0x1000)
        mu.mem_map(GPAGE, 0x1000)
        mu.reg_write(UC_ARM64_REG_TPIDRRO_EL0, TLS)
        # GOT: ctx, page table, pass
        mu.mem_write(D.GOT_PAGE + D.GOT_CTX, struct.pack('<Q', GLOB))
        mu.mem_write(GLOB + 0x10, struct.pack('<I', ESP))
        mu.mem_write(D.GOT_PAGE + D.GOT_PAGES, struct.pack('<Q', GLOB + 0x1000))
        mu.mem_write(GLOB + 0x1000 + 8 * (ESP >> 12),
                     struct.pack('<Q', GPAGE))
        mu.mem_write(D.GOT_PAGE + D.GOT_PASS, struct.pack('<Q', GLOB + 0x100))
        mu.mem_write(D.GOT_PAGE + D.GOT_DEPTH, struct.pack('<Q', GLOB + 0x200))
        mu.mem_write(D.GOT_PAGE + D.GOT_COLOUR, struct.pack('<Q', GLOB + 0x208))
        mu.mem_write(D.GOT_PAGE + D.GOT_PENDING, struct.pack('<Q', GLOB + 0x210))
        self.set_pass(7)
        self.set_gret(0x41BD7B)
        self.set_mult(1)

    def set_pass(self, v):
        self.mu.mem_write(GLOB + 0x100, struct.pack('<I', v))

    def set_gret(self, v):
        self.mu.mem_write(GPAGE + (ESP & 0xFFF), struct.pack('<I', v))

    def set_targets(self, depth, colour, pending):
        self.mu.mem_write(GLOB + 0x200, struct.pack('<QQB', depth, colour,
                                                    pending))

    def set_phase(self, v):
        self.mu.mem_write(self.base + D.PHASE, struct.pack('<I', v))

    def set_mult(self, v):
        self.mu.mem_write(D.MULT, struct.pack('<I', v))

    def bss(self, off, fmt='<I'):
        n = struct.calcsize(fmt)
        return struct.unpack(fmt, self.mu.mem_read(self.base + off, n))

    def randregs(self, rnd):
        regs = {}
        for r in range(31):
            regs[r] = rnd.getrandbits(64)
            self.mu.reg_write(X[r], regs[r])
        return regs

    def regs(self):
        return {r: self.mu.reg_read(X[r]) for r in range(31)}


def call_hook(e, rnd, hid, entry, word, lr, x0=None):
    sp = STACK - 0x400 - 16 * rnd.randrange(8)
    e.mu.reg_write(UC_ARM64_REG_SP, sp)
    before = e.randregs(rnd)
    before[30] = lr
    e.mu.reg_write(X[30], lr)
    if x0 is not None:
        before[0] = x0
        e.mu.reg_write(X[0], x0)
    e.mu.emu_start(entry, entry + 4, count=400)
    bad = []
    after = e.regs()
    for r in list(range(0, 9)) + list(range(18, 31)):
        if after[r] != before[r]:
            bad.append('%s: x%d changed' % (D.NAMES[hid], r))
    want = sp
    if (word & 0xFF8003FF) == 0xD10003FF:            # sub sp, sp, #imm
        want = sp - ((word >> 10) & 0xFFF)
    elif word in (0x6DBB2BEB,):
        want = sp - 0x50
    elif word == 0xF81D0FF5:
        want = sp - 0x30
    elif word == 0xFC1C0FE8:
        want = sp - 0x40
    elif word == 0xF81E0FF3:
        want = sp - 0x20
    if e.mu.reg_read(UC_ARM64_REG_SP) != want:
        bad.append('%s: sp %#x, want %#x' % (D.NAMES[hid],
                                            e.mu.reg_read(UC_ARM64_REG_SP),
                                            want))
    return bad


def call_menu(e, rnd, site, callee):
    sp = STACK - 0x400 - 16 * rnd.randrange(8)
    e.mu.reg_write(UC_ARM64_REG_SP, sp)
    before = e.randregs(rnd)
    nz = rnd.randrange(16) << 28
    e.mu.reg_write(UC_ARM64_REG_NZCV, nz)
    seen = {}

    def hook(uc, addr, size_, _):
        if addr == callee:
            seen['phase'] = e.bss(D.PHASE)[0]
            seen['x9x10'] = (uc.reg_read(X[9]), uc.reg_read(X[10]))
            seen['lr'] = uc.reg_read(X[30])
            seen['sp'] = uc.reg_read(UC_ARM64_REG_SP)
            for r in range(0, 18):
                uc.reg_write(X[r], 0xDEAD0000 + r)
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(X[30]))
    h = e.mu.hook_add(UC_HOOK_CODE, hook)
    e.mu.emu_start(site, site + 4, count=400)
    e.mu.hook_del(h)
    bad = []
    if seen.get('phase') != 1:
        bad.append('menu: phase %r inside' % seen.get('phase'))
    if seen.get('x9x10') != (before[9], before[10]):
        bad.append('menu: callee did not see x9/x10')
    if seen.get('sp', 1) % 16:
        bad.append('menu: sp misaligned at callee')
    if e.bss(D.PHASE)[0] != 2:
        bad.append('menu: phase not 2 after')
    after = e.regs()
    if (after[9], after[10]) != (0xDEAD0009, 0xDEAD000A):
        bad.append('menu: x9/x10 not the callee\'s')
    for r in range(19, 30):
        if after[r] != before[r]:
            bad.append('menu: x%d changed' % r)
    if e.mu.reg_read(UC_ARM64_REG_SP) != sp:
        bad.append('menu: sp changed')
    if e.mu.reg_read(UC_ARM64_REG_NZCV) & 0xF0000000 != nz:
        bad.append('menu: flags changed')
    return bad


def call_flip(e, rnd):
    sp = STACK - 0x400
    e.mu.reg_write(UC_ARM64_REG_SP, sp)
    before = e.randregs(rnd)
    out = {}

    def intr(uc, intno, _):
        out['regs'] = e.regs()
        out['sp'] = uc.reg_read(UC_ARM64_REG_SP)
        out['stack'] = bytes(uc.mem_read(out['sp'], 0x100))
        out['tls'] = bytes(uc.mem_read(TLS, 0x100))
        uc.emu_stop()
    h = e.mu.hook_add(UC_HOOK_INTR, intr)
    e.mu.emu_start(D.FLIP, D.FLIP + 4, count=2000)
    e.mu.hook_del(h)
    bad = []
    if 'regs' not in out:
        after = e.regs()
        for r in list(range(0, 9)) + list(range(18, 31)):
            if after[r] != before[r]:
                bad.append('flip: x%d changed' % r)
        if e.mu.reg_read(UC_ARM64_REG_SP) != sp - 0x40:
            bad.append('flip: sp')
    return bad, out


def fake_report(out):
    lines = ['Crashed Thread Info:', '    Registers:']
    r = out['regs']
    for i in range(29):
        lines.append('        X[%02d]:           %016x' % (i, r[i]))
    lines.append('        FP:              %016x' % r[29])
    lines.append('        LR:              %016x' % r[30])
    for title, blob, at in (('Stack Dump:', out['stack'], out['sp']),
                            ('TLS Dump:', out['tls'], TLS)):
        lines.append('    ' + title)
        for i in range(0, len(blob), 16):
            lines.append('        %016x %s' % (at + i, ' '.join(
                '%02x' % b for b in blob[i:i + 16])))
    return '\n'.join(lines) + '\nX\n'


def main():
    pre, probe = sys.argv[1], sys.argv[2]
    mp, mq = nxmap.Main(pre), nxmap.Main(probe)
    callee = FP._bl_target(struct.unpack_from('<I', mp.img, D.MENU_SITE)[0],
                           D.MENU_SITE)
    wrapper = FP._bl_target(struct.unpack_from('<I', mq.img, D.MENU_SITE)[0],
                            D.MENU_SITE)
    assert callee and wrapper and wrapper != callee
    # the wrapper really calls the old callee
    words = struct.unpack_from('<64I', mq.img, wrapper)
    assert any(FP._bl_target(w, wrapper + 4 * i) == callee
               for i, w in enumerate(words)), 'wrapper does not call callee'
    # only the hooked words, the menu site and the dead part differ
    import ff7nx_deadspace
    lo, hi = ff7nx_deadspace.part(pre, 'probe', None)
    allowed = {D.MENU_SITE, D.FLIP} | {h[1] for h in D.HOOKS}
    a, b = bytes(mp.img), bytes(mq.img)
    for i in range(0, min(len(a), len(b)), 4):
        if a[i:i + 4] != b[i:i + 4]:
            assert i in allowed or lo <= i < hi, hex(i)
    base = None
    # the BSS base: find it from the common recorder's adrp/add
    import re  # noqa: F401
    st = os.environ.get('DRAWPROBE_BASE')
    with open(probe, 'rb') as handle:
        blob = handle.read()
    import ff7nx_audio_cave as AC
    segs, _raw = AC.segments(blob)
    with open(pre, 'rb') as handle:
        pblob = handle.read()
    psegs, _ = AC.segments(pblob)
    base = (AC.scratch_base(pblob, psegs) + 15) & ~15
    img = bytes(mq.img)
    fails = 0
    H = {h[0]: h for h in D.HOOKS}

    def hk(e, rnd, hid, lr, x0=None):
        _, entry, word, _d = H[hid]
        return call_hook(e, rnd, hid, entry, word, lr, x0)

    for seed in range(200):
        rnd = random.Random(seed)
        e = Emu(img, base)
        bad = []
        lrs = [0x8FB00 + 4 * rnd.randrange(0x700) for _ in range(12)]
        dep, col = rnd.getrandbits(48) << 4, rnd.getrandbits(48) << 4
        dep2 = dep ^ 0x5550

        def frame(glitch):
            b = []
            e.set_targets(dep, col, 0)
            e.set_pass(7)
            for k in range(10):                    # 10 calls before the menu
                b += hk(e, rnd, 6 if k % 2 else 3, lrs[k])
            b += hk(e, rnd, 9, lrs[0])                 # blit check: not kept
            b += call_menu(e, rnd, D.MENU_SITE, callee)
            e.set_phase(1)
            b += hk(e, rnd, 8, lrs[10])
            b += hk(e, rnd, 11, lrs[11], x0=8)
            e.set_pass(8)
            for k in range(3):
                if glitch and k == 1:
                    e.set_targets(dep2, col, 1)
                b += hk(e, rnd, 1, lrs[k])
                b += hk(e, rnd, 9, lrs[0])
            e.set_phase(2)
            b += hk(e, rnd, 13, lrs[0])
            b += hk(e, rnd, 9, lrs[0])
            b2, out = call_flip(e, rnd)
            return b + b2, out

        for g in (0, 0, 0, 0, 0, 1, 0, 0):
            b, out = frame(g)
            bad += b
            if out:
                bad.append('dumped without the edge')
        if e.bss(D.NSIG)[0] != 2:
            bad.append('%d patterns' % e.bss(D.NSIG)[0])
        meta0 = e.bss(D.META, '<IIHHBBBB')
        if meta0[1] != 7 or meta0[2] != 0 or meta0[3] != 7 or meta0[4] != 5:
            bad.append('pattern 0 meta %r' % (meta0,))
        if meta0[5:] != (11, 6, 1):
            bad.append('pattern 0 counts %r' % (meta0[5:],))
        e.set_mult(3)
        b, out = frame(0)
        bad += b
        if not out:
            bad.append('no dump on the edge')
        else:
            dec = R.decode(fake_report(out))
            if (dec['frames'], dec['menufr'], dec['fw2'], dec['nsig'],
                    dec['imax'], dec['imin']) != (9, 9, 9, 2, 0, 1):
                bad.append('decoded stats %r' % ({k: dec[k] for k in (
                    'frames', 'menufr', 'fw2', 'nsig', 'imax', 'imin')},))
            pats = dec['patterns']
            if [(q['count'], q['first'], q['last']) for q in pats] != [
                    (8, 0, 8), (1, 5, 5)]:
                bad.append('decoded patterns %r' % pats)
            for name in ('det_max', 'det_min'):
                det = [R.ev(x) for x in dec[name]]
                before = [(d['id'], d['lr']) for d in det[:8]]
                want = [(6 if k % 2 else 3, lrs[k]) for k in range(2, 10)]
                if before != want:
                    bad.append('%s before %r' % (name, before))
                inside = [(d['id'], d['lr'], d['pass'], d['pending'],
                           d['depth'], d['colour']) for d in det[8:13]]
                g = name == 'det_min'
                want = [(8, lrs[10], 7, 0, dep & 0xFFFF, col & 0xFFFF),
                        (11, lrs[11], 8, 0, dep & 0xFFFF, col & 0xFFFF)]
                for k in range(3):
                    gl = g and k >= 1
                    want.append((1, lrs[k], 8, int(gl),
                                 (dep2 if gl else dep) & 0xFFFF,
                                 col & 0xFFFF))
                if inside != want:
                    bad.append('%s inside %r' % (name, inside))
                if any(dec[name][13:]):
                    bad.append('%s tail not empty' % name)
            if [R.ev(x)['id'] for x in dec['newest'] if x] != [8, 11, 1, 1, 1, 0][:5]:
                bad.append('newest %r' % dec['newest'])
            if seed == 0:
                R.report(dec, R.Mapper(pre))
        if bad:
            fails += 1
            if fails < 5:
                print('seed %d: %s' % (seed, '; '.join(bad[:6])))
    print('draw probe: %d hooks, menu wrapper +0x%X -> +0x%X: %d/200 failures'
          % (len(D.HOOKS), wrapper, callee, fails))
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""
test_nocheats.py -- the two input tweaks, checked against the real module.

    python3 test_nocheats.py [--nso path/to/exefs/main]

Both are tiny, and both rest entirely on claims about the STOCK module rather
than on any logic of their own, so that is what this checks: the words at the
sites, the encodings, and the two structural facts the `no-cheats` patch is
argued from -- that the module reads nn::hid in exactly one place, and that the
DirectInput key loop never maps the stick clicks.

BUILD 736 adds the booster function: its words at the patched row, and the
function itself EXECUTED (arm64emu, the two accessors modelled from their
disassembly) over press/release sequences, stock and patched.

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
import argparse
import struct
import sys

import capstone

import ff7nx_nocheats as NC

MD = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
FAIL = []


def ok(cond, what):
    print(('  ok  ' if cond else '  FAIL  ') + what)
    if not cond:
        FAIL.append(what)


def dis(word, at=0):
    i = next(MD.disasm(struct.pack('<I', word), at))
    return (i.mnemonic + ' ' + i.op_str).strip()


L3, R3 = 1 << 4, 1 << 5
# (speed multiplier, no-battle byte, booster byte) after each gesture, patched
_WANT = {
    'L3 tap': (3, 0, 0),
    'R3 tap': (1, 0, 0),
    'L3 held long, then released': (3, 0, 0),
    'L3, then R3; R3 up first': (1, 1, 0),
    'L3, then R3; L3 up first': (1, 1, 0),
    'R3, then L3; both up together': (1, 1, 0),
    'both down and up together': (1, 1, 0),
    'L3+R3 twice': (1, 0, 0),
}
_SEQ = {
    'L3 tap': [L3, 0],
    'R3 tap': [R3, 0],
    'L3 held long, then released': [L3, L3, L3, L3, 0],
    'L3, then R3; R3 up first': [L3, L3 | R3, L3, 0],
    'L3, then R3; L3 up first': [L3, L3 | R3, R3, 0],
    'R3, then L3; both up together': [R3, R3 | L3, 0],
    'both down and up together': [L3 | R3, 0],
    'L3+R3 twice': [L3 | R3, 0, 0, L3, L3 | R3, L3, 0],
}


def _booster(img, patched):
    """Run 0x1880 a frame at a time over each gesture in _SEQ."""
    import arm64emu as EM
    code = {a: struct.unpack_from('<I', img, a)[0]
            for a in range(NC.BOOSTER_FN, 0x1AD0, 4)}
    if patched:
        code[NC.BOOSTER_R3_SITE] = NC.B_LATCH_RET

    def bit(i):
        return struct.unpack_from('<I', img, 0x11DDAE4 + 4 * (i & 0xFFFFFFFF))[0]

    def held(cpu):                    # 0x111BE00: cur & bit
        o = cpu.x[0]
        cpu.x[0] = (cpu.mem.u(o + 0x20, 8) >> bit(cpu.x[1])) & 1

    def released(cpu):                # 0x111BEE0: prev & ~cur & bit
        o = cpu.x[0]
        c, p = cpu.mem.u(o + 0x20, 8), cpu.mem.u(o + 0x28, 8)
        cpu.x[0] = ((p & ~c) >> bit(cpu.x[1])) & 1

    out = {}
    for name, frames in _SEQ.items():
        mem = EM.Mem()
        MGR, DEV, VT = 0x500000, 0x500300, 0x500400
        MULT, NOB, BB = 0x600000, 0x600010, 0x600020
        for slot, v in ((0x12CE1D0, 0x500050), (0x12CE1D8, NOB),
                        (0x12CE1E0, BB), (0x12CE1A8, MULT)):
            mem.setu(slot, v, 8)
        mem.setu(0x500050, MGR, 8)
        mem.setu(MGR + 8, 0x500100, 8)
        mem.setu(0x500100, 0x500200, 8)
        mem.setu(0x500200 + 0x88, DEV, 8)
        mem.setu(DEV, VT, 8)
        mem.setu(VT + 0x38, 0x111BE00, 8)
        mem.setu(VT + 0x48, 0x111BEE0, 8)
        mem.setu(MULT, 1, 4)
        prev = 0
        for cur in [0] + frames + [0]:
            # what the poll cave hands the object: bit 5 kept, bit 62 added
            cur = NC.apply_body(cur)
            mem.setu(DEV + 0x20, cur, 8)
            mem.setu(DEV + 0x28, prev, 8)
            cpu = EM.Cpu(mem, native={0x111BE00: held, 0x111BEE0: released})
            cpu.sp, cpu.x[30] = 0x7FF000, 0xDEAD0
            assert cpu.run(NC.BOOSTER_FN, code) == 0xDEAD0
            prev = cur
        out[name] = (mem.u(MULT, 4), mem.u(NOB, 1), mem.u(BB, 1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', default='dump/exefs/main')
    a = ap.parse_args(argv)

    print('encodings')
    ok(dis(NC.NOP) == 'nop', 'the replacement is a nop')
    body = [dis(w) for w in NC.nocheats_body()]
    ok(body == ['ror x13, x13, #5', 'bfi x13, x13, #0x39, #1',
                'ror x13, x13, #0x3b'],
       'the cave body is ror/bfi/ror on x13 alone: %s' % body)
    for word in (0x20, 0x21 | (1 << 9), 0, 0xFFFFFFFF, 0xFFFFDFFF):
        want = word | (((word >> 5) & 1) << NC.R3_SHADOW_BIT)
        ok(NC.apply_body(word) == want,
           'StickR (bit 5) of %#x is copied to bit %d, nothing else changes'
           % (word, NC.R3_SHADOW_BIT))
    ok(dis(NC.B_LATCH_RET, NC.BOOSTER_R3_SITE) == 'b #0x%x'
       % NC.BOOSTER_LATCH_RET,
       'the booster patch is `b 0x%X`' % NC.BOOSTER_LATCH_RET)

    try:
        import nxmap
        m = nxmap.Main(a.nso)
    except SystemExit:
        raise
    except Exception as exc:
        print('\nmodule tests SKIPPED -- pass --nso /path/to/exefs/main (%s)'
              % exc)
        return 1 if FAIL else 0
    img = m.img

    def word(at):
        return struct.unpack_from('<I', img, at)[0]

    print()
    print('the stock module says what the patch assumes')
    ok(word(NC.AUTORUN_SITE) == NC.AUTORUN_ORIG,
       'auto-run site 0x%X is `%s`' % (NC.AUTORUN_SITE, dis(NC.AUTORUN_ORIG)))
    for va in NC.BUTTON_STORES:
        ok(word(va) == NC.BUTTON_STORE_ORIG,
           'button store 0x%X is `%s`' % (va, dis(NC.BUTTON_STORE_ORIG)))

    # exactly one nn::hid read in the whole module
    def callers(tgt):
        out = []
        for off in range(0, 0x1152660, 4):
            w = word(off)
            if (w & 0xFC000000) != 0x94000000:
                continue
            d = w & 0x3FFFFFF
            if d & 0x2000000:
                d -= 0x4000000
            if off + d * 4 == tgt:
                out.append(off)
        return out

    full = callers(0x11517E0)          # GetNpadState(NpadFullKeyState&, ...)
    hand = callers(0x11517F0)          # GetNpadState(NpadHandheldState&, ...)
    ok(full == [0x111C028] and hand == [0x111C0C0],
       'nn::hid is read in exactly ONE place: %s -- so obj+0x20 is the only '
       'copy of the physical buttons in the module'
       % ['0x%X' % x for x in full + hand])
    ok(all(0x111BFC0 < x < 0x111C1D8 for x in full + hand),
       'and both reads are inside the poll this patch hooks')

    # the key loop skips ids 0 and 1
    ok(word(0x10D3968) == 0x51000908 and word(0x10D396C) == 0x7100451F,
       'the DirectInput key loop takes ids 2..0x13 only, so the two stick '
       'clicks (ids 0 and 1) map to no scancode')
    tbl = 0x11DDAE4
    ok(struct.unpack_from('<I', img, tbl)[0] == 4
       and struct.unpack_from('<I', img, tbl + 4)[0] == 5,
       'and the id->bit table maps id 0 to StickL (bit 4), id 1 to StickR '
       '(bit 5)')

    # nothing else reads bit 5
    hits = []
    pos, end = 0, 0x1152660
    while pos < end:
        got = False
        for i in MD.disasm(img[pos:end], pos):
            got = True
            pos = i.address + 4
            if i.mnemonic in ('tst', 'ands', 'and') and i.op_str.startswith('x') \
                    and i.op_str.endswith('#0x20'):
                hits.append(i.address)
        if not got:
            pos += 4
    ok(not hits,
       'no code anywhere tests bit 5 of a 64-bit value directly, so the '
       'accessors are the only way the module can see StickR')

    print()
    print('the booster function (0x%X)' % NC.BOOSTER_FN)
    ok(word(NC.BOOSTER_R3_SITE) == NC.BOOSTER_R3_ORIG,
       'the R3-alone row starts at 0x%X with `%s`'
       % (NC.BOOSTER_R3_SITE, dis(NC.BOOSTER_R3_ORIG, NC.BOOSTER_R3_SITE)))
    ok(word(NC.BOOSTER_R3_SITE + 4) == 0xF940F108,
       'and loads GOT slot 0x12CE1E0 (the booster byte) from it')
    ok(word(0x1A90) == 0x39400109 and word(0x1A94) == 0x52000129
       and word(0x1A98) == 0x39000109,
       'the shared ldrb/eor/strb at 0x1A90 is left alone')
    ok(word(0x19DC) == 0x1400002D,
       'because the no-battle rows reach it from 0x19DC')
    ok(word(NC.BOOSTER_LATCH_RET) == 0x320003E8
       and word(NC.BOOSTER_LATCH_RET + 4) == 0x3903C268,
       '0x%X sets the latch (mov w8,#1; strb w8,[x19,#0xf0]) and returns'
       % NC.BOOSTER_LATCH_RET)
    for k, lbl in enumerate(('stock', 'patched')):
        res = _booster(img, patched=bool(k))
        want = dict(_WANT)
        if not k:
            want['R3 tap'] = (1, 0, 1)
        for name, got in res.items():
            ok(got == want[name], '%-7s %-34s -> speed x%d, no-battle %d, '
               'booster %d' % (lbl, name, got[0], got[1], got[2]))

    print()
    if FAIL:
        print('%d FAILED' % len(FAIL))
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())

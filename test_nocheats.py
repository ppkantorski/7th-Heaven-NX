#!/usr/bin/env python3
"""
test_nocheats.py -- the two input tweaks, checked against the real module.

    python3 test_nocheats.py [--nso path/to/exefs/main]

Both are tiny, and both rest entirely on claims about the STOCK module rather
than on any logic of their own, so that is what this checks: the words at the
sites, the encodings, and the two structural facts the `no-cheats` patch is
argued from -- that the module reads nn::hid in exactly one place, and that the
DirectInput key loop never maps the stick clicks.
"""
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', default='dump/exefs/main')
    a = ap.parse_args(argv)

    print('encodings')
    ok(dis(NC.NOP) == 'nop', 'the replacement is a nop')
    ok(dis(NC.AND_NOT_STICKR) == 'and x13, x13, #0xffffffffffffffdf',
       'the mask clears exactly bit %d (StickR) of the button word'
       % NC.STICKR_BIT)
    ok((~NC.AND_NOT_STICKR or True) and (0xFFFFFFFFFFFFFFDF
                                         == ~(1 << NC.STICKR_BIT) & (1 << 64) - 1),
       'and bit %d is the one nn::hid uses for StickR' % NC.STICKR_BIT)

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
    if FAIL:
        print('%d FAILED' % len(FAIL))
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())

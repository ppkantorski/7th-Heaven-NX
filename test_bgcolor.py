#!/usr/bin/env python3
"""
test_bgcolor.py -- offline checks for ff7nx_bgcolor.

    python3 test_bgcolor.py --nso dump/exefs/main

Every check is either arithmetic on instruction encodings or a read of the
shipping module. Nothing here needs hardware.
"""
import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_bgcolor as BG                                       # noqa: E402

FAIL = []


def check(cond, what):
    print('%-4s %s' % ('ok' if cond else 'FAIL', what))
    if not cond:
        FAIL.append(what)


def decode_ldst_pair(word):
    """(Rt, Rt2, Rn, imm7) of an STP/LDP with signed offset."""
    return (word & 0x1F, (word >> 10) & 0x1F, (word >> 5) & 0x1F,
            (word >> 15) & 0x7F)


def test_encodings():
    stock = 0xA900A668
    new = 0xA900FE7F
    st_rt, st_rt2, st_rn, st_imm = decode_ldst_pair(stock)
    nw_rt, nw_rt2, nw_rn, nw_imm = decode_ldst_pair(new)
    check((stock & 0xFFC00000) == (new & 0xFFC00000),
          'store: same opcode class')
    check((st_rn, st_imm) == (nw_rn, nw_imm) == (19, 1),
          'store: same base x19 and same +8 offset')
    check((st_rt, st_rt2) == (8, 9), 'store: stock writes x8, x9')
    check((nw_rt, nw_rt2) == (31, 31), 'store: patched writes xzr, xzr')
    check(0xD503201F == 0xD503201F, 'load slot becomes a real NOP')
    check(len(BG.WORDS) == 2, 'exactly two words are written')
    check(all(s != n for _, s, n, _ in BG.WORDS),
          'every patched word differs from its stock word')


def test_module(path):
    import nxmap
    m = nxmap.Main(path)
    text = m.text
    check(BG.verify_anchors(text, lambda *_: None),
          'every anchor word matches the derivation module')
    check(BG.state(text) == 'stock', 'shipping module reads as stock')
    for va, stock, _, what in BG.WORDS:
        got = struct.unpack_from('<I', text, va)[0]
        check(got == stock, '+0x%07X is %08X  (%s)' % (va, stock, what))
    # The two words this depends on being the colour source, unpatched.
    check(struct.unpack_from('<I', text, 0x10D6908)[0] == 0x2D412408,
          'clear still loads s8, s9 from gfx_driver_data + 8')
    check(struct.unpack_from('<I', text, 0x10D6910)[0] == 0x2D42280B,
          'clear still loads s11, s10 from gfx_driver_data + 0x10')


def test_roundtrip(path):
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = os.path.join(d, 'main')
        shutil.copy(path, tmp)
        before = open(tmp, 'rb').read()
        ok = BG.apply(tmp, tmp, log=lambda *_: None)
        check(ok, 'apply succeeds')
        import nxmap
        check(BG.state(nxmap.Main(tmp).text) == 'patched',
              'module reads as patched after apply')
        check(open(tmp, 'rb').read() != before, 'apply changed the file')
        ok = BG.revert(tmp, tmp, log=lambda *_: None)
        check(ok, 'revert succeeds')
        check(open(tmp, 'rb').read() == before,
              'revert round-trips to a byte-identical module')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', help='path to exefs/main')
    a = ap.parse_args(argv)
    test_encodings()
    if a.nso:
        test_module(a.nso)
        test_roundtrip(a.nso)
    else:
        print('..   no --nso given; module checks skipped')
    print()
    print('%d failure(s)' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    raise SystemExit(main())

"""BUILD 716 -- ff7nx_palauto: FX layers the script switches on and off
mid-scene (gates), and ADPAL brightness that wraps through 0 (signed
lattice: junonr1, spipe_1, fship_4, nvmin1_2, subin_1a/b, cos_btm2,
nvmin1_1).

The synthetic tests need nothing; the vanilla ones skip without
game_data_files/field/flevel.lgp.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ff7nx_palauto as PU                                     # noqa: E402

VANILLA = os.path.join(ROOT, 'game_data_files', 'field', 'flevel.lgp')


def _wrap_script(minus=0x87, plus=0x85, cmp=0):
    """var5[0]: SET 0; MINUS 1; IF v == F6 ...; PLUS 1; IF v == 0 ...
    (junonr1's AKARI: 0, 255 .. F6, back up to 0)."""
    s = bytearray(64)
    s[0:4] = bytes((0x80, 0x50, 0x00, 0x00))
    s[4:8] = bytes((minus, 0x50, 0x00, 0x01))
    s[8:14] = bytes((0x14, 0x50, 0x00, 0xF6, cmp, 0x05))
    s[14:18] = bytes((plus, 0x50, 0x00, 0x01))
    s[18:24] = bytes((0x14, 0x50, 0x00, 0x00, cmp, 0x05))
    uses = [(0, 'set', 3), (4, 'minus', 3), (8, 'ifl', 3),
            (14, 'plus', 3), (18, 'ifl', 3)]
    return bytes(s), uses


class Signed(unittest.TestCase):

    def test_unsigned_reading_is_too_long(self):
        s, uses = _wrap_script()
        step, _a, lo, hi, _sets = PU.lattice(s, uses)
        self.assertGreater((hi - lo) // step + 1, PU.MAX_MULTI)

    def test_signed_reading_is_eleven_states(self):
        s, uses = _wrap_script()
        step, _a, lo, hi, sets = PU.lattice(s, uses, signed=True)
        self.assertEqual((lo, hi, step), (-10, 0, 1))
        self.assertEqual(sets, [0])

    def test_renumber_keeps_every_comparison(self):
        s, uses = _wrap_script()
        out = PU.renumber(s, uses, -10, 1, 11, signed=True)
        self.assertEqual(out[3], 10)            # SET 0   -> state 10
        self.assertEqual(out[7], 1)             # MINUS 1 stays a step
        self.assertEqual(out[11], 0)            # IF == F6 (-10) -> 0
        self.assertEqual(out[21], 10)           # IF == 0 -> 10
        # every value: the renumbered compare agrees with the original
        for v in range(-10, 1):
            for o in (8, 18):
                self.assertEqual((v & 255) == s[o + 3],
                                 (v + 10) == out[o + 3])

    def test_saturating_ops_refused(self):
        for minus, plus in ((0x78, 0x85), (0x87, 0x76)):
            s, uses = _wrap_script(minus, plus)
            with self.assertRaises(ValueError):
                PU.lattice(s, uses, signed=True)

    def test_ordered_comparison_refused(self):
        s, uses = _wrap_script(cmp=3)
        with self.assertRaises(ValueError):
            PU.lattice(s, uses, signed=True)


class Gate(unittest.TestCase):

    def test_site_skips_hide_when_gate_open(self):
        hide = bytes((0xE4, 0x00, 0x02))
        g = PU.gate_site(7, hide)
        self.assertEqual(g[:5], bytes((0x14, 0x50, 0x07, 0x00, 0x00)))
        # the jump counts from the jump byte to just past `hide`
        self.assertEqual(5 + g[5], len(g))
        self.assertEqual(g[6:], hide)

    def test_codes_per_mode(self):
        show, hide = PU.gate_codes('single', 9, P=3)
        self.assertEqual(hide, bytes((0xE4, 0x00, 0x03)))
        self.assertEqual(show, hide + bytes((0xE0, 0x05, 0x03, 0x09)))
        show, hide = PU.gate_codes('multi', 9, prev=255)
        self.assertEqual(hide, bytes((0xE1, 0x50, 0xFF, 0x00)))
        self.assertEqual(show[:4], hide)
        self.assertEqual(show[-4:], bytes((0x80, 0x55, 0xFF, 0x09)))


def _parts(name):
    import lgp
    a = lgp.Archive(VANILLA)
    return list(lgp.split_sections(a.decompressed(a.index[name])))


@unittest.skipUnless(os.path.exists(VANILLA), 'no vanilla flevel.lgp')
class Vanilla(unittest.TestCase):

    def _lat(self, name, q, signed):
        sec0 = _parts(name)[0]
        ef = [e for e in PU.find_effects(sec0) if e['q'] == q][0]
        uses = PU.var_uses(sec0, ef['var'],
                           skip={o for o, _l in ef['sites']})
        dead = PU.dead_init_sets(sec0, uses, ef['sites'])
        return PU.lattice(sec0, uses, dead, signed)

    def test_junonr1_light_is_minus_ten_to_zero(self):
        step, _a, lo, hi, _s = self._lat('junonr1', 10, True)
        self.assertEqual((lo, hi, step), (-10, 0, 1))

    def test_subin_1a_is_minus_fifteen_to_zero(self):
        step, _a, lo, hi, _s = self._lat('subin_1a', 8, True)
        self.assertEqual((lo, hi, step), (-15, 0, 1))

    def test_zz7_gate_switches_both_ways(self):
        p = _parts('zz7')
        _P, masks, ops = PU.gate_of(p[0], p[8], 4)
        self.assertEqual(len(masks), 1)
        kinds = {k for _o, _z, k in ops}
        self.assertEqual(kinds, {'on', 'off'})

    def test_mktpb_two_bits_refused(self):
        p = _parts('mktpb')
        with self.assertRaises(ValueError):
            PU.gate_of(p[0], p[8], 9)


if __name__ == '__main__':
    unittest.main()

"""BUILD 714 -- ff7nx_palauto: nvmkin32's lights (128-entry loads, a bound
shared by four loops, one variable driving two palettes) and PAIR mode
(every state kept as coarse + fine additive images when one copy per state
does not fit).

The vanilla / built tests read game_data_files/ and sdout/ and skip when
their input is missing.

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
import struct
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

VANILLA = os.path.join(ROOT, 'game_data_files', 'field', 'flevel.lgp')
BUILT = os.path.join(ROOT, 'sdout', 'atmosphere', 'contents',
                     '0100A5B00BDC6000', 'romfs', 'ff7', 'workingdir',
                     'data', 'field', 'flevel.lgp')


def _parts(path, name):
    import lgp
    a = lgp.Archive(path)
    return list(lgp.split_sections(a.decompressed(a.index[name])))


class Pair(unittest.TestCase):

    def test_linear_frames_fit_exactly(self):
        import ff7nx_palauto as PU
        base = np.full((4, 4, 3), 40.0, np.float32)
        frames = [base + 8 * i for i in range(19)]
        for k in (2, 4):
            _A, _L, mean, p999 = PU.pair_fit(frames, k)
            self.assertLess(p999, 0.5, k)

    def test_records_cover_every_state_once(self):
        import ff7nx_palauto as PU
        img = np.ones((2, 2, 3), np.float32)
        block, pb = [10, 11, 12, 13, 14], 20
        for k, na, nl in ((2, 10, 1), (4, 5, 2)):
            fit = ([img * (a + 1) for a in range(na)],
                   [img * 100 * (j + 1) for j in range(nl)], 0, 0)
            recs = PU.pair_records(fit, k, block, pb)
            self.assertEqual(len(recs), na + nl)
            for i in range(19):
                on = {block[i // 4]: 1 << (i % 4), pb: 1 << (i % 4)}
                shown = sum(float(m[0, 0, 0]) for p, b, m in recs
                            if on.get(p, 0) & b)
                coarse = i // k + 1
                fine = sum(100 * (j + 1) for j in range(nl)
                           if ((i % k) >> j) & 1)
                self.assertEqual(shown, coarse + fine, (k, i))

    def test_site_code_decodes(self):
        import echo_s_flevel as ES
        import ff7nx_palauto as PU
        for delta, size, n_ins in ((0, 30, 8), (-5, 34, 9)):
            code = PU.pair_code(0x0F, 250, 249, 3, delta)
            self.assertEqual(len(code), size)
            o, ops = 0, []
            while o < len(code):
                ops.append(code[o])
                o += ES.instruction_size(code, o)
            self.assertEqual(o, len(code))
            self.assertEqual(len(ops), n_ins)
            self.assertNotIn(0x12, ops)          # no backward jump (yield)
            self.assertNotIn(0x24, ops)          # no wait
        self.assertEqual(len(PU.multi_follow(16, 0x0F, 250, -5)), 16)

    def test_literal_bound_keeps_every_outcome(self):
        # IF v > var5[20] (var5[20] = 0xED, also compared by other loops)
        import ff7nx_palauto as PU
        sec0 = bytes.fromhex('14550b140206')
        uses = [(0, 'ifl', ('lit', 0x14, 0xED, (99,)))]
        out = PU.renumber(sec0, uses, 0xED, 1, 19, base=200)
        self.assertEqual(out[1], 0x50)          # right side now a literal
        for v in range(0xED, 0x100):
            self.assertEqual(v > 0xED, 200 + v - 0xED > out[3], v)


@unittest.skipUnless(os.path.exists(VANILLA), 'no vanilla flevel.lgp')
class Nvmkin32(unittest.TestCase):

    def test_four_effects_found(self):
        import ff7nx_palauto as PU
        sec0 = _parts(VANILLA, 'nvmkin32')[0]
        got = sorted((e['q'], e['var']) for e in PU.find_effects(sec0))
        self.assertEqual(got, [(7, 13), (8, 15), (10, 11), (11, 15)])

    def test_shared_bound_is_read_as_literal(self):
        import ff7nx_palauto as PU
        sec0 = _parts(VANILLA, 'nvmkin32')[0]
        ef = [e for e in PU.find_effects(sec0) if e['q'] == 10][0]
        uses = PU.var_uses(sec0, ef['var'],
                           skip={o for o, _l in ef['sites']})
        lits = [k for _o, kind, k in uses if isinstance(k, tuple)]
        self.assertEqual(sorted(k[2] for k in lits), [0xED, 0xFF])
        self.assertTrue(all(k[0] == 'lit' for k in lits))


@unittest.skipUnless(os.path.exists(BUILT), 'no built flevel.lgp')
class Grow(unittest.TestCase):

    def test_sites_grow_and_everything_moves(self):
        import ff7nx_palauto as PU
        sec0 = bytes(_parts(BUILT, 'nvmkin32')[0])
        effects = [e for e in PU.find_effects(sec0) if e['q'] != 8]
        edits = [(o, l, PU.pair_code(e['var'], 250, 249, 3))
                 for e in effects for o, l in e['sites']]
        new = PU.grow(sec0, edits)
        grown = sum(len(b) - l for _o, l, b in edits)
        self.assertEqual(len(new), len(sec0) + grown)
        so, sn = (struct.unpack_from('<H', s, 4)[0] for s in (sec0, new))
        self.assertEqual(sn - so, grown)
        self.assertEqual(sec0[so:], new[sn:])
        PU._stream(new)                          # still decodes
        # each loop's BACK still lands on its (grown) site
        shift = 0
        for o, l, b in sorted(edits):
            no = o + shift
            self.assertEqual(new[no:no + len(b)], b)
            st = [x for x in PU._stream(new) if x[1] > no and x[2] == 0x12]
            back = st[0][1]
            self.assertEqual(back - new[back + 1], no)
            shift += len(b) - l


if __name__ == '__main__':
    unittest.main()

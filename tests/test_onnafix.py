"""BUILD 717 -- ff7nx_onnafix: blin60_1's caped women (EDEA, 23 bones)
get ONNA.a, a stand for their own skeleton, instead of the guards' 21-bone
BOCB. The vanilla tests skip without game_data_files/field/char.lgp.

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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ff7nx_onnafix as OF                                     # noqa: E402

CHAR = os.path.join(ROOT, 'game_data_files', 'field', 'char.lgp')


def _record(name, hrc, anims):
    nm = name.encode() + b'\0'
    out = struct.pack('<H', len(nm)) + nm + b'\0\0'
    out += hrc.encode().ljust(8, b'\0') + b'512\0'
    out += struct.pack('<H', len(anims)) + bytes(30)
    for a in anims:
        out += struct.pack('<H', len(a)) + a.encode() + b'\x01\0'
    return out


def _loader(recs):
    return struct.pack('<HHH', 0, len(recs), 512) + b''.join(recs)


class Loader(unittest.TestCase):

    def test_only_edea_bocb_is_repointed(self):
        sec = _loader([_record('shinra_onna', 'EDEA.HRC', ['BOCB.aki']),
                       _record('shinra_guard', 'AZHE.HRC',
                               ['ACFE.aki', 'BOCB.aki']),
                       _record('shinra_onna', 'EDEA.HRC', ['BOCB.aki'])])
        new, n = OF.patch_loader(sec)
        self.assertEqual(n, 2)
        self.assertEqual(len(new), len(sec))
        self.assertEqual(new.count(b'ONNA.aki'), 2)
        self.assertEqual(new.count(b'BOCB.aki'), 1)     # the guard's
        self.assertEqual(OF.patch_loader(new)[1], 0)     # idempotent

    def test_bad_walk_refused(self):
        sec = _loader([_record('shinra_onna', 'EDEA.HRC', ['BOCB.aki'])])
        with self.assertRaises(ValueError):
            OF.patch_loader(sec + b'\0')


class Math(unittest.TestCase):

    def test_decompose_round_trip(self):
        for r in ((270, 0, 0), (309.4, 329.1, 38), (12, 200, 300),
                  (90, 278.4, 180)):
            a = OF.decompose(OF.rot(r))
            A, B = OF.rot(r), OF.rot(a)
            for i in range(3):
                for j in range(3):
                    self.assertAlmostEqual(A[i][j], B[i][j], places=9)


def _archive():
    import lgp
    a = lgp.Archive(CHAR)
    return lambda n: a.index[n]['payload'] if n in a.index else None


@unittest.skipUnless(os.path.exists(CHAR), 'no vanilla char.lgp')
class Vanilla(unittest.TestCase):

    def test_the_mismatch_is_there(self):
        get = _archive()
        self.assertEqual(len(OF.parse_hrc(get('edea.hrc'))), 23)
        self.assertEqual(OF.parse_a(get('bocb.a'))[0], 21)
        self.assertEqual(OF.parse_a(get('avbf.a'))[0], 23)

    def test_onna_is_avbf_with_the_cape_down(self):
        get = _archive()
        want, _note = OF.plan_char(get)
        nb, frames = OF.parse_a(want['onna.a'])
        nb0, src = OF.parse_a(get('avbf.a'))
        self.assertEqual((nb, len(frames)), (nb0, len(src)))
        bones = OF.parse_hrc(get('edea.hrc'))
        idx = {b[0]: i for i, b in enumerate(bones)}
        for f in frames:
            w = OF._world(bones, f)
            for name, sx in (('l_collar', -1), ('r_collar', 1)):
                M = w[idx[name]][2]
                # the cape spreads along the collar's local -X (left) /
                # +X (right): it now points down (model +Y)
                self.assertGreater(M[1][0] * sx, 0.9)
            # the hands hang below the chest
            for hand in ('l_hand', 'r_hand'):
                self.assertGreater(w[idx[hand]][1][1],
                                   w[idx['chest']][1][1] + 3)
        # nothing but the cape and arm bones moves
        moved = {i for f, g in zip(frames, src) for i in range(nb)
                 if any(abs(((a - b) + 180) % 360 - 180) > 1e-3
                        for a, b in zip(OF.rot(f[2][i])[0] + OF.rot(f[2][i])[1],
                                        OF.rot(g[2][i])[0] + OF.rot(g[2][i])[1]))}
        allowed = {idx[n] for n in OF.COLLAR_BONES}
        for arm in OF.ARMS:
            allowed |= {idx[n] for n in arm}
        self.assertTrue(moved <= allowed, moved - allowed)

    def test_a_21_bone_edea_gets_bocb_unchanged(self):
        get = _archive()
        hrc = get('edea.hrc').decode('latin1').replace('\r', '')
        blocks = hrc.split('\n\n')
        keep = [b for b in blocks[1:] if b.split('\n')[0] not in
                ('none1', 'hair')]
        h21 = (blocks[0].replace(':BONES 23', ':BONES 21') + '\n\n'
               + '\n\n'.join(keep)).encode('latin1')
        fake = lambda n: h21 if n == 'edea.hrc' else get(n)  # noqa: E731
        want, _note = OF.plan_char(fake)
        self.assertEqual(want['onna.a'], get('bocb.a'))


class Switch(unittest.TestCase):

    def test_disable(self):
        old = os.environ.get(OF.OFF_ENV)
        try:
            os.environ[OF.OFF_ENV] = '1'
            self.assertFalse(OF.enabled())
            self.assertEqual(OF.apply_to_char('/nonexistent')['added'], [])
        finally:
            if old is None:
                os.environ.pop(OF.OFF_ENV, None)
            else:
                os.environ[OF.OFF_ENV] = old


if __name__ == '__main__':
    unittest.main()

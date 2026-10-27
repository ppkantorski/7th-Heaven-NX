"""BUILD 756: user settings vs locked developer settings.

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
import os
import shutil
import struct
import tempfile
import unittest

import ff7nx_dockres as D

ROOT = nxpaths.ROOT
MAIN = os.path.join(ROOT, 'dump', 'exefs', 'main')
SDK = os.path.join(ROOT, 'dump', 'exefs', 'sdk')
need_dump = unittest.skipUnless(os.path.isfile(MAIN) and os.path.isfile(SDK),
                                'no dump/exefs/main + sdk')


def _dis(img, a, n=80):
    """Follow a laid-out cave (runs joined by plain `b`) to its br / ret."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    out = []
    for _ in range(n):
        i = next(cs.disasm(img[a:a + 4], a))
        text = ('%s %s' % (i.mnemonic, i.op_str)).strip()
        if i.mnemonic == 'b':
            t = int(i.op_str.strip('#'), 16)
            if t == 0x11524E0:
                out.append('b CreateLayer@plt')
                return out
            a = t
            continue
        out.append(text)
        if i.mnemonic in ('br', 'ret'):
            return out
        a += 4
    return out


def _target(img, va):
    w = struct.unpack_from('<I', img, va)[0]
    imm = w & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return va + 4 * imm


class Modes(unittest.TestCase):
    def test_env(self):
        self.assertIsNone(D.mode({}))
        self.assertIsNone(D.mode({D.ENV: 'off'}))
        self.assertEqual(D.mode({D.ENV: '1080'}), (1920, 1080))
        self.assertEqual(D.mode({D.ENV: '900p'}), (1600, 900))
        self.assertEqual(D.mode({D.ENV: 'always'}), (1920, 1080))
        self.assertTrue(D.always({D.ENV: 'always'}))
        self.assertFalse(D.always({D.ENV: '1080'}))

    def test_factor_keeps_the_scene_target(self):
        """fcvtzs(logical * (1080 / H)) for logical = H*16/9 and H*4/3."""
        import numpy as np
        for h, wide, narrow in ((720, 1920, 1440), (900, 1920, 1440),
                                (1080, 1920, 1440)):
            f = np.float32(1080.0) / np.float32(h)
            self.assertEqual(int(np.float32(h * 16 // 9) * f), wide)
            self.assertEqual(int(np.float32(h * 4 // 3) * f), narrow)
            self.assertEqual(int(np.float32(h) * f), 1080)
        self.assertEqual(np.float32(1080.0) / np.float32(720), np.float32(1.5))

    def test_never_invalidates_an_archive(self):
        import build
        self.assertIn(D.ENV, build.ARCHIVE_NEUTRAL_ENV)


@need_dump
class Patch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.out = os.path.join(cls.tmp, 'main')
        cls.ok = D.apply_to_nso(MAIN, cls.out, SDK, (1920, 1080))
        import nxmap
        cls.img = nxmap.Main(cls.out).img

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_anchor_is_a_load_time_slot(self):
        """BUILD 761b: GetOperationMode's PLT slot did not hold the function
        on hardware (docked boot jumped into nothing); longjmp's GLOB_DAT
        slot is written by rtld at load."""
        import nxmap
        slot, delta, first8, name = D.sdk_target(
            bytes(nxmap.Main(MAIN).img), SDK)
        self.assertEqual((name, slot), ('longjmp', 0x12CEC40))
        self.assertEqual(delta, 0x3ADDF0 - 0x4B90C4)
        self.assertEqual(first8, 0xA9014FF4D100C3FF)   # sub sp; stp x20,x19

    def test_layer_cave(self):
        self.assertTrue(self.ok)
        got = _dis(self.img, _target(self.img, D.LAYER_SITE))
        self.assertEqual(got[:9], [
            'stp x29, x30, [sp, #-0x20]!', 'mov x29, sp',
            'stp x0, x1, [sp, #0x10]', 'bl #0x1151800', 'bl #0x1151830',
            'cmp w0, #1', 'ldp x0, x1, [sp, #0x10]',
            'ldp x29, x30, [sp], #0x20', got[8]])
        self.assertTrue(got[8].startswith('b.ne '))
        self.assertEqual(_dis(self.img, int(got[8].split('#')[1], 16)),
                         ['b CreateLayer@plt'])
        self.assertEqual(got[9:21], [
            'adrp x16, #0x12ce000', 'ldr x16, [x16, #0xc40]',
            'mov w17, #0xb2d4', 'movk w17, #0x10, lsl #16',
            'sub x16, x16, x17', 'ldr x17, [x16]', 'mov x9, #0xc3ff',
            'movk x9, #0xd100, lsl #16', 'movk x9, #0x4ff4, lsl #32',
            'movk x9, #0xa901, lsl #48', 'cmp x17, x9', got[20]])
        self.assertTrue(got[20].startswith('b.ne '))
        self.assertEqual(_dis(self.img, int(got[20].split('#')[1], 16)),
                         ['b CreateLayer@plt'])
        self.assertEqual(got[21:], ['mov w2, #0x780', 'mov w3, #0x438',
                                    'br x16'])

    def test_getters_and_factor(self):
        self.assertEqual(_dis(self.img, _target(self.img, D.GET_W)), [
            'adrp x0, #0x3feb000', 'ldr w0, [x0, #0x730]', _dis(
                self.img, _target(self.img, D.GET_W))[2], 'mov w0, #0x500',
            'ret'])
        self.assertEqual(_dis(self.img, _target(self.img, D.GET_H))[:2], [
            'adrp x0, #0x3feb000', 'ldr w0, [x0, #0x734]'])
        self.assertEqual(_dis(self.img, _target(self.img, D.FACTOR_SITE)), [
            'ucvtf s1, w0', 'mov w16, #0x438', 'ucvtf s0, w16',
            'fdiv s0, s0, s1', 'ret'])

    def test_refuses_a_module_it_already_patched(self):
        self.assertFalse(D.apply_to_nso(self.out, self.out + '.2', SDK,
                                        (1920, 1080)))


@need_dump
class Always(unittest.TestCase):
    """BUILD 762: 1080p in every mode -- no operation-mode query."""
    def test_cave_has_no_mode_test(self):
        tmp = tempfile.mkdtemp()
        try:
            out = os.path.join(tmp, 'main')
            self.assertTrue(D.apply_to_nso(MAIN, out, SDK, (1920, 1080),
                                           every=True))
            import nxmap
            img = nxmap.Main(out).img
            got = _dis(img, _target(img, D.LAYER_SITE))
            self.assertEqual(got[:11], [
                'adrp x16, #0x12ce000', 'ldr x16, [x16, #0xc40]',
                'mov w17, #0xb2d4', 'movk w17, #0x10, lsl #16',
                'sub x16, x16, x17', 'ldr x17, [x16]', 'mov x9, #0xc3ff',
                'movk x9, #0xd100, lsl #16', 'movk x9, #0x4ff4, lsl #32',
                'movk x9, #0xa901, lsl #48', 'cmp x17, x9'])
            self.assertEqual(_dis(img, int(got[11].split('#')[1], 16)),
                             ['b CreateLayer@plt'])
            self.assertEqual(got[12:], ['mov w2, #0x780', 'mov w3, #0x438',
                                        'br x16'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()

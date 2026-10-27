"""BUILD 749h: x86 0x476AAB installs KOTR's camera into effect100 slot 99
without the registration cave, so its pause-throttle counter is stale. The
patch resets ctr[99] inside 476AAB's translation, in place.

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
import os, struct, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import ff7nx_dispatch as D
import ff7nx_dispatch_sites as S

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X8,
                                 UC_ARM64_REG_X20, UC_ARM64_REG_X30)

CTR = S.DISPATCH_SITES['effect100']['throttle']['ctr']
MAIN = os.path.join(os.path.dirname(HERE), 'dump', 'exefs', 'main')

TRANSLATE = 0x10FC3A0
GUEST = 0x08000000          # host window standing in for guest memory
CTX = 0x05000000
EBP = 0x0019F000


def run(words):
    """Execute the five words at their real address. Returns (guest EAX,
    ctr[99], x0-independence)."""
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0x200000, 0x20000)
    uc.mem_map(TRANSLATE & ~0xFFF, 0x1000)
    uc.mem_map(CTR & ~0xFFF, 0x2000)
    uc.mem_map(CTX, 0x1000)
    uc.mem_map(GUEST, 0x400000)
    code = struct.pack('<5I', *words) + struct.pack('<I', 0xD503201F)
    uc.mem_write(D.KOTR_CAM_AT, code)
    # translate: x0 = GUEST + (w0 & 0x3FFFFF); ret
    tr = [0x12005400, 0xD2A10010, 0x8B100000, 0xD65F03C0]   # and w0,w0,#0x3FFFFF; movz x16,#0x0800,lsl16; add x0,x0,x16; ret
    uc.mem_write(TRANSLATE, struct.pack('<4I', *tr))
    uc.mem_write(CTX + 0x14, struct.pack('<I', EBP))
    uc.mem_write(GUEST + ((EBP - 4) & 0x3FFFFF), struct.pack('<I', D.KOTR_CAM_DATA))
    uc.mem_write(CTR + 99, b'\x01')
    uc.mem_write(CTR + 98, b'\x02')
    uc.mem_write(CTR + 100, b'\x03')
    uc.reg_write(UC_ARM64_REG_X20, CTX)
    uc.reg_write(UC_ARM64_REG_X30, 0)
    uc.emu_start(D.KOTR_CAM_AT, D.KOTR_CAM_AT + 20)
    eax = struct.unpack('<I', uc.mem_read(CTX, 4))[0]
    c = bytes(uc.mem_read(CTR + 98, 3))
    return eax, c


class KotrCameraSeed(unittest.TestCase):
    def test_stock_leaves_the_counter_and_loads_eax(self):
        eax, c = run(D.KOTR_CAM_STOCK)
        self.assertEqual(eax, D.KOTR_CAM_DATA)
        self.assertEqual(c, b'\x02\x01\x03')     # stale phase survives

    def test_patch_loads_the_same_eax_and_resets_only_slot_99(self):
        eax, c = run(D.kotr_camera_seed_words(CTR))
        self.assertEqual(eax, D.KOTR_CAM_DATA)
        self.assertEqual(c, b'\x02\x00\x03')

    def test_the_slot_is_kotr_camera(self):
        d = S.DISPATCH_SITES['effect100']
        self.assertEqual(d['array_fn'] + 4 * D.KOTR_CAM_SLOT, 0xBF29E4)
        self.assertEqual(d['data_base'] + d['stride'] * D.KOTR_CAM_SLOT,
                         D.KOTR_CAM_DATA)
        self.assertLess(D.KOTR_CAM_SLOT, d['slots'])

    @unittest.skipUnless(os.path.exists(MAIN), 'no stock main')
    def test_stock_words_and_idempotence(self):
        import nxmap
        m = nxmap.Main(MAIN)
        s, e = m.extent(0x476AAB)
        self.assertTrue(s <= D.KOTR_CAM_AT and D.KOTR_CAM_AT + 20 <= e)
        text = bytearray(m.text)
        self.assertEqual(list(struct.unpack_from('<5I', text, D.KOTR_CAM_AT)),
                         list(D.KOTR_CAM_STOCK))
        self.assertTrue(D.patch_kotr_camera_seed(text, CTR))
        self.assertFalse(D.patch_kotr_camera_seed(text, CTR))
        struct.pack_into('<I', text, D.KOTR_CAM_AT, 0xD503201F)
        with self.assertRaises(SystemExit):
            D.patch_kotr_camera_seed(text, CTR)


if __name__ == '__main__':
    unittest.main()

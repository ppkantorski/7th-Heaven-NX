"""BUILD 886: a shrinking dialogue box draws no text.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import os  # noqa: E402
import shutil  # noqa: E402
import struct  # noqa: E402

import pytest  # noqa: E402

import ff7nx_textclose as TC  # noqa: E402

DUMP = _nx_os.path.join(_nx_root, 'dump', 'exefs', 'main')


def _emu(t, hook, W, H, cw, ch, count=57):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X22, \
        UC_ARM64_REG_PC
    mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    mu.mem_map(0, (len(t) + 0xFFFF) & ~0xFFFF)
    mu.mem_write(0, bytes(t))
    S = 0x40000000
    mu.mem_map(S, 0x1000)
    buf = bytearray(0x30)
    struct.pack_into('<hhhh', buf, 8, W, H, cw, ch)
    struct.pack_into('<H', buf, 0x12, count)
    mu.mem_write(S + 0x100, bytes(buf))
    mu.reg_write(UC_ARM64_REG_X0, S + 0x100 + 0x12)
    mu.reg_write(UC_ARM64_REG_X22, 0xDEAD)
    mu.emu_start(hook, hook + 4, count=40)
    return mu.reg_read(UC_ARM64_REG_X22) & 0xFFFF, mu.reg_read(UC_ARM64_REG_PC)


def test_body_shape():
    w = TC.body_words_at(TC.HOOKS[0], lambda i: 0x100000 + 4 * i)
    assert len(w) == 12 and w[0] == TC.STOCK and all(x is not None for x in w)


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the dump main')
def test_apply_and_emulate(tmp_path):
    pytest.importorskip('unicorn')
    import nxmap
    main = tmp_path / 'main'
    shutil.copyfile(DUMP, main)
    assert TC.apply(main, log=lambda *_: None) == 0
    assert TC.apply(main, log=lambda *_: None) == 0          # idempotent
    t = nxmap.Main(str(main)).text
    for hook in TC.HOOKS:
        assert _emu(t, hook, 200, 41, 200, 41) == (57, hook + 4)   # open
        assert _emu(t, hook, 200, 41, 150, 41) == (0, hook + 4)    # shrinking w
        assert _emu(t, hook, 200, 41, 200, 30) == (0, hook + 4)    # shrinking h
        assert _emu(t, hook, 200, 41, 8, 8) == (0, hook + 4)


def test_refuses_non_stock(tmp_path):
    if not os.path.exists(DUMP):
        pytest.skip('needs the dump main')
    import nxmap
    m = nxmap.Main(DUMP)
    img = bytearray(m.img)
    struct.pack_into('<I', img, TC.HOOKS[1], 0xD503201F)
    assert TC.build_patches(img, set(m.arm_starts), log=lambda *_: None) is None


def test_disabled_env():
    assert TC.enabled({'SEVENTH_NX_TEXT_CLOSE': '0'}) is False
    assert TC.enabled({}) is True

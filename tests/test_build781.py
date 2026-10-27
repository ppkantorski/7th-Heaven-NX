"""BUILD 781: the KOTR camera gets the camera decorator its slot never gets
from registration (it is installed straight into effect100 slot 99).

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
import struct  # noqa: E402

import pytest  # noqa: E402

import ff7nx_dispatch as D  # noqa: E402
import ff7nx_dispatch_sites as S  # noqa: E402

T = S.DISPATCH_SITES['effect100']['throttle']
CTR = T['ctr']
CAM = (T['model_next'] + 0x64 * 6 + 7) & ~7          # as _inject_camera_hold
CAM_INIT, CAM_FINAL, CAM_THR = CAM + 40, CAM + 58, CAM + 60


def _run(words):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import UC_ARM64_REG_X20
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0x200000, 0x20000)
    uc.mem_map(CTR & ~0xFFF, 0x2000)
    ctx = 0x05000000
    uc.mem_map(ctx, 0x1000)
    code = b''.join(struct.pack('<I', w) for w in words)
    uc.mem_write(D.KOTR_CAM_AT, code)
    uc.mem_write(CTR + 99, b'\x01')
    uc.mem_write(CAM_INIT, b'\x01')
    uc.mem_write(CAM_FINAL, b'\x01')
    uc.mem_write(CAM_THR, struct.pack('<H', 1500))
    uc.reg_write(UC_ARM64_REG_X20, ctx)
    uc.emu_start(D.KOTR_CAM_AT, D.KOTR_CAM_AT + 4 * len(words))
    return (uc.mem_read(CTR + 99, 1)[0], uc.mem_read(CAM_INIT, 1)[0],
            uc.mem_read(CAM_FINAL, 1)[0],
            struct.unpack('<H', uc.mem_read(CAM_THR, 2))[0],
            struct.unpack('<I', uc.mem_read(ctx, 4))[0])


def test_781_seed_then_register_marks_the_slot_a_camera():
    pytest.importorskip('unicorn')
    seed = D.kotr_camera_seed_words(CTR)
    body = D.kotr_camera_register_body(CTR, CAM_THR, CAM_INIT, CAM_FINAL,
                                       5000)
    ctr99, init, final, thr, eax = _run(seed + body)
    assert ctr99 == D.CAM_MARK                 # camera arm, phase 0
    assert (init, final, thr) == (2, 0, 5000)  # 783: KOTR_CAM_FIRST, not 0
    assert eax == D.KOTR_CAM_DATA              # the seed still loads EAX


def test_781_targets_share_the_seed_page_and_threshold_is_ffnx():
    body = D.kotr_camera_register_body(CTR, CAM_THR, CAM_INIT, CAM_FINAL,
                                       5000)
    assert len(body) == 7
    with pytest.raises(SystemExit):
        D.kotr_camera_register_body(CTR, CTR + 0x2000, CAM_INIT, CAM_FINAL,
                                    5000)
    assert 'run_kotr_camera_476AFB' in D.EFFECT100_CAMERA

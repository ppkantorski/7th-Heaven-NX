#!/usr/bin/env python3
"""tests/test_modelinterp.py -- the guest function pointer must survive.

BUILD 378. The pre cave's hook sits four instructions before

    +0x7D3054   bl #0xa1a0

and w0 carries the guest function pointer into that call. The model arm used
w0 as scratch -- `and w0, w16, #0x1F` was its first instruction -- so every
model-marked slot made the dispatcher branch to the slot index instead of the
effect function. That is build 214's abort and the operator's instant crash on
Choco/Mog, which is one of the ten marked routines.

The sub-paths did spill x0, but they spilled it after it had already been
destroyed, so the restore put the garbage back. Nothing in the existing
harness asserted w0's value at the cave exit, which is why 5,979 cave
executions and eleven mutation checks all passed over it.

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
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as E                                           # noqa: E402
import ff7nx_dispatch as D                                     # noqa: E402

try:
    import ff7nx_dispatch_sites as S
except ImportError:                                            # pragma: no cover
    S = None

pytestmark = pytest.mark.skipif(S is None,
                                reason='needs ff7nx_dispatch_sites.py')

CAVE = 0x900000
FNPTR = 0x0050B1A3          # run_chocomog_movement_50B1A3, a marked routine


def _thr(**extra):
    thr = dict(S.DISPATCH_SITES['effect100']['throttle'])
    thr['table'] = []
    thr.update(extra)
    return thr


def _site(thr):
    dh = S.DISPATCH_SITES['effect100']['disp_hook']
    return {'hook': thr['pre'], 'displaced': thr['pre_displaced'],
            'ctx_reg': thr['ctx_reg'], 'store_reg': thr['store_reg'],
            'idx_off': dh['idx_off']}


def _run(words, thr, site, idx, marker, phase):
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=0x40000000)
    mem.setu(thr['ctr'] + idx, marker, 1)
    mem.setu(thr['model_phase'] + idx, phase, 1)
    cpu.set(thr['ctx_reg'], 0x30000000)
    mem.setu(0x30000000 + site['idx_off'], idx, 4)
    cpu.set(0, FNPTR)
    cpu.set(site['store_reg'], 0x1234)
    cpu.run(CAVE, words)
    return cpu


@pytest.mark.parametrize('marker', [0x20, 0x30, 0x28, 0x38])
@pytest.mark.parametrize('phase', [0, 1, 2, 3])
def test_the_function_pointer_survives_the_model_arm(marker, phase):
    """0x20/0x28 are an unregistered slot, 0x30/0x38 a registered one; 0x28
    and 0x38 carry Alexander's larger threshold. All four, every phase."""
    thr = _thr(model=list(D.QUARANTINED_SUMMON_MODEL_INTERPOLATION))
    site = _site(thr)
    words = D.build_throttle_pre_cave(CAVE, site, thr, S.PAUSED_GUEST,
                                      thr['mask_bits'])
    cpu = _run(words, thr, site, 7, marker, phase)
    assert cpu.get(0) & 0xFFFFFFFF == FNPTR, (
        'the model arm destroyed the guest function pointer: the dispatcher '
        'would branch to 0x%X' % (cpu.get(0) & 0xFFFFFFFF))


@pytest.mark.parametrize('marker', [0x00, 0x40, 0x80, 0x10])
def test_the_other_arms_preserve_it_too(marker):
    """The generic (0x00), KOTR (0x40), excluded (0x80) and OneCall (0x10)
    paths, checked with the same instrument that caught the model one."""
    thr = _thr(model=list(D.QUARANTINED_SUMMON_MODEL_INTERPOLATION))
    site = _site(thr)
    words = D.build_throttle_pre_cave(CAVE, site, thr, S.PAUSED_GUEST,
                                      thr['mask_bits'])
    for phase in range(4):
        cpu = _run(words, thr, site, 5, marker, phase)
        assert cpu.get(0) & 0xFFFFFFFF == FNPTR, (marker, phase)


def test_the_displaced_store_register_survives_as_well():
    """wE is the value the displaced `str wE, [ctx, #0x10]` writes, and it is
    live for exactly the same reason w0 is."""
    thr = _thr(model=list(D.QUARANTINED_SUMMON_MODEL_INTERPOLATION))
    site = _site(thr)
    e = site['store_reg']
    words = D.build_throttle_pre_cave(CAVE, site, thr, S.PAUSED_GUEST,
                                      thr['mask_bits'])
    for marker in (0x20, 0x30, 0x28, 0x38, 0x00, 0x40):
        for phase in range(4):
            cpu = _run(words, thr, site, 7, marker, phase)
            assert cpu.get(e) & 0xFFFFFFFF == 0x1234, (marker, phase)


def test_the_arm_is_not_using_w0_as_scratch_at_all():
    """Structural, so a future edit cannot reintroduce it: no instruction in
    the model arm may DEFINE w0 outside the spill/restore pairs."""
    thr = _thr(model=list(D.QUARANTINED_SUMMON_MODEL_INTERPOLATION))
    plain = D.build_throttle_pre_cave(CAVE, _site(thr), _thr(),
                                      S.PAUSED_GUEST, thr['mask_bits'])
    full = D.build_throttle_pre_cave(CAVE, _site(thr), thr,
                                     S.PAUSED_GUEST, thr['mask_bits'])
    assert len(full) > len(plain)
    # the four words that legitimately touch x0 in a spill pair
    legit = {0xF90003E0,            # str x0, [sp]
             0xF94003E0,            # ldr x0, [sp]
             }
    for word in full[len(plain):]:
        defines_w0 = (word & 0x1F) == 0
        if defines_w0 and word not in legit:
            # movz/movk/bl into w0 for TRANSLATE is fine -- it happens after a
            # spill. Only flag the AND/LDR/ADD forms the bug used.
            top = word & 0xFF800000
            assert top not in (0x12000000,      # and (immediate)
                               0xB9400000,      # ldr (immediate)
                               0x11000000), (   # add (immediate)
                'model arm defines w0 with %08X outside a spill' % word)

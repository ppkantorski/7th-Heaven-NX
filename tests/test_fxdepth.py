#!/usr/bin/env python3
"""
tests/test_fxdepth.py -- BUILD 364, the depth-only geometry correction.

The claim is that one instruction carries the whole depth coordinate and that
scaling it leaves the width alone. Both halves are checked by EXECUTION: the
cave runs under arm64emu over the real range of values the vertex builder
produces, and its output is compared against an independent reference. The
identity position (100 %) is held to the STOCK instruction bit for bit, so
that leaving the dial alone cannot change a single vertex.

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
import shutil
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                # noqa: E402
import arm64emu as E                                           # noqa: E402
import ff7nx_fxdepth as D                                      # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
BASE = 0x800000
# (R, rcos) pairs spanning everything the builder can produce: ring 0, the
# first ring, a mid ring, and the outer ring, against the full cosine range.
CASES = [(R, c) for R in (0, 384, 3840, 11904, 12192)
         for c in (-4096, -2896, -2048, 0, 2048, 2896, 4096)]


def _run(words, z):
    """Execute the cave with w8 = z (the pre-shift -R*rcos) and w25 = -5000."""
    code = dict(zip([BASE + 4 * i for i in range(len(words))], words))
    cpu = E.Cpu(E.Mem())
    cpu.set(8, z & 0xFFFFFFFF)
    cpu.set(25, (-5000) & 0xFFFFFFFF)
    cpu.set(10, 0xDEAD)                    # rcos, must be treated as dead
    cpu.run(BASE, None, code=code, start_pc=BASE, max_steps=200)
    v = cpu.get(8) & 0xFFFFFFFF
    return v - (1 << 32) if v >= 1 << 31 else v


def _cave(pct):
    """The body with its return branch replaced by a step off the end."""
    w = D.body_words(lambda i: BASE + 4 * i, pct)
    return w[:-1] + [0x14000000 | 1]


def test_the_identity_position_is_the_stock_instruction_bit_for_bit():
    """100 % must not change a single vertex: (x>>12)*256>>8 == x>>12 for
    every x, positive or negative, because both shifts are arithmetic."""
    words = _cave(100.0)
    for R, c in CASES:
        z = -R * c
        assert _run(words, z) == (-5000) + (z >> 12), (R, c)


def test_the_cave_computes_the_scaled_depth():
    for pct in (25.0, 60.0, 75.0, 82.0, 85.0, 94.0, 120.0, 200.0):
        words = _cave(pct)
        k = D.k_for(pct)
        for R, c in CASES:
            z = -R * c
            assert _run(words, z) == (-5000) + (((z >> 12) * k) >> 8), (pct, R, c)


def test_the_multiply_cannot_overflow():
    """The shift comes first for a reason: -R*rcos reaches 50M and scaling
    that would overflow w8, while the shifted value is at most the radius."""
    for pct in (100.0, 200.0):
        k = D.k_for(pct)
        for R, c in CASES:
            assert abs((-R * c >> 12) * k) < 2 ** 31, (pct, R, c)


def test_the_words_disassemble_to_what_the_header_claims():
    cs = pytest.importorskip('capstone')
    md = cs.Cs(cs.CS_ARCH_ARM64, cs.CS_MODE_LITTLE_ENDIAN)
    want = ['asr w8, w8, #0xc', 'mov w10, #0xda', 'mul w8, w8, w10',
            'asr w8, w8, #8', 'add w8, w25, w8']
    words = D.body_words(lambda i: BASE + 4 * i, 85.0)
    for n, (word, text) in enumerate(zip(words, want)):
        got = list(md.disasm(struct.pack('<I', word), BASE + 4 * n))
        assert got, '%08X does not disassemble' % word
        assert '%s %s' % (got[0].mnemonic, got[0].op_str) == text


def test_the_dial_is_off_unless_it_is_asked_for():
    was = os.environ.pop(D.DEPTH_ENV, None)
    try:
        assert D.percent() == 100.0 and not D.enabled()
        assert D.k_for(100.0) == D.STOCK_K
        for bad in ('', 'x', '0', '1000', '-5'):
            os.environ[D.DEPTH_ENV] = bad
            assert D.percent() == 100.0, bad
    finally:
        os.environ.pop(D.DEPTH_ENV, None)
        if was is not None:
            os.environ[D.DEPTH_ENV] = was


@pytest.mark.skipif(not os.path.exists(MAIN), reason='needs exefs/main')
def test_every_anchor_matches_the_shipping_image():
    import nxmap
    img = nxmap.Main(MAIN).img
    for va, want in sorted(D.ANCHORS.items()):
        got = struct.unpack_from('<I', img, va)[0]
        assert got == want, '+0x%X is %08X, expected %08X' % (va, got, want)
    assert struct.unpack_from('<I', img, D.HOOK)[0] == D.HOOK_STOCK


@pytest.mark.skipif(not os.path.exists(MAIN), reason='needs exefs/main')
def test_apply_is_idempotent_revert_is_byte_identical_and_width_is_untouched(
        tmp_path):
    import nxmap
    work = tmp_path / 'main'
    shutil.copy(MAIN, work)
    before = work.read_bytes()
    WIDTH_SITE = 0x473314
    was = os.environ.get(D.DEPTH_ENV)
    try:
        for pct in ('85', '82', '120'):
            os.environ[D.DEPTH_ENV] = pct
            assert D.apply_all(str(work), log=lambda *a: None) == 0
            img = nxmap.Main(str(work)).img
            assert D.installed(img) == D.pct_of(D.k_for(float(pct)))
            # the WIDTH instruction must be exactly as it shipped
            assert struct.unpack_from('<I', img, WIDTH_SITE)[0] == \
                D.ANCHORS[WIDTH_SITE]
            assert D.apply_all(str(work), log=lambda *a: None) == 0
            assert work.read_bytes() != before
            assert D.apply_all(str(work), revert=True,
                               log=lambda *a: None) == 0
            assert work.read_bytes() == before, pct
    finally:
        os.environ.pop(D.DEPTH_ENV, None)
        if was is not None:
            os.environ[D.DEPTH_ENV] = was


@pytest.mark.skipif(not os.path.exists(MAIN), reason='needs exefs/main')
def test_it_shares_no_site_with_the_other_disc_modules(tmp_path):
    """fxdisc +0x473248, fxscale +0x473178, fxorigin +0x473180, and the width
    at +0x473314 -- none of them may appear in this module's patch set."""
    import nxmap
    work = tmp_path / 'main'
    shutil.copy(MAIN, work)
    was = os.environ.get(D.DEPTH_ENV)
    os.environ[D.DEPTH_ENV] = '85'
    try:
        patches, notes, problems = D.plan(nxmap.Main(str(work)))
        assert not problems
        touched = {int(p['va'], 16) for p in patches}
        for other in (0x473248, 0x473178, 0x473180, 0x473314, 0x473310):
            assert other not in touched, hex(other)
        assert D.HOOK in touched
    finally:
        os.environ.pop(D.DEPTH_ENV, None)
        if was is not None:
            os.environ[D.DEPTH_ENV] = was

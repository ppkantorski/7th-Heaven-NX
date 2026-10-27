"""
tests/test_bz_stars.py -- Bahamut ZERO's star field quad, all seven sites.

BUILD 386, finishing HANDOFF-254 §3. Five ARM immediates in `exefs/main` and
two `.data` words in `ff7_en`; the note refused to ship the five without the
two, because position-without-size puts the stars on a 512-unit grid while
each quad stays 256 across -- gaps between them, worse than the popping.

Nothing here trusts a capstone mnemonic for the value: ADD and SUB of an
immediate differ only in one opcode bit, and the -512 case is exactly that
flip, so every assertion compares the literal 32-bit word or re-derives the
operand from FFNx's own source.

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
import hashlib
import shutil
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import a64 as A                      # noqa: E402
import ff7nx_battlewide as B         # noqa: E402
import nxmap                         # noqa: E402

MAIN = ROOT / 'exefs' / 'main'
EXE = ROOT / 'game_data_files' / 'ff7_1.02' / 'ff7_en'
FFNX = ROOT / 'repos' / 'FFNx-master' / 'src' / 'ff7' / 'widescreen.cpp'

needs_main = pytest.mark.skipif(not MAIN.exists(), reason='no exefs/main')
needs_exe = pytest.mark.skipif(not EXE.exists(), reason='no ff7_en')
needs_ffnx = pytest.mark.skipif(not FFNX.exists(), reason='no FFNx checkout')


def word(img, va):
    return struct.unpack('<I', img[va:va + 4])[0]


# ------------------------------------------------------- against FFNx itself

@needs_ffnx
def test_the_seven_sites_match_ffnx_widescreen_cpp():
    """Re-derive the offsets and values from FFNx rather than trusting a note."""
    import re
    src = FFNX.read_text()
    code = dict(re.findall(
        r'patch_code_short\(ff7_externals\.bahamut_zero_draw_bg_effect_sub_4859AA'
        r'\s*\+\s*(0x[0-9A-Fa-f]+),\s*(-?\d+)\)', src))
    data = dict(re.findall(
        r'patch_code_short\(ff7_externals\.bahamut_zero_bg_star_graphics_data_7F6748'
        r'\s*\+\s*(0x[0-9A-Fa-f]+),\s*(-?\d+)\)', src))
    assert {int(k, 16): int(v) for k, v in code.items()} == {
        0x20F: -512, 0x23F: 1024, 0x26F: 512, 0x29E: 512, 0x2CE: 512}
    assert {int(k, 16): int(v) for k, v in data.items()} == {0x8: 512, 0xA: 512}

    # The five code sites, in FFNx's own order, must be the five this module
    # patches -- and the operand each one becomes must be what FFNx writes.
    wants = [-512, 1024, 512, 512, 512]
    assert len(B.BZ_STARS_SITES) == 5
    for (label, _va, stock, wide), want in zip(B.BZ_STARS_SITES, wants):
        # stock is always +/-256; rebuild the patched word from `want` alone.
        rd, rn = stock & 0x1F, (stock >> 5) & 0x1F
        is_sub = (stock & 0xFF000000) == 0x51000000
        signed = -want if is_sub else want
        rebuilt = (A.add_imm(rd, rn, signed) if signed >= 0
                   else A.sub_imm(rd, rn, -signed))
        assert rebuilt == wide, (
            '%s: table says %08X, FFNx\'s %d gives %08X'
            % (label, wide, want, rebuilt))

    assert len(B.BZ_STARS_EXE) == 2
    for _label, va, stock, new in B.BZ_STARS_EXE:
        assert stock == 256 and new == 512
        assert va - B.BZ_STARS_DATA_VA in (0x8, 0xA)


# --------------------------------------------------------- against the image

@needs_main
def test_every_code_site_is_stock_and_inside_the_right_function():
    m = nxmap.Main(str(MAIN))
    lo, hi = m.extent(B.BZ_STARS_X86)
    for label, va, stock, _wide in B.BZ_STARS_SITES:
        assert word(m.img, va) == stock, (
            '%s: +0x%X is %08X, expected the stock %08X'
            % (label, va, word(m.img, va), stock))
        assert lo <= va < hi, (
            '%s: +0x%X is outside the translated body of 0x%X (0x%X..0x%X)'
            % (label, va, B.BZ_STARS_X86, lo, hi))


@needs_main
def test_the_stock_words_are_the_three_adds_then_two_subs():
    """The ARM order must match the x86 order, or the sites are mismatched."""
    kinds = [w & 0xFF000000 for _l, _va, w, _n in B.BZ_STARS_SITES]
    assert kinds == [0x11000000] * 3 + [0x51000000] * 2
    regs = [(w & 0x1F, (w >> 5) & 0x1F) for _l, _va, w, _n in B.BZ_STARS_SITES]
    assert regs == [(23, 8)] * 3 + [(22, 8)] * 2, regs
    for _l, _va, w, _n in B.BZ_STARS_SITES:
        assert ((w >> 10) & 0xFFF) == 0x100, 'stock immediate is not 256'


@needs_main
def test_no_site_is_claimed_by_another_battlewide_group():
    mine = {va for _l, va, _s, _n in B.BZ_STARS_SITES}
    assert len(mine) == 5
    m = nxmap.Main(str(MAIN))
    for g in B.ACTIVE_GROUPS:
        for _name, x86, fields in B.GROUPS[g][1]:
            lo, hi = m.extent(x86)
            clash = {va for va in mine if lo <= va < hi}
            assert not clash, (
                'group %d (%s) owns %s' % (g, _name,
                                           sorted(hex(v) for v in clash)))


@needs_exe
def test_both_size_words_are_file_backed_and_currently_256():
    import exe_patch
    data = EXE.read_bytes()
    pe = exe_patch.parse_pe(data)
    for label, va, stock, _new in B.BZ_STARS_EXE:
        off = exe_patch.va_to_offset(pe, va)
        assert off is not None, '%s: VA %#x is not file-backed' % (label, va)
        assert struct.unpack_from('<H', data, off)[0] == stock


@needs_exe
def test_the_size_words_live_in_data_not_rdata():
    """
    The point HANDOFF-254 missed. They are not in `main` because they do not
    need to be: this port runs the guest's own data image, and EXE_CONFIRMED
    has patched ff7_en `.data` since the first build.
    """
    import ff7nx_resolve as R
    exe = R.Exe(EXE.read_bytes())
    for _label, va, _s, _n in B.BZ_STARS_EXE:
        assert exe.sect(va)[0] == '.data'
    # ...the same section as a patch that has been shipping and works.
    import ff7nx_60fps as F
    proven = [p[1] for p in F.EXE_CONFIRMED if exe.sect(p[1])[0] == '.data']
    assert proven, 'no .data patch in EXE_CONFIRMED to stand on'


@needs_exe
def test_nothing_references_the_size_words_absolutely():
    """
    They are reached through the structure's ADDRESS, which is why there is no
    ARM site materialising them and why 254 could not find one. If a direct
    reference ever appears, this patch would need a code site too.
    """
    import re
    import ff7nx_resolve as R
    exe = R.Exe(EXE.read_bytes())
    _n, vbase, raw, rsize = exe.sect(0x401000)
    text = exe.data[raw:raw + rsize]
    for _label, va, _s, _new in B.BZ_STARS_EXE:
        assert not list(re.finditer(re.escape(struct.pack('<I', va)), text)), (
            '0x%X is referenced absolutely in x86 .text after all' % va)
    # The base, by contrast, is pushed as an argument several times.
    hits = list(re.finditer(re.escape(struct.pack('<I', B.BZ_STARS_DATA_VA)),
                            text))
    assert len(hits) >= 3, 'the structure address is never passed anywhere'


# ------------------------------------------------------------- apply/revert

@needs_main
def test_apply_then_revert_restores_main_byte_for_byte(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    before = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert B.apply_bz_stars(dst, log=lambda *a: None) == 0
    after = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert after != before, 'apply changed nothing'
    m = nxmap.Main(str(dst))
    for label, va, _stock, wide in B.BZ_STARS_SITES:
        assert word(m.img, va) == wide, label
    assert B.apply_bz_stars(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before


@needs_main
def test_applying_twice_is_a_no_op(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    B.apply_bz_stars(dst, log=lambda *a: None)
    once = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert B.apply_bz_stars(dst, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == once


@needs_exe
def test_apply_then_revert_restores_ff7_en_byte_for_byte(tmp_path):
    dst = tmp_path / 'ff7_en'
    shutil.copy(EXE, dst)
    before = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert B.apply_exe_bz_stars(dst, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() != before
    assert B.apply_exe_bz_stars(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before


@needs_main
def test_a_modified_site_is_refused_rather_than_overwritten(tmp_path):
    """The stock-or-widened check is the only thing standing between this and
    another module's patch at the same address."""
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    m = nxmap.Main(str(dst))
    img = bytearray(m.img)
    struct.pack_into('<I', img, B.BZ_STARS_SITES[0][1], 0xDEADBEEF)

    class FakeMain:
        pass
    fake = FakeMain()
    fake.img = bytes(img)
    patches, _notes, problems = B.bz_stars_plan(fake)
    assert problems and 'neither the stock' in problems[0]
    assert not patches, 'it planned a write over an unrecognised word'


@needs_exe
def test_a_modified_size_word_is_refused(tmp_path):
    import exe_patch
    dst = tmp_path / 'ff7_en'
    data = bytearray(EXE.read_bytes())
    pe = exe_patch.parse_pe(bytes(data))
    off = exe_patch.va_to_offset(pe, B.BZ_STARS_EXE[0][1])
    struct.pack_into('<H', data, off, 333)
    dst.write_bytes(bytes(data))
    lines = []
    assert B.apply_exe_bz_stars(dst, log=lines.append) == 1
    assert any('expected 256 or 512' in ln for ln in lines), lines


# --------------------------------------------------------- Pandora's Box

@needs_ffnx
def test_pandora_matches_ffnx():
    """origin = wide_viewport_x - 170, re-derived from FFNx's own source."""
    import re
    src = FFNX.read_text()
    m = re.search(r'patch_code_int\(ff7_externals\.'
                  r'pandora_box_skill_draw_bg_flash_effect_568371\s*\+\s*'
                  r'(0x[0-9A-Fa-f]+),\s*wide_viewport_x\s*-\s*(\d+)\)', src)
    assert m, 'FFNx no longer patches Pandora the way this port expects'
    assert int(m.group(1), 16) == 0x3D
    wide_x = int(re.search(r'int wide_viewport_x = (-?\d+);',
                           (ROOT / 'repos' / 'FFNx-master' / 'src' /
                            'widescreen.h').read_text()).group(1))
    want = wide_x - int(m.group(2))
    assert want == -277, want
    # the patched word must encode exactly that
    assert B.PANDORA_WIDE == 0x12800000 | ((~want & 0xFFFF) << 5) | 8


@needs_main
def test_pandora_site_is_stock_and_inside_its_function():
    m = nxmap.Main(str(MAIN))
    assert word(m.img, B.PANDORA_SITE) == B.PANDORA_STOCK
    lo, hi = m.extent(B.PANDORA_X86)
    assert lo <= B.PANDORA_SITE < hi


@needs_main
def test_pandora_is_the_only_movn_of_minus_170_in_that_function():
    """If the recompiler ever emits a second one, the site is ambiguous."""
    m = nxmap.Main(str(MAIN))
    lo, hi = m.extent(B.PANDORA_X86)
    hits = [va for va in range(lo, hi, 4)
            if word(m.img, va) == B.PANDORA_STOCK]
    assert hits == [B.PANDORA_SITE], [hex(v) for v in hits]


@needs_main
def test_pandora_does_not_collide_with_shadow_flare_or_the_stars():
    """The first draft of this patch was a second copy of shadow flare."""
    claimed = {B.WHITE_FLASH_X, B.WHITE_FLASH_RIGHT}
    claimed |= {va for _l, va, _s, _n in B.BZ_STARS_SITES}
    assert B.PANDORA_SITE not in claimed


@needs_main
def test_pandora_apply_revert_roundtrip(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    before = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert B.apply_pandora(dst, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() != before
    m = nxmap.Main(str(dst))
    assert word(m.img, B.PANDORA_SITE) == B.PANDORA_WIDE
    assert B.apply_pandora(dst, log=lambda *a: None) == 0          # idempotent
    assert B.apply_pandora(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before


@needs_main
def test_pandora_refuses_an_unrecognised_word(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    m = nxmap.Main(str(dst))
    img = bytearray(m.img)
    struct.pack_into('<I', img, B.PANDORA_SITE, 0xDEADBEEF)

    class Fake:
        pass
    fake = Fake()
    fake.img = bytes(img)
    patches, _n, problems = B.pandora_plan(fake)
    assert problems and not patches

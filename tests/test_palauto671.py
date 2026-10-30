"""BUILD 671: ff7nx_palauto reads ADPAL2 and bounds held in variables, so
mds7plr2's reactor lights become truecolor with all 16 pulse values.

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
import struct
import sys

import pytest

HERE = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, HERE)
import ff7nx_palauto as A   # noqa: E402

VAN = os.path.join(HERE, 'dump/romfs/ff7/workingdir/data/field/flevel.lgp')


def _s0(name):
    if not os.path.exists(VAN):
        pytest.skip('no vanilla flevel')
    import lgp
    import ff7nx_daynight as D
    _i, rd = D._stream_index(VAN)
    return bytes(lgp.split_sections(lgp.lzs_decompress(rd(name)[4:]))[0])


def test_adpal2_layout_from_the_x86_handler():
    # mds7plr2's op: start 1 literal, B/G/R = var5[0], size 0xFE + 1
    s = bytes.fromhex('ef 05 55 00 00 10 01 00 00 00 fe')
    vo = A._value_op(s, 0, 0xEF)
    assert (vo['S'], vo['D'], vo['start'], vo['size']) == (0, 0x10, 1, 255)
    assert vo['ch'] == [(5, 0), (5, 0), (5, 0)]
    with pytest.raises(ValueError):            # start from a variable
        A._value_op(bytes.fromhex('ef 15 55 00 00 10 01 00 00 00 fe'), 0, 0xEF)
    import numpy as np
    c5 = np.array([[0, 0, 0], [10, 20, 31], [1, 2, 3]])
    out = A._apply(0xEF, c5, vo, 0xF0)        # v = -16, entries 1..255
    assert out.tolist() == [[0, 0, 0], [0, 4, 15], [0, 0, 0]]


def test_adpal_r_bank_reading_is_unchanged_by_default():
    """The E9/EA R bank is byte 3's high nibble on x86; reading it would
    change the 1x proof of fields already converted, so the old reading
    stays unless R_BANK_HI is set."""
    s = bytes.fromhex('e9 00 55 50 20 30 29 29 29 ff')
    assert A.R_BANK_HI is False
    assert A._value_op(s, 0, 0xE9)['ch'][2] == (0, 0x29)


def test_mds7plr2_lights_found_and_renumbered():
    s0 = _s0('mds7plr2')
    ef = A.find_effects(s0)
    assert [(e['q'], e['op'], e['var']) for e in ef] == [(4, 0xEF, 0)]
    uses = A.var_uses(s0, 0, skip={o for o, _l in ef[0]['sites']})
    consts = [k for _o, kind, k in uses if isinstance(k, tuple)]
    # IF v > var5[3] (= F0) and IF v < var5[2] (= FF): bounds in variables
    assert sorted((k[1], k[2]) for k in consts) == [(2, 0xFF), (3, 0xF0)]
    step, _anchor, lo, hi, _sets = A.lattice(s0, uses)
    assert (step, lo, hi) == (1, 240, 255)
    new = A.renumber(s0, uses, lo, step, 16, base=48)
    lg = s0.find(bytes.fromhex('80 50 01 00 80 50 00 ff 80 50 02 ff 80 50 03 f0'))
    assert lg > 0
    assert new[lg:lg + 16] == bytes.fromhex(
        '80 50 01 00 80 50 00 3f 80 50 02 3f 80 50 03 30')
    assert sum(a != b for a, b in zip(s0, new)) == 3


def test_const_var_refuses_a_written_or_read_bound():
    assert A.const_var(_script(b'\x80\x50\x03\xf0' b'\x14\x55\x00\x03\x02\x00'
                               b'\x00'), 3, 0) == (0xF0, (A_CODE,))
    # the bound is also stepped: not a constant
    assert A.const_var(_script(b'\x80\x50\x03\xf0' b'\x95\x05\x03'
                               b'\x14\x55\x00\x03\x02\x00\x00'), 3, 0) is None
    # two different literals
    assert A.const_var(_script(b'\x80\x50\x03\xf0' b'\x80\x50\x03\xe0'
                               b'\x14\x55\x00\x03\x02\x00\x00'), 3, 0) is None


def test_pillar_1_key_only_copy_is_followed():
    """pillar_1's second light copies entry 0 (the key) from the first
    light's buffer; both effects are now found (the field is still refused
    later: Cosmos has 14 / 23 frames for 31 / 32 states)."""
    s0 = _s0('pillar_1')
    ef = A.find_effects(s0)
    assert sorted((e['q'], e['var'], e['cp']) for e in ef) == [
        (7, 8, (0, 1)), (8, 10, (0, 1))]


A_CODE = None


def _script(body):
    global A_CODE
    import echo_s_flevel as ES
    head = ES._SECTION1_FIXED_HEADER
    code = head + 8 + ES._SECTION1_ROUTINE_TABLE_BYTES
    s = bytearray(code)
    s[2] = 1
    struct.pack_into('<H', s, 6, 0)
    struct.pack_into('<H', s, 4, code + len(body))
    for i in range(ES._SECTION1_ROUTINES_PER_ACTOR):
        struct.pack_into('<H', s, head + 8 + 2 * i, code)
    A_CODE = code
    return bytes(s) + body


def test_version_is_in_the_flevel_key():
    src = open(nxpaths.src_file('build.py')).read()
    assert "('palauto', ff7nx_palauto.VERSION)" in src.replace('\n', ' ') \
        or "ff7nx_palauto.VERSION)" in src
    assert A.VERSION >= 671          # bumped by every later reading change

"""BUILD 854: ff7nx_palauto follows KAWAI (it carries no variable), admits
onna_4's hand-proved palette shuttle, and keeps a tile the pair model cannot
follow at one image per state.

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
import struct

import numpy as np

import ff7nx_palauto as A


def _script(body):
    import echo_s_flevel as ES
    head = ES._SECTION1_FIXED_HEADER
    code = head + 8 + ES._SECTION1_ROUTINE_TABLE_BYTES
    s = bytearray(code)
    s[2] = 1
    struct.pack_into('<H', s, 6, 0)
    struct.pack_into('<H', s, 4, code + len(body))
    for i in range(ES._SECTION1_ROUTINES_PER_ACTOR):
        struct.pack_into('<H', s, head + 8 + 2 * i, code)
    return bytes(s) + body, code


# onna_4's lg1 routine body: palette 9 blinks through var5[4]
LG1 = bytes.fromhex('805004ff' '80500000' '00'
                    'e5000900' '7f' '240200'
                    'e9005550000804' '04047f' 'e700000800' 'e60008097f'
                    '145000000013' '145004f60207' '87500401' '1005'
                    '80500001' '1011' '145004ff0307' '85500401' '1005'
                    '80500000' '123c' '00')


def test_kawai_is_no_variable_reference():
    # a facial-animation KAWAI whose bytes include the variable's index (4)
    kawai = bytes.fromhex('28070004040000')
    body = LG1[:9] + kawai + LG1[9:]
    s0, code = _script(body)
    o = code + 9
    assert A._refs(s0, o, 0x28, 7) == []
    ef = [e for e in A.find_effects(s0) if e['q'] == 9]
    assert len(ef) == 1 and ef[0]['var'] == 4
    uses = A.var_uses(s0, 4, skip={x for x, _l in ef[0]['sites']})
    assert sorted(k for _o, k, _x in uses) == ['ifl', 'ifl', 'minus',
                                               'plus', 'set']
    # a KAWAI whose length byte disagrees is still unknown
    assert A._refs(s0, o, 0x28, 6) is None


def test_onna_4_shuttle_is_admitted_only_on_its_exact_bytes():
    sig, pals = A.SHUTTLES['onna_4']
    head = bytes.fromhex('805005ff' '805006ff' '80500100' '80500200' '00')
    s0, code = _script(head + sig + b'\x00')
    generic = {e['q'] for e in A.find_effects(s0)}
    assert 12 not in generic and 14 not in generic
    got = {e['q']: e for e in A.find_effects(s0, 'onna_4')}
    assert got[12]['var'] == 5 and got[12]['src_pal'] == 12
    assert got[14]['var'] == 6 and got[14]['src_pal'] == 14
    for q, e in got.items():
        (o, n), = e['sites']
        assert n == 15 and s0[o] == 0xE9 and s0[o + 10] == 0xE6
        assert s0[o + 13] == q
    # one byte different: generic rules only
    bad = bytearray(s0)
    bad[code + len(head) + 3] ^= 1
    assert {e['q'] for e in A.find_effects(bytes(bad), 'onna_4')} == generic
    assert A.find_effects(s0, 'onna_5') == A.find_effects(s0)


def test_exact_records_show_one_frame_per_state():
    frames = [np.full((2, 2, 3), i, np.float32) for i in range(12)]
    block = [58, 59, 60]
    recs = A.exact_records(frames, block)
    assert len(recs) == 12
    for i, (p, bits, img) in enumerate(recs):
        # pair_code: BGON block[v // 4], state v % 4
        assert p == block[i // 4] and bits == 1 << (i % 4)
        assert float(img[0, 0, 0]) == i


def test_pair_fit_cannot_follow_a_changing_shape():
    """The onna_4 lamp core: a texel rising and falling across the states
    is beyond A[i // 2] + B -- which is why such a tile goes exact."""
    seq = [10, 18, 41, 57, 106, 149, 122, 92, 94, 97, 92, 104]
    frames = [np.full((1, 1, 3), v, np.float32) for v in seq]
    _a, _l, _m, worst = A.pair_fit(frames, 2)
    assert worst > A.PAIR_MAX_ERR

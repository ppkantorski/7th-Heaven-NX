"""BUILD 671: mds7's opening pan ends where the follow camera picks up.

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
import ff7nx_scrollhandoff as H   # noqa: E402

DUMP = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data', 'field')
OLD = bytes.fromhex('68 00 00 a0 ff 5c 00 50 00')        # SCR2DL (-96, 92) 80
NEW = bytes.fromhex('62 00 50 00 02 5f 5f 5f 5f')        # SCRLC 80 linear, 4 NOP


def _script(body):
    """A one-actor section-1 script whose every routine is `body`."""
    import echo_s_flevel as ES
    head = ES._SECTION1_FIXED_HEADER
    code = head + 8 + ES._SECTION1_ROUTINE_TABLE_BYTES
    s = bytearray(code)
    s[2] = 1                                               # one actor
    struct.pack_into('<H', s, 6, 0)                        # no akao
    struct.pack_into('<H', s, 4, code + len(body))         # strings after
    for i in range(ES._SECTION1_ROUTINES_PER_ACTOR):
        struct.pack_into('<H', s, head + 8 + 2 * i, code)
    return bytes(s) + body


def _ops(script):
    return [(o, op) for st in H._stream(script) for o, op, _sz in st]


def _mds7_script():
    if not os.path.isfile(os.path.join(DUMP, 'flevel.lgp')):
        pytest.skip('needs the dump')
    import lgp
    import ff7nx_daynight as D
    _idx, rd = D._stream_index(os.path.join(DUMP, 'flevel.lgp'))
    raw = lgp.lzs_decompress(rd('mds7')[4:])
    return bytes(lgp.split_sections(raw)[0])


def test_mds7_last_pan_becomes_a_scroll_to_the_player():
    s = _mds7_script()
    hits = H.find(s)
    assert len(hits) == 1
    off, op = hits[0]
    assert op == H.OP_SCR2DL and s[off:off + 9] == OLD
    new, report = H.apply_to_script(s)
    assert new is not None and len(new) == len(s)
    assert new[off:off + 9] == NEW and '-> 62 00 50 00 02' in report
    assert [i for i in range(len(s)) if s[i] != new[i]] == \
        [off + i for i in range(9) if OLD[i] != NEW[i]]
    # every instruction boundary of the old script is still one, so no
    # branch target can land inside the edit; nothing else decodes differently
    old_b = set(o for o, _ in _ops(s))
    new_ops = _ops(new)
    assert old_b <= set(o for o, _ in new_ops)
    tail = [op for o, op in new_ops if off <= o < off + 11]
    assert tail == [0x62, 0x5F, 0x5F, 0x5F, 0x5F, 0x67, 0x65]
    # the first two pans are untouched; done once, a second pass finds nothing
    assert s.count(bytes.fromhex('68 00 00 05 00 32 00 c8 00')) == 1
    assert new.count(bytes.fromhex('68 00 00 05 00 32 00 c8 00')) == 1
    assert H.find(new) == []


def test_smooth_pan_keeps_its_easing():
    body = bytes.fromhex('66 00 00 10 00 20 00 1e 00') + b'\x67\x65\x00'
    new, _ = H.apply_to_script(_script(body))
    assert new.endswith(bytes.fromhex('62 00 1e 00 03 5f 5f 5f 5f 67 65 00'))


@pytest.mark.parametrize('body', [
    # operand from a variable: left alone
    bytes.fromhex('68 10 00 10 00 20 00 50 00') + b'\x67\x65\x00',
    bytes.fromhex('68 00 02 10 00 20 00 50 00') + b'\x67\x65\x00',
    # not directly followed by SCRLW, SCRCC
    bytes.fromhex('68 00 00 10 00 20 00 50 00') + b'\x67\x00',
    bytes.fromhex('68 00 00 10 00 20 00 50 00') + b'\x65\x00',
    # two candidates: ambiguous
    (bytes.fromhex('68 00 00 10 00 20 00 50 00') + b'\x67\x65') * 2 + b'\x00',
])
def test_refuses_anything_but_one_constant_pan_before_the_hand_off(body):
    s = _script(body)
    new, report = H.apply_to_script(s)
    assert new is None and 'found' in report


class _Ar:
    def __init__(self, raw):
        import lgp
        self.index = {'mds7': {'payload': self.encode_field(raw)}}
        self._raw = raw
        self.lgp = lgp

    def decompressed(self, entry):
        return self.lgp.lzs_decompress(entry['payload'][4:])

    @staticmethod
    def encode_field(raw):
        import lgp
        enc = lgp.lzs_store(raw)
        return struct.pack('<I', len(enc)) + enc


def _field(script):
    import lgp
    return lgp.join_sections([script] + [b'\x00' * 4] * 8)


def test_flevel_pass_edits_only_the_named_field(monkeypatch):
    import lgp
    s = _script(OLD + b'\x67\x65\x00')
    ar = _Ar(_field(s))
    payloads = {}
    st = H.apply_to_flevel(ar, payloads, encode=ar.encode_field)
    assert st['fields'] == ['mds7'] and not st['refused']
    got = lgp.split_sections(lgp.lzs_decompress(payloads['mds7'][4:]))[0]
    assert got.endswith(NEW + b'\x67\x65\x00')
    assert 'BUILD 671' in H.summarise(st)
    # off: nothing
    monkeypatch.setenv(H.ENV, '0')
    payloads = {}
    assert H.apply_to_flevel(ar, payloads, encode=ar.encode_field)['fields'] == []
    assert payloads == {}


def test_flevel_pass_reports_a_field_it_cannot_match():
    s = _script(b'\x67\x65\x00')
    ar = _Ar(_field(s))
    payloads = {}
    st = H.apply_to_flevel(ar, payloads, encode=ar.encode_field)
    assert st['fields'] == [] and payloads == {}
    assert st['refused'] and 'unchanged' in H.summarise(st)


def test_version_and_switch_are_in_the_flevel_key():
    src = open(nxpaths.src_file('build.py')).read()
    assert "('scrollhandoff', ff7nx_scrollhandoff.VERSION," in src
    assert 'ff7nx_scrollhandoff.enabled()' in src
    assert H.enabled({}) and not H.enabled({H.ENV: '0'})

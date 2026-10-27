"""BUILD 609: Echo-S's remaining PC button names become the Switch buttons.

Runs on the real Echo-S field set and the dump when present.

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
import re
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
PROJ = os.path.expanduser('~/mnt/7th_heaven_nx')

import lgp  # noqa: E402
import echo_s_flevel as E  # noqa: E402

DUMP = os.path.join(PROJ, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                    'field', 'flevel.lgp')
ECHO = os.path.join(PROJ, 'cache', 'Echo-s', 'Echo-S', 'flevel.lgp')
PC_WORDS = ('PAGEUP', 'PAGEDN', 'PAGEDOWN', 'PGUP', 'PGDN', 'PAGE UP',
            'PAGE DOWN', 'START', 'ASSIST', 'TARGET', 'CAMERA', 'MENU',
            'CANCEL', 'SWITCH', 'CONFIRM', 'OK', 'LEFT', 'RIGHT', 'UP',
            'DOWN', 'PAGEUP/PAGEDOWN')


def _dec(b):
    out = ''
    for c in b:
        if c == 0xff:
            break
        out += chr(c + 0x20) if c < 0x60 else '{%02X}' % c
    return out


def _strings(s0):
    so = struct.unpack_from('<H', s0, 4)[0]
    n = struct.unpack_from('<H', s0, so)[0]
    offs = struct.unpack_from('<%dH' % n, s0, so + 2)
    return [_dec(s0[so + o:]) for o in offs]


def _have():
    return os.path.exists(DUMP) and os.path.isdir(ECHO)


_ARC = {}


def _merged(field):
    if 'a' not in _ARC:
        _ARC['a'] = lgp.Archive(DUMP)
    a = _ARC['a']
    raw, _ = E.merge_field_payload(
        a.index[field]['payload'],
        open(os.path.join(ECHO, field), 'rb').read(), field_name=field)
    return _strings(lgp.split_sections(raw)[0])


def test_glyph_byte_table_matches_the_named_table():
    for byte, name in ((0x95, 'CANCEL'), (0x98, 'CONFIRM'), (0x97, 'MENU'),
                       (0x96, 'SWITCH'), (0x9B, 'CAMERA')):
        assert E.PC_BUTTON_GLYPH_BY_BYTE[byte] == E.PC_BUTTON_GLYPHS[
            E._enc(name)]


def test_elm_camera_tutorial():
    if not _have():
        return
    s = _merged('elm')[37]
    assert ('if you press {FE}{D5}{F6}2{FE}{D9} + {FE}{D5}{F6}4{FE}{D9} '
            'while on' in s)
    assert ('by pressing {FE}{D5}{F6}1{FE}{D9}\\n  or {FE}{D5}{F6}3{FE}{D9}'
            .replace('\\n', '{E7}') in s)
    assert 'PAGEDN' not in s and '{F6}0' not in s and 'RS' not in s


def test_targeted_fields_have_no_pc_button_words_left():
    if not _have():
        return
    rx = re.compile(r'\{FE\}\{D[2-8]\}\[?([A-Za-z /]+)\]?\{FE\}\{D9\}'
                    r'|\[([A-Za-z/]+)\]')
    for field in ('elm', 'junpb_2', 'subin_1b', 'games_2', 'snow',
                  'blackbg2', 'junmin1', 'whitebg3', 'mds7_w2', 'fship_4',
                  'crcin_1', 'niv_ti2', 'sinin1_2'):
        for s in _merged(field):
            for m in rx.finditer(s):
                w = (m.group(1) or m.group(2)).upper()
                assert w not in PC_WORDS, (field, w, s[:120])


def test_action_matches_the_ports_own_text():
    if not _have():
        return
    j = _merged('junpb_2')
    assert any('Hold {FE}{D5}{F6}1{FE}{D9} and {FE}{D5}{F6}3' in s for s in j)
    assert any('press {FE}{D5}{F6}6{FE}{D9} during battle' in s for s in j)
    sub = _merged('subin_1b')
    assert any('{F6}1{FE}{D9} Rapid ascent' in s for s in sub)
    assert any('{F6}4{FE}{D9} Viewscreen' in s for s in sub)
    snow = _merged('snow')
    assert sum(s.count('{F6}1{FE}{D9} + ') for s in snow) == 2
    g = _merged('games_2')
    assert any('{F6}:{FE}{D9} Move right' in s for s in g)   # was LEFT
    b = _merged('blackbg2')[0]
    assert '{F6}1{FE}{D9}: Reset Flag' in b
    assert '{F6}3{FE}{D9}:   Battle On/Off' in b


def test_a_changed_echo_text_fails_loudly():
    import pytest
    bad = {'elm': ((b'no such bytes', b'x', 1),)}
    old = E.FIELD_BUTTON_FIXES
    E.FIELD_BUTTON_FIXES = bad
    try:
        if not _have():
            return
        a = lgp.Archive(DUMP)
        s0 = lgp.split_sections(a.decompressed(a.index['elm']))[0]
        with pytest.raises(E.EchoFlevelError):
            E.apply_field_button_fixes(s0, 'elm')
    finally:
        E.FIELD_BUTTON_FIXES = old


def test_stock_kalm_hint_names_zl_and_zr():
    """BUILD 803: without Echo-S the port's own Kalm line is fixed too."""
    if not os.path.exists(DUMP):
        return
    a = lgp.Archive(DUMP)
    s0 = lgp.split_sections(a.decompressed(a.index['elm']))[0]
    fixed, n = E.apply_stock_button_fixes(s0, 'elm')
    assert n == 1
    s = _strings(fixed)[107]
    assert s.startswith('{B2}First press {F6}2 + {F6}4{E7}{E1}to change')
    # every other string is untouched
    assert _strings(fixed)[:107] == _strings(s0)[:107]
    assert _strings(fixed)[108:] == _strings(s0)[108:]


def test_stock_kalm_fix_leaves_echo_text_alone():
    if not _have():
        return
    a = lgp.Archive(DUMP)
    raw, _ = E.merge_field_payload(
        a.index['elm']['payload'],
        open(os.path.join(ECHO, 'elm'), 'rb').read(), field_name='elm')
    s0 = lgp.split_sections(raw)[0]
    assert E.apply_stock_button_fixes(s0, 'elm') == (s0, 0)

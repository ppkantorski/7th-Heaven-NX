"""BUILD 558: the rest of Echo-S's PC text markup (glyph forms, item icons,
the PC heart) found by classifying every string in the release.

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
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import echo_s_flevel as EF  # noqa: E402


def enc(text):
    return bytes(ord(c) - 0x20 for c in text)


def run(b):
    out, n = EF._strip_pc_field_markup(b + b'\xff')
    return out[:-1], n


def test_dpad_glyphs_before_words_are_dropped():
    # games_2: {FE}{D2}{BE}{BF}{93}{94}Direction Pad{FE}{D9}: Move
    src = b'\xfe\xd2\xbe\xbf\x93\x94' + enc('Direction Pad') + b'\xfe\xd9'
    out, n = run(src)
    assert out == b'\xfe\xd2' + enc('Direction Pad') + b'\xfe\xd9' and n == 1


def test_bracket_and_colour_between_forms():
    out, _ = run(b'\xfe\xd5\x99' + b'\x3b' + enc('PAGEUP') + b'\x3d'
                 + b'\xfe\xd9')
    # BUILD 609: PAGEUP is L (F6 11), sourced from the port's own strings
    assert out == b'\xfe\xd5\xf6\x11\xfe\xd9'
    # blin61: {98}{FE}{D5}(OK){FE}{D9} -- glyph 98 is CONFIRM, i.e. A
    out, _ = run(b'\x98\xfe\xd5\x08' + enc('OK') + b'\x09\xfe\xd9')
    assert out == b'\xfe\xd5\xf6\x10\xfe\xd9'


def test_named_only_glyphs():
    out, _ = run(b'\xfe\xd5\x82\x08' + enc('START') + b'\x09\xfe\xd9')
    assert out == b'\xfe\xd5\xf6\x15\xfe\xd9'        # BUILD 609: +
    # 82 NOT followed by a bracket is an ordinary character: untouched
    src = b'\x00\x82' + enc('Abc')
    assert run(src)[0] == src


def test_item_icons_in_lists_and_prose():
    # games_1 GP exchange
    src = b'\xe0\xfe\xe9\x77' + enc('Potion') + b'\x00\x11\x27\x30'
    assert run(src)[0] == (b'\xe0\xfe\xe9' + enc('Potion')
                          + b'\x00\x00\x11\x27\x30')   # BUILD 560 cell
    # "Obtained 3 {79}Elixirs!"
    src = enc('Obtained 3 ') + b'\x79' + enc('Elixirs!')
    assert run(src)[0] == enc('Obtained 3 Elixirs!')
    # materia icon before a colour wrapper keeps the wrapper
    src = b'\xe0\xca\xfe\xd5' + enc('Enemy Lure') + b'\xfe\xd9'
    assert run(src)[0] == b'\xe0\xfe\xd5' + enc('Enemy Lure') + b'\xfe\xd9'
    # "1/35 Soldier" (digit follows)
    src = enc('prize: ') + b'\x7d' + enc('1/35 Soldier')
    assert run(src)[0] == enc('prize: 1/35 Soldier')


def test_icon_bytes_left_alone_elsewhere():
    # not after a separator
    src = enc('ab') + b'\x77' + enc('Cd')
    assert run(src)[0] == src
    # FE E2 carries four argument bytes: never scanned as text
    src = b'\xfe\xe2\x62\x03\x06\x00' + enc('X')
    assert run(src)[0] == src


def test_pc_heart_removed():
    src = enc('Tee-hee.') + EF.PC_HEART + b'\xb3'
    assert run(src)[0] == enc('Tee-hee.') + b'\xb3'


def test_spaced_table_keeps_price_column():
    # BUILD 560, games_1 GP exchange: FE E9 fixed-pitch rows
    row = lambda icon, name, gap, price: (b'\xe0\xfe\xe9' + icon + enc(name)
                                          + b'\x00' * gap + enc(price)
                                          + b'\xfe\xe9\xe7')
    src = (row(b'\x77', 'Potion', 8, '1GP') + row(b'\x78', 'Turbo Ether', 1, '100GP')
           + row(b'', 'Gold Ticket', 2, '300GP') + b'\xe0' + enc('Never mind'))
    out, _ = run(src)
    want = (row(b'', 'Potion', 9, '1GP') + row(b'', 'Turbo Ether', 2, '100GP')
            + row(b'', 'Gold Ticket', 2, '300GP') + b'\xe0' + enc('Never mind'))
    assert out == want
    # proportional lists (coloin1 "Potion = 80") are not padded
    src = b'\xe0\x77' + enc('Potion = 80') + b'\xe7'
    assert run(src)[0] == b'\xe0' + enc('Potion = 80') + b'\xe7'

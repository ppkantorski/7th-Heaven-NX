"""BUILD 774: FX pages the 10-21 census found still paletted although the
mod has exactly one picture per palette (md8_3's fountain, woa_2 page 19,
mogu_1's average-band pages).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import ff7nx_fxcomplete as FC  # noqa: E402
import ff7nx_fxpages as XP  # noqa: E402


class _Prov:
    def __init__(self, slots=(), act=None, off=None, anyst=()):
        self.slots = dict.fromkeys(slots, ('p', 'e'))
        self.state_slots = act or {}
        self.inactive_state_slots = off or {}
        self.any_state_slots = set(anyst)


def test_md8_3_reaches_fxcomplete():
    assert 'md8_3' not in FC.CONFIRMED
    assert FC.SCRIPTED_OVERRIDE['md8_3'] == frozenset((8, 9, 10, 11))
    assert FC.ONCE_OVERRIDE['md8_3'] == FC.SCRIPTED_OVERRIDE['md8_3']
    # the other confirmed fields stay exactly as confirmed
    assert {'kuro_1', 'mrkt2', 'junbin5', 'del3', 'smkin_3',
            'mogu_1'} <= FC.CONFIRMED


def test_woa_2_only_page_19():
    assert 'woa_2' in FC.HOLD and FC.SLOTS == {'woa_2': (19,)}


def test_mogu_1_single_state_is_static():
    k = ('mogu_1', 24, 2)
    prov = _Prov(slots=[('mogu_1', 24, 0)],
                 act={('mogu_1', 24, 0): (('p', 'base'),)},
                 off={k: (('p', 'hash'),)}, anyst=[k])
    assert not XP.cosmos_animates(prov, 'mogu_1', 24, 2)
    # a second runtime dump anywhere is an animation again
    prov.inactive_state_slots[k] = (('p', 'h1'), ('p', 'h2'))
    assert XP.cosmos_animates(prov, 'mogu_1', 24, 2)
    # and the rule is per field / per palette
    other = ('kuro_1', 24, 2)
    prov2 = _Prov(slots=[('kuro_1', 24, 0)], off={other: (('p', 'h'),)},
                  anyst=[other])
    assert XP.cosmos_animates(prov2, 'kuro_1', 24, 2)

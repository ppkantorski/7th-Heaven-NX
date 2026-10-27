"""BUILD 669: Item menu -- list centred, Arrange evened out, top finger at
ESUI's height, the list viewport restored (ghost in the gaps).

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
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import ff7nx_esui as E
import ff7nx_esuicave as EC
import a64 as A

EMPTY = {'words': {}, 'sites': {}, 'hooks': [], 'replace': [], 'skip_calls': [],
         'store_const': [], 'stores': {}}


def _port():
    sites = {s: (0x6EB3B8, {0: ('add', 2), 1: ('add', -9)}, [])
             for s in (0xE13EA8, 0xE140E0, 0xE14358, 0xE14538, 0xE147C0)}
    sites[0xE1421C] = (0x6EB3B8, {1: ('add', -9)}, [])
    sites[0xE14D98] = (0x6F5B03, {1: ('add', -4)}, [])
    for s in (0xE13B74, 0xE143D8):
        sites[s] = (0x6EB3B8, {0: ('add', 33), 1: ('affd', 24, 37, 109, 111)}, [])
    sites[0xE15518] = (0x6EB3B8, {0: ('add', 12)}, [])
    sites[0xE15598] = (0x6EB3B8, {0: ('add', 5), 1: ('affd', 24, 37, 113, 111)}, [])
    return dict(EMPTY, sites=sites)


def test_item_menu_transforms():
    p = E._with_deltas('menus', 0x715105, _port())
    s = p['sites']
    for site in (0xE13EA8, 0xE140E0, 0xE14358, 0xE14538, 0xE147C0):
        assert s[site][1][1] == ('add', -6)                       # y 14 -> 17
        assert s[site][1][0] == ('addmap', 2, ((108, 92), (106, 90)))
    assert s[0xE1421C][1] == {1: ('add', -9), 0: ('addmap', 0, ((108, 92), (106, 90)))}
    assert s[0xE14D98][1][0] == ('addmap', 0, ((150, 134),))
    assert s[0xE13B74][1][0] == ('add', 14) and s[0xE15598][1][0] == ('add', -14)
    assert s[0xE15518][1][0] == ('add', -7)
    assert 'calls' not in p and 'backdrop' not in p
    assert E.TWEAKS['menus'] == {0xE1573C: A.movz(27, 0x175 - 19), 0xE15EC8: A.movz(8, 0x224 - 19),
                                 0xE15AD8: A.movz(28, 0x226 - 19)}


def test_caves_assemble():
    w = EC.callsite_cave(0x100000, 0x200000, {0: ('addmap', 2, ((108, 92),))})
    assert w[-1] == A.b(0x100000 + 4 * (len(w) - 1), 0x200000)
    w = EC.call_cave(0x100000, 0x300000, 0x294252E0, 0x200000, (0, 0, 0x280, 0x1E0))
    assert w[-2] == 0x294252E0 and w[-1] == A.b(0x100000 + 4 * (len(w) - 1), 0x300004)


def test_menu_backdrop():
    p = E._with_deltas('menus', 0x6CB56A, dict(EMPTY))
    assert p['backdrop'] == [(0xD05048, 0x294226E8, ((-160, -160, 800, 640),), 0.9999, 0xFF000000)]
    w = EC.backdrop_cave(0x100000, 0xD05048, 0x294226E8, 0x200000, *p['backdrop'][0][2:])
    assert w[-2] == 0x294226E8 and w[-1] == A.b(0x100000 + 4 * (len(w) - 1), 0xD0504C)
    out, _ = EC.build(p, lambda va: 0x294226E8, {0x66E272: 0x200000}, 0x100000)
    assert out[0xD05048] == A.b(0xD05048, 0x100000)


def test_shop_backdrop():
    """BUILD 836: the shop runs its own loop (71FF95), so it gets its own
    backdrop just before its draw call -- the Sell "How many" box left a
    trail in the 16:9 margins without it."""
    p = E._with_deltas('shop', 0x71FF95, dict(EMPTY))
    assert p['backdrop'] == [(0xE3D61C, 0xB94012E8, ((-160, -160, 800, 640),), 0.9999, 0xFF000000)]
    w = EC.backdrop_cave(0x100000, 0xE3D61C, 0xB94012E8, 0x200000, *p['backdrop'][0][2:])
    assert w[-2] == 0xB94012E8 and w[-1] == A.b(0x100000 + 4 * (len(w) - 1), 0xE3D620)
    out, _ = EC.build(p, lambda va: 0xB94012E8, {0x66E272: 0x200000}, 0x100000)
    assert out[0xE3D61C] == A.b(0xE3D61C, 0x100000)


def test_call_hook_refuses_other_word():
    port = dict(EMPTY, calls=[(0x1000, 0x6FA12F, (0,), 0x294252E0)])
    try:
        EC.build(port, lambda va: 0xD503201F, {0x6FA12F: 0x2000}, 0x8000)
    except ValueError as e:
        assert 'expected' in str(e)
    else:
        raise AssertionError('accepted a different word')


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'):
            f(); print('ok', n)

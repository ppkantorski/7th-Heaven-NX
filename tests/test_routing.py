#!/usr/bin/env python3
"""
test_routing.py -- a model's parts must land in the model's archive.

    python3 test_routing.py

THE BUG THIS PINS
=================
An entry name that exists in more than one vanilla archive used to be routed
by name alone, which split models across archives:

    fb/world/aja.hrc    -> world_us.lgp   (aja.hrc is a world_us.lgp entry)
    fb/world/CBHA.TEX   -> char.lgp       (cbha.tex is a char.lgp entry)

Vanilla's world chocobo is untextured, so nothing else put `cbha.tex` in
world_us.lgp and the model sampled a texture its archive did not contain.

THE FIX THIS PINS
=================
A part follows the model that names it, but only when that is provably
lossless -- either the part was never a name match in the archive it leaves,
or another candidate still supplies that name there.

And the fix must NOT be "the folder wins". The first attempt was, and it
dragged `aja.hrc` OUT of world_us.lgp, because the folder
"Chocobo - NinoStyle/World" votes char.lgp -- most of its file names happen
to exist in char.lgp. That regression is case 4 below.

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build                                                    # noqa: E402

FAIL = []


def check(name, got, want):
    if got == want:
        print('  ok    %-52s -> %s' % (name, got))
    else:
        FAIL.append(name)
        print('  FAIL  %-52s -> %s, wanted %s' % (name, got, want))


def scenario(tmp):
    """
    A miniature of the real thing.

      char.lgp     already has cbha.tex and aqgd.rsd
      world_us.lgp already has aja.hrc, and is untextured

      fb/char/    aaaa.hrc + its parts + CBHA.TEX + AQGD.rsd
      fb/world/   aja.hrc  + its parts + CBHA.TEX + AQGD.rsd + newtex.tex
    """
    os.makedirs(os.path.join(tmp, 'fb/char'), exist_ok=True)
    os.makedirs(os.path.join(tmp, 'fb/world'), exist_ok=True)

    def w(p, data):
        full = os.path.join(tmp, p)
        open(full, 'wb').write(data)
        return full

    hrc = (b':HEADER_BLOCK 2\r\n:SKELETON x\r\n:BONES 1\r\n\r\n'
           b'root\r\nroot\r\n1.0\r\n1 AQGD\r\n')
    rsd_char = b'@RSD940102\r\nPLY=AQGE.PLY\r\nMAT=X.MAT\r\nGRP=X.GRP\r\nNTEX=1\r\nTEX[0]=CBHA.TIM\r\n'
    rsd_world = b'@RSD940102\r\nPLY=AQGE.PLY\r\nMAT=X.MAT\r\nGRP=X.GRP\r\nNTEX=2\r\nTEX[0]=CBHA.TIM\r\nTEX[1]=NEWTEX.TIM\r\n'
    files = [
        ('fb/char/aaaa.hrc', hrc), ('fb/char/aqgd.rsd', rsd_char),
        ('fb/char/aqge.p', b'P'), ('fb/char/cbha.tex', b'CHAR-TEX'),
        ('fb/world/aja.hrc', hrc), ('fb/world/aqgd.rsd', rsd_world),
        ('fb/world/aqge.p', b'P'), ('fb/world/cbha.tex', b'WORLD-TEX'),
        ('fb/world/newtex.tex', b'NEW'),
    ]
    catalogs = {
        'char.lgp': {'aaaa.hrc', 'aqgd.rsd', 'aqge.p', 'cbha.tex'},
        'world_us.lgp': {'aja.hrc'},
    }
    cands = []
    for rel, data in files:
        full = w(rel, data)
        lowname = os.path.basename(rel)
        hits = [a for a, n in catalogs.items() if lowname in n]
        cands.append({'rel': rel, 'low': lowname, 'full': full, 'hits': hits,
                      'direct': hits[0] if hits else None,
                      'route': ('m.iro', os.path.dirname(rel))})
    return cands


def main():
    import tempfile
    from collections import Counter
    tmp = tempfile.mkdtemp()
    cands = scenario(tmp)

    votes = {}
    for c in cands:
        if c['direct']:
            votes.setdefault(c['route'], Counter())[c['direct']] += 1
    route_target = {k: v.most_common(1)[0][0] for k, v in votes.items()}
    print('folder votes:')
    for k, v in votes.items():
        print('    %-16s %s  -> %s' % (k[1], dict(v), route_target[k]))
    print()

    moved = build._reroute_by_folder(cands, route_target)
    where = {c['rel']: (c.get('target')
                        or build._provisional_target(c, route_target))
             for c in cands}

    print('%d part(s) rerouted\n' % len(moved))
    # 1-3: the world model and its parts end up together in world_us.lgp
    check('fb/world/aja.hrc   (the model)', where['fb/world/aja.hrc'],
          'world_us.lgp')
    check('fb/world/cbha.tex  (name is a char.lgp entry)',
          where['fb/world/cbha.tex'], 'world_us.lgp')
    check('fb/world/aqgd.rsd  (name is a char.lgp entry)',
          where['fb/world/aqgd.rsd'], 'world_us.lgp')
    check('fb/world/aqge.p    (name is a char.lgp entry)',
          where['fb/world/aqge.p'], 'world_us.lgp')
    check('fb/world/newtex.tex (in no vanilla archive)',
          where['fb/world/newtex.tex'], 'world_us.lgp')
    # 4: the char model keeps everything
    check('fb/char/aaaa.hrc', where['fb/char/aaaa.hrc'], 'char.lgp')
    check('fb/char/cbha.tex   (char keeps its own)',
          where['fb/char/cbha.tex'], 'char.lgp')
    check('fb/char/aqgd.rsd', where['fb/char/aqgd.rsd'], 'char.lgp')
    check('fb/char/aqge.p', where['fb/char/aqge.p'], 'char.lgp')

    print('\nthe guarantee: no archive loses a name it would have had')
    before, after = {}, {}
    for c in cands:
        b = build._provisional_target(c, route_target)
        before.setdefault(b, set()).add(c['low'])
        after.setdefault(where[c['rel']], set()).add(c['low'])
    ok = True
    for arch, names in before.items():
        lost = {n for n in names - after.get(arch, set())
                if n in {'aaaa.hrc', 'aqgd.rsd', 'aqge.p', 'cbha.tex',
                         'aja.hrc'}}
        if lost:
            ok = False
            FAIL.append('%s lost %s' % (arch, sorted(lost)))
            print('  FAIL  %s would lose %s' % (arch, sorted(lost)))
    if ok:
        print('  ok    every vanilla name still supplied to its archive')

    print('\n%d failure(s)' % len(FAIL))
    if FAIL:
        return 1
    print('PASS')
    return 0


def test_unmatched_model_archive_leaf_fallback():
    """Texture-only world/high option folders are not silently dropped."""
    empty_votes = {}
    for rel, want in (
            ('Buster Sword Field Model/world/AAAA1.TEX', 'world_us.lgp'),
            ('Buster Sword Field Model/high/BSCA1.P', 'high-us.lgp'),
            ('Buster Sword Field Model/char/FSW0.TEX', 'char.lgp')):
        got = build._provisional_target(
            {'rel': rel, 'direct': None, 'route': ('m.iro',
                                                   os.path.dirname(rel))},
            empty_votes)
        assert got == want, (rel, got, want)


if __name__ == '__main__':
    sys.exit(main())

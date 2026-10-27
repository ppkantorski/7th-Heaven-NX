#!/usr/bin/env python3
"""
test_minigame_routing.py -- minigame archives are addressed by FOLDER.

    python3 tests/test_minigame_routing.py

THE BUG THIS PINS (build 364)
=============================
Once chocobo.lgp & co. joined the name catalogs, a bare name match beat the
folder, and Ninostyle's new character skins were packed over chocobo-race
art that happens to share their two-letter names:

    fb/char/AB.tex                       -> chocobo.lgp  (race panel art)
    fb/char/bo.TEX                       -> chocobo.lgp  (race NUMBER FONT)
    fb/high/br.TEX                       -> chocobo.lgp  (racer numbers)
    Alt. Buggy2 - NinoStyle/World/bg.TEX -> chocobo.lgp  (gold panels)

while Ninostyle Fixes' fb/chocobo/CI.TEX had no name match anywhere and was
dropped. 7th Heaven routes by folder (direct/chocobo, direct/char, ...).

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build                                                    # noqa: E402

FAIL = []


def check(name, got, want):
    ok = got == want
    print('  %s  %-58s -> %s' % ('ok  ' if ok else 'FAIL', name, got))
    if not ok:
        FAIL.append(name)


H = build._minigame_direct_hits
# the exact build-364 collisions
check('fb/char/AB.tex keeps only char-side hits',
      H('fb/char/AB.tex', ['chocobo.lgp']), [])
check('fb/char/bo.TEX may not name-match the race font',
      H('fb/char/bo.TEX', ['chocobo.lgp', 'fchocobo.lgp']), [])
check('fb/high/br.TEX falls back to its own routing',
      H('fb/high/br.TEX', ['chocobo.lgp']), [])
check('world folder never matches a minigame by name',
      H('Alt. Buggy2 - NinoStyle/World/bg.TEX', ['chocobo.lgp']), [])
check('Windows separators are honoured',
      H('fb\\char\\se.tex', ['chocobo.lgp']), [])
# minigame folders always go to their own (US-named) archive
check('fb/chocobo/CI.TEX is a chocobo.lgp entry with no vanilla name',
      H('fb/chocobo/CI.TEX', []), ['chocobo.lgp'])
check('fb/chocobo/CBHA.TEX goes to chocobo.lgp despite a char.lgp name',
      H('fb/chocobo/CBHA.TEX', ['char.lgp']), ['chocobo.lgp'])
check('Dynamic Weapons/Chibi/Chocobo is the chocobo folder too',
      H('Dynamic Weapons/Chibi/Chocobo/cl.TEX', []), ['chocobo.lgp'])
for leaf, arc in (('coaster', 'coaster.lgp'), ('condor', 'condor.lgp'),
                  ('snowboard', 'snowboard-us.lgp'), ('sub', 'sub.lgp')):
    check('%s folder -> %s' % (leaf, arc), H('x/%s/a.tex' % leaf, []), [arc])
# everything that is not a minigame is untouched, order preserved
check('char/world/high hits pass through unchanged',
      H('fb/char/aaaa.tex', ['char.lgp', 'world_us.lgp', 'high-us.lgp']),
      ['char.lgp', 'world_us.lgp', 'high-us.lgp'])
check('high is NOT a folder-authoritative minigame (unchanged behaviour)',
      H('fb/high/caaa.hrc', ['high-us.lgp', 'high-fr.lgp']),
      ['high-us.lgp', 'high-fr.lgp'])
check('only minigame hits are stripped from a mixed list',
      H('fb/char/x.tex', ['char.lgp', 'chocobo.lgp', 'condor.lgp']),
      ['char.lgp'])
# the table agrees with ARCHIVES
check('every folder archive exists in ARCHIVES',
      all(a in build.ARCHIVES for a in build._MINIGAME_FOLDER_ARCHIVES), True)

print('\n%d failure(s)' % len(FAIL))
print('PASS' if not FAIL else 'FAIL')
sys.exit(1 if FAIL else 0)

#!/usr/bin/env python3
"""exit-report: every engine exit() site traps, and --off restores stock.

    python3 tests/test_exit_report.py [path/to/main]

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
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import diag_toggle as T                                         # noqa: E402
import nso_tool                                                 # noqa: E402

src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'dump',
                                                         'exefs', 'main')
FAIL = []


def check(name, ok):
    print('  %s  %s' % ('ok  ' if ok else 'FAIL', name))
    if not ok:
        FAIL.append(name)


sw = T.SWITCHES['exit-report']
check('231 exit sites declared', len(T.EXIT_SITES) == 231)
check('dispatcher traps on the mov, not the bl',
      {0x9CA4, 0xA2A0, 0xA698} <= set(sw['words'])
      and not ({0x9CA8, 0xA2A4, 0xA69C} & set(sw['words'])))
with tempfile.TemporaryDirectory() as d:
    m = os.path.join(d, 'main')
    shutil.copy(src, m)
    check('module starts at stock', T.state_of(m, 'exit-report') == 'off')
    check('--on succeeds', T.main([m, 'exit-report', '--on']) == 0)
    words = T.read_words(m, sw['words'])
    check('every site is udf #0', all(w == 0 for w in words.values()))
    before = T.state_of(m, 'heapabort')
    other = 'off' if before == 'on' else 'on'
    T.main([m, 'heapabort', '--' + other])
    check('heapabort still toggles alongside',
          T.state_of(m, 'heapabort') == other
          and T.state_of(m, 'exit-report') == 'on')
    T.main([m, 'heapabort', '--' + before])
    check('--off succeeds', T.main([m, 'exit-report', '--off']) == 0)
    a, b = nso_tool.parse_nso(m), nso_tool.parse_nso(src)
    check('round trip is byte-identical in every segment',
          all(a['segments'][s]['data'] == b['segments'][s]['data']
              for s in ('.text', '.rodata', '.data')))
    check('segment hashes valid', all(a['segments'][s]['hash_ok'] is not False
                                      for s in a['segments']))
print('\n%d failure(s)' % len(FAIL))
print('PASS' if not FAIL else 'FAIL')
sys.exit(1 if FAIL else 0)

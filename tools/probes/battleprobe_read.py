#!/usr/bin/env python3
"""
battleprobe_read.py -- decode the crash report of an L3 press with
battleprobe on ('BTL1').

    python3 battleprobe_read.py <crash_report.log>

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

HERE = nxpaths.ROOT
sys.path.insert(0, HERE)

import frameprobe_read as FR                 # noqa: E402

MAGIC = 0x314C5442
KNOWN = {
    0x46BA0B: 'Omnislash loop',
    0x5D4240: 'vincent_limit_fade_effect',
    0x45CF2A: 'vincent_limit_satan_slam_camera',
    0x45C0C0: 'vincent_limit_satan_slam',
}


def names():
    """x86 address -> FFNx-style name, from the dispatch tables' own names."""
    out = dict(KNOWN)
    for mod in ('ff7nx_dispatch', 'ff7nx_dispatch_sites'):
        try:
            src = open(os.path.join(HERE, mod + '.py')).read()
        except OSError:
            continue
        for m in re.finditer(r"'([A-Za-z0-9_]+?_([0-9A-Fa-f]{6}))'", src):
            out.setdefault(int(m.group(2), 16), m.group(1))
    return out


def slot_list(words, n, title, nm, w):
    shown = min(n, len(words) // 2)
    w('%s: %d live%s' % (title, n, '' if shown == n else
                         ' (first %d shown)' % shown))
    for k in range(shown):
        a, b = words[2 * k], words[2 * k + 1]
        fn, slot = a & 0xFFFFFF, a >> 24
        f0, nf = b & 0xFFFF, b >> 16
        w('  slot %3d  fn 0x%06X %-44s  field_0 0x%04X  n_frames %d'
          % (slot, fn, nm.get(fn, ''), f0, nf - 0x10000 if nf & 0x8000 else nf))


def report(text, out=sys.stdout):
    w = lambda s='': out.write(s + '\n')
    thread = FR._crashed_thread(text) if hasattr(FR, '_crashed_thread') else text
    stack = FR.parse_dump(thread, 'Stack Dump')
    tls = FR.parse_dump(thread, 'TLS Dump')
    if len(tls) < 256 or struct.unpack_from('<I', tls, 0)[0] != MAGIC:
        w('not a battleprobe report (no BTL1 in the TLS dump)')
        return 1
    t = struct.unpack_from('<64I', tls, 0)
    s = struct.unpack_from('<64I', stack, 0) if len(stack) >= 256 else (0,) * 64
    n10, ncam, n60, n100 = (t[1] & 0xFF, (t[1] >> 8) & 0xFF,
                            (t[1] >> 16) & 0xFF, t[1] >> 24)
    w('battle paused byte: %d   script wait frames: %d   driver mode: %d'
      % (t[2] & 0xFF, (t[2] >> 8) & 0xFF, t[2] >> 16))
    w('anim_event_queue[0..]: %s' % tls[16:48].hex(' '))
    w('AI context head (lastCommand, lastAction, ...): %s' % tls[48:64].hex(' '))
    for a in range(10):
        st, fl = t[16 + 2 * a], t[17 + 2 * a]
        if st or fl:
            w('  actor %d: status 0x%08X  state 0x%08X' % (a, st, fl))
    nm = names()
    slot_list(t[36:56], n10, 'effect10 (movement)', nm, w)
    slot_list(t[56:64], ncam, 'camera', nm, w)
    slot_list(s[0:24], n60, 'effect60 (per-frame effects)', nm, w)
    slot_list(s[24:64], n100, 'effect100 (actions)', nm, w)
    return 0


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    return report(open(argv[1], encoding='utf-8', errors='replace').read())


if __name__ == '__main__':
    sys.exit(main(sys.argv))

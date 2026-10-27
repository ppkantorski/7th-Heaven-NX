"""tools/esui/collect.py

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
import gen, struct, json, sys, os
FNS = [0x6c62a2, 0x6c98a6, 0x6ca1ef, 0x6ca23f, 0x6ca2a9, 0x6ca346, 0x6f62f7, 0x6f9739, 0x70212a, 0x703176]
os.environ['DEFOK'] = ''


def collect():
    allw = {}
    for fn in FNS:
        e, log = gen.all_for(fn)
        for a, v in e.items():
            if a in allw and allw[a][1] != v[1]:
                raise SystemExit('conflict at %x' % a)
            allw[a] = (v[0], v[1], fn, v[3], v[4], v[5])
    return allw


if __name__ == '__main__':
    W = collect()
    import nxmap
    B = nxmap.Main('tools/esui/main.built')
    S = nxmap.Main('dump/exefs/main')
    bad = 0
    for a, (w0, w1, fn, o, n, st) in sorted(W.items()):
        cb = struct.unpack_from('<I', B.img, a)[0]
        cs = struct.unpack_from('<I', S.img, a)[0]
        if cb != cs or cs != w0:
            bad += 1
            print('CONFLICT %x fn %x stock %08x built %08x ours-expect %08x' % (a, fn, cs, cb, w0))
    near = set()
    for a in W:
        for d in range(-32, 36, 4):
            b = a + d
            if struct.unpack_from('<I', B.img, b)[0] != struct.unpack_from('<I', S.img, b)[0]:
                near.add(b)
    print('edits', len(W), 'conflicts', bad, 'build-changed words within 32 bytes:',
          sorted(hex(x) for x in near))
    json.dump({'%x' % a: [w0, w1, '%x' % fn, o, n, st] for a, (w0, w1, fn, o, n, st) in sorted(W.items())},
              open('edits_main.json', 'w'), indent=0)

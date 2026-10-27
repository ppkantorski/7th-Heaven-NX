"""ARM draw list of a menu function on a module: strings (x, y, ink box) and bars/boxes

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
import sys, os, collections
S = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, '.'); sys.path.insert(0, 'tools/esui'); sys.path.insert(0, S)
import preview2 as P2, render_arm as RA, ff7nx_esui as E, fontmetrics as FM
BUILT = '../../7th_heaven_nx/sdout/atmosphere/contents/0100A5B00BDC6000/romfs/ff7'
EN = open(BUILT + '/resources/ff7_1.02/ff7_en', 'rb').read()
secs, _ = E._sections(open(BUILT + '/workingdir/data/lang-en/kernel/window.bin', 'rb').read()); WID = secs[2][1][:256]
SHEET = {'a': 'usfont_a_l.tex', 'b': 'usfont_b_l.tex', 'winb': 'btl_win_b_l.tex', 'wind': 'btl_win_d_l.tex'}
def run(fn, module, pokes=None, en=None):
    RA.FN = fn; RA.MODULE[0] = module; RA._M.clear(); RA.NQ[0] = True
    pk = dict(P2.pk); pk.update(pokes or {})
    ev, st = RA.run(en or EN, WID, pk, names=P2.names)
    strings = collections.OrderedDict(); other = []
    for e in ev:
        if e[0] == 'quad':
            _, which, vs, g = e
            if which not in SHEET or len(g) < 2: continue
            key = (g[0], g[1])
            for k in range(0, len(vs), 4):
                (x0, y0, u0, v0), (_, y1, _, v1), (x1, _, u1, _) = vs[k:k + 3]
                m = FM.ink(SHEET[which], u0, v0, u1, v1, p=(min(15, g[2] & 0xff) if which in ('winb', 'wind') else min(15, (g[2] & 7) * 2)) if len(g) > 2 else 14)
                if m is None: continue
                s = strings.setdefault(key, {'x': g[0], 'y': g[1], 'top': 1e9, 'bot': -1e9, 'l': 1e9, 'r': -1e9, 'n': 0, 'sheet': which})
                sc = (y1 - y0) / 24.0
                s['top'] = min(s['top'], y0 + m[0] * sc); s['bot'] = max(s['bot'], y0 + m[1] * sc)
                s['l'] = min(s['l'], x0); s['r'] = max(s['r'], x1); s['n'] += 1
        else:
            other.append(e)
    # glyph calls -> words: same y, touching
    gl = sorted(strings.values(), key=lambda s: (s['y'], s['x']))
    words = []
    for g in gl:
        w = words[-1] if words else None
        if w and w['y'] == g['y'] and w['sheet'][0] == g['sheet'][0] and g['l'] - w['r'] < 14:
            for k in ('top',): w[k] = min(w[k], g[k])
            w['bot'] = max(w['bot'], g['bot']); w['r'] = max(w['r'], g['r']); w['n'] += g['n']
        else:
            words.append(dict(g))
    return words, other, st
if __name__ == '__main__':
    fn = int(sys.argv[1], 16); mod = sys.argv[2]
    ss, oth, st = run(fn, mod)
    print(st)
    for s in ss: print('str x%4d y%4d ink %.1f..%.1f  l%.0f r%.0f n%d %s' % (s['x'], s['y'], s['top'], s['bot'], s['l'], s['r'], s['n'], s['sheet']))
    for e in oth[:40]: print(e[0], e[1][:6])

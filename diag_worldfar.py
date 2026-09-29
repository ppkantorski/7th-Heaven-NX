#!/usr/bin/env python3
r"""
diag_worldfar.py -- turn the world-map module off (or back on) in a BUILT
main, for A/B tests on hardware. No rebuild.

    python3 diag_worldfar.py <main> --show
    python3 diag_worldfar.py <main> far --off     far field + my sky only
    python3 diag_worldfar.py <main> all --off     every hook of ff7nx_worldfar
    python3 diag_worldfar.py <main> --on          put back what --off took out

`far --off`  puts back the game's own terrain-draw call (+0xF3632C) and its
             four cloud/meteor quad calls, so the far field and my sky are
             never drawn. The planet bend, heights, sink, camera and
             controls stay.
`all --off`  puts back EVERY word ff7nx_worldfar changed: all hooks, the
             Highwind ceiling, the camera pitch, the ZL/ZR key cases, the
             primitive-buffer and world-allocation sizes. The module code
             and its BSS are still in the file but nothing reaches them --
             the world map is the stock one (with the shaders' depth).

The words --off replaces are saved beside the module
(<main>.worldfar-diag.json) and --on restores exactly those. A rebuild also
restores everything. Writes go through nso_patcher like diag_toggle.py:
every site is checked against the word read from the file first.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import a64 as A                      # noqa: E402
import ff7nx_worldfar as W           # noqa: E402
import diag_toggle as D              # noqa: E402


def stock_words(which):
    """{va: stock word} for the chosen set."""
    out = {W.HOOK_SITE: A.bl(W.HOOK_SITE, W.HOOK_TARGET)}
    for site in W.SKY_CALLS:
        out[site] = A.bl(site, W.SKY_TARGET)
    if which == 'far':
        return out
    out[W.CAMERA_CALL] = A.bl(W.CAMERA_CALL, W.CAMERA_TARGET)
    out[W.INPUT_CALL] = A.bl(W.INPUT_CALL, W.INPUT_TARGET)
    out[W.ALLOC_CALL] = A.bl(W.ALLOC_CALL, W.ALLOC_TARGET)
    out[W.MOVE_CALL] = A.bl(W.MOVE_CALL, W.MOVE_TARGET)
    for site in W.TRANSFORM_CALLS:
        out[site] = A.bl(site, W.TRANSFORM_TARGET)
    for site in W.SINK_CALLS:
        out[site] = A.bl(site, W.SINK_TARGET)
    for site in W.HEIGHT_CALLS:
        out[site] = A.bl(site, W.HEIGHT_TARGET)
    out[W.PITCH_SITE] = W.PITCH_ORIG
    out[W.DI_ZL_SITE] = W.DI_ZL_ORIG
    out[W.DI_ZR_SITE] = W.DI_ZR_ORIG
    for va, word, _what in W.CEILING_WORDS:
        out[va] = word
    for va, word, _new, _what in W.MINIMAP_WORDS:
        out[va] = word
    for pairs in (W.PRIM_LIMIT, W.PRIM_SECOND, W.ALLOC):
        for va, word in pairs:
            out[va] = word
    return out


def sidecar(path):
    return path + '.worldfar-diag.json'


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    path = argv[1]
    if argv[2] == '--show':
        cur = D.read_words(path, list(stock_words('all')))
        stock = stock_words('all')
        n = sum(1 for va in stock if cur[va] != stock[va])
        far = all(cur[va] == w for va, w in stock_words('far').items())
        print('  %d of %d world-map sites are modded; far field %s'
              % (n, len(stock), 'OFF' if far else 'on'))
        print('  sidecar: %s' % ('present' if os.path.exists(sidecar(path))
                                 else 'none'))
        return 0
    if argv[2] == '--on':
        if not os.path.exists(sidecar(path)):
            print('  nothing to restore (no %s) -- rebuild instead'
                  % os.path.basename(sidecar(path)))
            return 1
        saved = {int(k): v for k, v in json.load(open(sidecar(path))).items()}
        n = D.write_words(path, saved, 'worldfar restore')
        os.remove(sidecar(path))
        print('  worldfar -> on   (%d word(s) restored)' % n)
        return 0
    which = argv[2]
    if which not in ('far', 'all') or len(argv) < 4 or argv[3] != '--off':
        print(__doc__)
        return 2
    target = stock_words(which)
    cur = D.read_words(path, list(target))
    saved = {}
    if os.path.exists(sidecar(path)):
        saved = {int(k): v for k, v in json.load(open(sidecar(path))).items()}
    for va, word in cur.items():
        if word != target[va] and va not in saved:
            saved[va] = word
    json.dump({str(k): v for k, v in saved.items()}, open(sidecar(path), 'w'),
              indent=1)
    n = D.write_words(path, target, 'worldfar %s off' % which)
    print('  worldfar %s -> off   (%d word(s) put back to stock)' % (which, n))
    if which == 'far':
        print('  the far field and the module\'s clouds/meteor are never drawn;'
              ' the planet bend, heights, camera and controls stay')
    else:
        print('  every ff7nx_worldfar hook is off: the world map is the stock '
              'one (only the shaders\' depth remains)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))

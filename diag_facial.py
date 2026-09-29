#!/usr/bin/env python3
r"""
diag_facial.py -- turn the facial-animation runtime off (or back on) in a
BUILT main, for an A/B test on hardware. No rebuild.

    python3 diag_facial.py <main> --show
    python3 diag_facial.py <main> --off     the five hooks back to stock
    python3 diag_facial.py <main> --on      put back what --off took out

WHY (BUILD 601)
The facial runtime (ff7nx_facial) parks its own eye textures in each field
model's anim entry and gives them back from a hook at the top of
field_load_models (x86 0x63E1D8) -- i.e. when the NEXT field loads models.
Build 517 made that hook skip models the game rebuilt in between (battles,
the title screen). Leaving a field for the WORLD MAP is the same shape and is
not covered: the game tears the field's models down (freeing whatever the
anim entries hold -- our art), and the next field_load_models then walks the
OLD anim table, finds our art pointer still there in the freed memory, writes
the stock pointer into it and unloads our art a second time.

--off restores the five hook words (blink, mouth, kawai, free, speak) to the
stock instructions they displaced. The caves stay in the file, unreachable:
the game is the stock one for faces (no custom eyes, no lip flap). Everything
else in the build is untouched.

The replaced words are saved beside the module (<main>.facial-diag.json) and
--on restores exactly those. A rebuild also restores everything. Every site
is checked against the word read from the file first (nso_patcher).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import diag_toggle as D              # noqa: E402
import ff7nx_facial as F             # noqa: E402


def stock_words():
    return {va: orig for _name, va, orig, _what in F.sites(voice_bss=1)}


def sidecar(path):
    return path + '.facial-diag.json'


def main(argv):
    if len(argv) < 3 or argv[2] not in ('--show', '--off', '--on'):
        print(__doc__)
        return 2
    path = argv[1]
    stock = stock_words()
    cur = D.read_words(path, list(stock))
    hooked = [va for va in stock if cur[va] != stock[va]]
    if argv[2] == '--show':
        print('  facial runtime: %d of %d hooks installed%s'
              % (len(hooked), len(stock),
                 '' if hooked else '  (OFF / stock faces)'))
        print('  sidecar: %s' % ('present' if os.path.exists(sidecar(path))
                                 else 'none'))
        return 0
    if argv[2] == '--on':
        if not os.path.exists(sidecar(path)):
            print('  nothing to restore (no %s) -- rebuild instead'
                  % os.path.basename(sidecar(path)))
            return 1
        saved = {int(k): v for k, v in json.load(open(sidecar(path))).items()}
        n = D.write_words(path, saved, 'facial restore')
        os.remove(sidecar(path))
        print('  facial runtime -> on   (%d hook(s) restored)' % n)
        return 0
    if not hooked:
        print('  already off')
        return 0
    for va in hooked:
        if (cur[va] >> 26) not in (0x05, 0x25):
            print('  +0x%X is %08X: not a branch and not stock -- refusing'
                  % (va, cur[va]))
            return 1
    saved = {}
    if os.path.exists(sidecar(path)):
        saved = {int(k): v for k, v in json.load(open(sidecar(path))).items()}
    for va in hooked:
        saved.setdefault(va, cur[va])
    json.dump({str(k): v for k, v in saved.items()}, open(sidecar(path), 'w'),
              indent=1)
    n = D.write_words(path, {va: stock[va] for va in hooked}, 'facial off')
    print('  facial runtime -> off  (%d hook(s) put back to stock)' % n)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))

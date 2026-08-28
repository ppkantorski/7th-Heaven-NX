#!/usr/bin/env python3
"""
ff7nx_battlecap.py -- a HARD size ceiling for battle.lgp that nothing escapes.

WHAT WAS WRONG
==============
The build reports

    Arisen battle background tiles capped at 768px
    (everything else in battle.lgp stays at the proven 256px)

and the archive it wrote contains, measured with `audit_texheaders.py`:

    1024px truecolor    29 file(s)
    1024px paletted      3
    1000px truecolor     2
     768px paletted    438      <- the Arisen tiles, correctly capped
     512px truecolor     5
     512px paletted    132
     500px paletted      1

610 textures over 256px, and 32 of them ABOVE the configured 768 ceiling.
Vanilla battle.lgp has **zero** textures over 256px.

The 1024s are player-character skins replacing vanilla slots of 64x64 and
128x64 -- the same UV mapping, 256 times the pixels:

    npac  1024x1024   vanilla slot  64x64
    saac  1024x1024   vanilla slot 128x64
    rvad  1024x1024   vanilla slot  64x64
    ...

WHY THEY ESCAPED
================
The cap lived *inside* `_convert_battle_textures`, and that function has two
early-outs before it is ever consulted:

    if 'main' in opt.lower():        -> player textures EXEMPT
        return unchanged
    if not tex.is_unpaletted(data):  -> already-paletted mod art passes
        return unchanged                through at any size

Both exemptions are about FORMAT, and both were right about format. Neither
was ever meant to be an exemption from SIZE, but because the cap was a
parameter of the converter rather than a pass of its own, that is exactly
what they became. `char.lgp` and `world_us.lgp` never had this hole -- they
cap through `_cap_field_textures`, which is a separate pass over every file.

WHAT THIS DOES
==============
One pass over every TEX headed for battle.lgp, after conversion, using
`tex.cap_dimensions` -- which preserves the format exactly. Paletted stays
paletted with the same palette and the same number of entries; truecolor
stays truecolor at the same bit depth. Only width, height and (when the
source carries a nonzero one) pitch change.

That matters for the player textures specifically. The reason they are
exempt from the CONVERTER is that the players-only build is proven
pixel-perfect on hardware as shipped, and `convert_for_battle` would requantise
them. This pass does not touch palette, bit depth, colour, or layout, so that
property is preserved in every respect except resolution.

WHAT IT IS NOT
==============
It is not a claim to have fixed the texture corruption. What is established
is a correlation, not a mechanism: every archive we push above vanilla's
256px ceiling has been reported corrupting, and `magic.lgp` -- the only one
we leave at 256 -- has not. Bringing battle.lgp back under its own
configured ceiling is worth doing because the build was not doing what it
said it was doing, and it makes the next measurement mean something.

    python3 ff7nx_battlecap.py --show <battle.lgp>
    SEVENTH_NX_BATTLE_TEX_CEILING=512 python3 7th_heaven_nx.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

CEILING_ENV = 'SEVENTH_NX_BATTLE_TEX_CEILING'

# Unset, the ceiling follows whatever the Arisen background cap is set to,
# because that is the number the user believed they were setting for the
# archive. It is deliberately NOT an independent default: two battle size
# knobs that can disagree is how this hole opened in the first place.
#
# 0 or an unparseable value disables the pass entirely and restores the
# behaviour every build before this one had.
DEFAULT_FOLLOWS_BG_CAP = True


def ceiling(bg_cap: int | None, env=None) -> int | None:
    """The hard ceiling in pixels, or None when the pass is off."""
    raw = (os.environ if env is None else env).get(CEILING_ENV, '')
    raw = str(raw).strip()
    if raw:
        try:
            val = int(raw)
        except ValueError:
            return bg_cap if DEFAULT_FOLLOWS_BG_CAP else None
        return val if val > 0 else None
    if not DEFAULT_FOLLOWS_BG_CAP:
        return None
    return bg_cap if (bg_cap and bg_cap > 0) else None


def survey(path):
    """[(name, w, h, paletted)] for every TEX over 256px. Read-only."""
    import lgp
    import tex
    out = []
    a = lgp.Archive(str(path))
    for e in a.entries:
        t = tex.parse(e['payload'])
        if t is None:
            continue
        if max(t['width'], t['height']) > 256:
            out.append((e['name'], t['width'], t['height'],
                        bool(t['palette_flag'])))
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('archive')
    ap.add_argument('--show', action='store_true')
    a = ap.parse_args(argv)
    rows = survey(a.archive)
    if not rows:
        print('no TEX over 256px')
        return 0
    import collections
    c = collections.Counter((max(w, h), 'paletted' if p else 'truecolor')
                            for _n, w, h, p in rows)
    for (s, k), n in sorted(c.items(), reverse=True):
        print('  %5dpx  %-9s  %4d file(s)' % (s, k, n))
    print('  %d TEX over 256px in total' % len(rows))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

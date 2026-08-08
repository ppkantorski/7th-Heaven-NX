#!/usr/bin/env python3
"""
try_marginpal.py -- run the real margin passes over ONE field, off the mod's
own chunk.9 and the real .iro, with ff7nx_marginpal ON and OFF, and render the
two side by side. No build, no console.

    python3 try_marginpal.py md8_1 [-o out.png]

This is the §1.2 offline loop applied to the palette question: if the change
is supposed to move a pixel it has to move here first.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_marginart as MA      # noqa: E402
import ff7nx_marginpage as MPG    # noqa: E402
import ff7nx_marginpal as MP      # noqa: E402
import field_bg_repack as R       # noqa: E402
import lgp                        # noqa: E402
import render_field as RF         # noqa: E402

MOD9 = 'cache/CosmosLimitBreak/LIMIT BREAK/flevel.lgp'
IRO = 'mods/CosmosLimitBreak.iro'
VAN = 'dump/romfs/ff7/workingdir/data/field/flevel.lgp'


def splice(name, V):
    """The vanilla field with COSMOS's section 9 -- what the build feeds the
    margin passes."""
    raw = V.decompressed(V.index[name])
    parts = lgp.split_sections(raw)
    parts[8] = open(os.path.join(MOD9, name + '.chunk.9'), 'rb').read()
    return lgp.join_sections(parts)


def run(name, raw, art, on, scope='margin'):
    MP.ENABLED = on
    os.environ.pop('FF7NX_MARGIN_PAL', None)
    new, st = MA.fill_field(name, raw, lgp, art, scope=scope)
    return (new if new is not None else raw), st


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('field')
    ap.add_argument('-o', '--out', default='marginpal_ab.png')
    ap.add_argument('--scope', default='margin')
    ap.add_argument('--split', action='store_true',
                    help='also run ff7nx_marginpage after the fill')
    a = ap.parse_args(argv)

    V = lgp.Archive(VAN)
    raw = splice(a.field, V)
    prov = R.ArtProvider([(IRO, None)], 1024, print)
    art = MA.provider_source(prov)

    outs = []
    for on in (False, True):
        new, st = run(a.field, raw, art, on, a.scope)
        if a.split:
            parts = lgp.split_sections(new)
            parts[8], mst = MPG.split_section9(parts[8])
            new = lgp.join_sections(parts)
        tag = 'marginpal %s' % ('ON' if on else 'OFF')
        keys = ('cells', 'filled', 'no_dds', 'black', 'wild', 'borrowed',
                'darkened', 'pal_tiles', 'pal_remapped')
        print('%-16s %s' % (tag, {k: st[k] for k in keys if k in st}))
        p = st.get('pal')
        if p and p.get('slots_repointed'):
            print('%-16s slots %d/%d repointed  %s  err %.2f -> %.2f  '
                  'idx %.1f -> %.1f'
                  % ('', p['slots_repointed'], p['slots'],
                     dict(p['chosen']), float(np.mean(p['err_before'])),
                     float(np.mean(p['err_after'])),
                     float(np.mean(p['idx_before'])),
                     float(np.mean(p['idx_after']))))
        img, _o = RF.render(new, (1, 2))
        outs.append((tag, img))

    d = np.abs(outs[0][1].astype(np.int32) - outs[1][1].astype(np.int32))
    outs.append(('diff  mean %.1f  max %d' % (d.mean(), d.max()),
                 np.clip(d * 3, 0, 255).astype(np.uint8)))

    from PIL import Image, ImageDraw
    PAD, LBL = 8, 16
    W = sum(i.shape[1] for _t, i in outs) + PAD * (len(outs) + 1)
    H = max(i.shape[0] for _t, i in outs) + LBL + PAD * 2
    img = Image.new('RGB', (W, H), (20, 20, 24))
    dr = ImageDraw.Draw(img)
    x = PAD
    for t, i in outs:
        dr.text((x, PAD), t, fill=(230, 230, 235))
        img.paste(Image.fromarray(i), (x, PAD + LBL))
        x += i.shape[1] + PAD
    img.save(a.out)
    print('  wrote', a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())

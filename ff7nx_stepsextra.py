#!/usr/bin/env python3
"""ff7nx_stepsextra.py -- field footstep routes Cosmo Memory does not have,
written in Cosmo's own config grammar and vocabulary.

BUILD 618s. del3 (Costa del Sol beach), hardware 10-03: "the water is not
using the water walking sounds when I run on it ... I hear sand sounds".

MEASURED: not a probe artefact and not a mapping bug. Cosmo Memory's
base/sfx/config.toml gives del3 ONE field-wide route,
    [del3_159]  sequential = [5110 .. 5115]
and no triangle overrides, so FFNx with Cosmo plays the same sand steps on
the sea. 5110-5115 is Cosmo's world-map DESERT set (walkmap type 8); its
world-map RIVER CROSSING set (walkmap type 4, wading) is 5130-5135, which
Cosmo also uses for whole water fields (qa_159).

ADDED: del3's sea triangles route to 5130-5135. The walkmesh's triangles
259-332 are the sea block (Square authored them as one contiguous group);
projected through the field camera, every one whose centre lies at or below
background y = +16 (the reach of the surf over the wet sand) is listed in
WATER. The field fallback stays Cosmo's sand. These routes are appended
AFTER the mod's configs, so they only fill gaps: a later Cosmo version that
maps del3's triangles itself would be replaced by ours only for these exact
triangles, and SEVENTH_NX_NO_STEPS_EXTRA=1 removes them.
"""
from __future__ import annotations

import os

OFF_ENV = 'SEVENTH_NX_NO_STEPS_EXTRA'
WADE = (5130, 5131, 5132, 5133, 5134, 5135)
WATER = {
    'del3': tuple(list(range(259, 281)) + list(range(283, 302))
                  + list(range(303, 333))),
}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def config_text(oggs=None):
    """The extra routes as one config text, or '' when off or when the wade
    set is not in the active sound pool."""
    if disabled():
        return ''
    if oggs is not None:
        have = {str(k) for k in (oggs.keys() if hasattr(oggs, 'keys')
                                 else oggs)}
        if not all(str(v) in have or any(str(h).endswith('%d.ogg' % v)
                                         or str(h) == '%d' % v for h in have)
                   for v in WADE):
            return ''
    seq = ', '.join(str(v) for v in WADE)
    out = ['# 7th Heaven NX additions (ff7nx_stepsextra, BUILD 618s)']
    for field, tris in WATER.items():
        for t in tris:
            out.append('[%s_%d_159]' % (field, t))
            out.append('sequential = [ %s ]' % seq)
            out.append('')
    return '\n'.join(out)


def summary():
    return '  FIELD STEPS EXTRA (BUILD 618s): %s -> wading set %d-%d. %s=1 ' \
           'disables.' % ('; '.join('%s %d sea triangles' % (f, len(t))
                                    for f, t in WATER.items()),
                          WADE[0], WADE[-1], OFF_ENV)

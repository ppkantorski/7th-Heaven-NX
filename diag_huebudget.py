#!/usr/bin/env python3
"""
diag_huebudget.py -- what a hue-aware margin fix would COST, before writing it.

FINDINGS-149 proved mds5_5's flat-olive sky is not a bad threshold but a page
that holds sky AND ground, where no single palette serves both. Three fixes
are available and they spend different budgets:

  SPLIT      move the conflicted cells to another page with a palette that
             fits.  Costs PAGES, and pages are what the no-growth loop pays
             for by dropping truecolor promotions elsewhere.
  TRUECOLOR  promote the conflicted cells.  Costs TRUECOLOR SLOTS, which are
             capped per field, but is what FFNx effectively does -- it never
             applies a palette at all, which is why it has none of these
             defects (FINDINGS-141).
  NEITHER    some cells are ORPHANED: no palette in the field is within reach
             of their hue.  A split cannot help those.  Only truecolor can.

This counts all three on the same population so the choice is made on
measurement rather than on preference.

    python3 diag_huebudget.py [--fields N] [--seed N] [--names a b c]
"""
from __future__ import annotations

import argparse
import collections
import os
import random
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp                                                     # noqa: E402
import diag_common as DC                                       # noqa: E402
import ff7nx_marginblack as MB                                 # noqa: E402
import ff7nx_marginart as MA                                   # noqa: E402
import ff7nx_marginpal as MP                                   # noqa: E402
import preflight_marginart as PF                               # noqa: E402
import field_bg_repack as R                                    # noqa: E402

# A cell is SERVED by a palette when its art's chromaticity is this close.
# Same units and calibration as LAYER1_MAX_HUE_GAP (FINDINGS-148): the two
# known-answer cases sit at 0.000 and 0.048.
SERVED = 0.030
# Beyond this, no palette is even approximately right. mds5_5's sky sits at
# 0.244..0.305 from the palette it was given.
ORPHAN = 0.100


def field_names(arch):
    return [e['name'] for e in arch.entries
            if '.' not in e['name'] and arch.is_field(e)]


def analyse(name, arch, ent, src):
    """(stats dict) or None -- one field's hue budget."""
    raw = PF._with_mod_section9(arch.decompressed(ent[name]), name)
    secs = lgp.split_sections(raw)
    cols, _, npg, _ = MB.palette_colours(secs[3])
    if npg < 1:
        return None
    prgbs = [MA.palette_rgb(cols[p]) for p in range(npg)]
    pch = {}
    for p in range(npg):
        c = MP.pal_chroma(prgbs[p])
        if c is not None:
            pch[p] = c
    if not pch:
        return None
    sec9 = secs[8]
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    cache = {}

    def art(slot):
        if slot not in cache:
            try:
                g = src(name, slot, 0)
            except Exception:                                  # noqa: BLE001
                g = None
            cache[slot] = g[0] if g else None
        return cache[slot]

    # per page: {cell -> best palette by hue, and its distance}
    per_page = collections.defaultdict(list)
    for t in MB.read_tiles(sec9, surv, pages):
        if t.layer != 1 or not t.outside_43:
            continue
        pg = pages.get(t.slot)
        if pg is None or pg.depth != 1:
            continue
        a = art(t.slot)
        if a is None:
            continue
        f = a.shape[0] // 256
        if f < 1:
            continue
        b = a[t.sy * f:(t.sy + 16) * f, t.sx * f:(t.sx + 16) * f, :3]
        b = b.reshape(-1, 3).astype(float)
        b = b[b.max(1) > 24]
        if not b.size:
            continue
        c = MP._chromaticity(b.mean(0))
        d = {p: float(np.linalg.norm(c - v)) for p, v in pch.items()}
        bp = min(d, key=d.get)
        per_page[t.slot].append((t.sx, t.sy, bp, d[bp]))

    st = dict(cells=0, orphan=0, conflicted=0, pages=0, split_pages=0,
              clusters=0)
    for slot, cells in per_page.items():
        if not cells:
            continue
        st['pages'] += 1
        st['cells'] += len(cells)
        st['orphan'] += sum(1 for _x, _y, _p, dd in cells if dd > ORPHAN)
        # Which palette would a single-palette page pick? The one that serves
        # the most cells -- a generous reading of what choose() can achieve.
        vote = collections.Counter(p for _x, _y, p, dd in cells
                                   if dd <= SERVED)
        if not vote:
            continue
        win = vote.most_common(1)[0][0]
        hurt = [1 for _x, _y, p, dd in cells
                if dd <= SERVED and p != win]
        st['conflicted'] += len(hurt)
        # Distinct palettes actually needed by cells this page cannot serve.
        need = {p for _x, _y, p, dd in cells if dd <= SERVED and p != win}
        if need:
            st['split_pages'] += 1
            st['clusters'] += len(need)
    return st


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--fields', type=int, default=45)
    ap.add_argument('--seed', type=int, default=11)
    ap.add_argument('--names', nargs='*', default=None)
    a = ap.parse_args(argv)

    arch = lgp.Archive(PF.DUMP)
    ent = {e['name']: e for e in arch.entries}
    src = MA.provider_source(
        R.ArtProvider([('mods/CosmosLimitBreak.iro', None)], 512))

    names = a.names
    if not names:
        pool = [n for n in field_names(arch)
                if os.path.exists(os.path.join(PF.MOD_CHUNKS,
                                               '%s.chunk.9' % n))]
        random.seed(a.seed)
        names = sorted(random.sample(pool, min(a.fields, len(pool))))
        scale = len(pool) / float(len(names))
    else:
        scale = 1.0

    tot = collections.Counter()
    done = 0
    worst = []
    for n in names:
        try:
            st = analyse(n, arch, ent, src)
        except Exception as exc:                               # noqa: BLE001
            print('  ! %s: %s: %s' % (n, type(exc).__name__, str(exc)[:60]))
            continue
        if not st:
            continue
        done += 1
        for k, v in st.items():
            tot[k] += v
        if st['conflicted']:
            worst.append((st['conflicted'], st['orphan'], n))

    print('HUE BUDGET over %d field(s)%s' % (
        done, '' if a.names else ' sampled (x%.1f for the archive)' % scale))
    print()
    c = tot['cells'] or 1
    print('  margin layer-1 cells measured        %8d' % tot['cells'])
    print('  CONFLICTED (a palette in the field   %8d  (%4.1f%%)'
          % (tot['conflicted'], 100.0 * tot['conflicted'] / c))
    print('    would serve them, but their page')
    print('    was given a different one)')
    print('  ORPHANED (NO palette is within %.2f  %8d  (%4.1f%%)'
          % (ORPHAN, tot['orphan'], 100.0 * tot['orphan'] / c))
    print('    -- a split cannot help these)')
    print()
    print('  pages carrying margin art            %8d' % tot['pages'])
    print('  pages that would need SPLITTING      %8d  (%4.1f%%)'
          % (tot['split_pages'], 100.0 * tot['split_pages']
             / max(1, tot['pages'])))
    print('  EXTRA PAGES a hue split would add    %8d' % tot['clusters'])
    if not a.names:
        print('  -> archive estimate                  %8d extra page(s)'
              % int(round(tot['clusters'] * scale)))
        print('     against a 16-page ceiling, 588 field(s) already over the')
        print('     mod\'s count and 187 over with the repack fully disabled.')
    print()
    print('  worst fields by conflicted cells (conflicted / orphaned):')
    for cf, orp, n in sorted(worst, reverse=True)[:10]:
        print('    %-10s %5d / %5d' % (n, cf, orp))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

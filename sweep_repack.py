#!/usr/bin/env python3
"""
sweep_repack.py -- run EVERY field through the real pipeline and count the
fields that raise.

WHY THIS EXISTS
===============
`build.py` catches `dense_repack` failures per field and logs

    ! field background: <name> not repacked -- <exception>

then carries on. The field keeps its paletted pages and loses its ENTIRE
truecolor promotion. Nothing crashes, the build completes, and the only
evidence is one warning line among hundreds.

Build 63 shipped `PROMOTE_L2_KEY = True`, which admitted keyed layer-2 cells
into the candidate list for the first time. Some carry a palette byte past the
end of the field's palette table -- `source_cell` clamps for exactly that and
says so at length; `black_fraction` did not, and had simply never been reached
by such a cell. 29+ fields raised IndexError and silently lost everything.

MY OFFLINE CHECK WAS A 20-FIELD RANDOM SAMPLE AND IT MISSED EVERY ONE. That is
the gap this closes: the archive is 709 fields, the sample was 3% of it, and
the failure was concentrated in fields with many palettes.

    python3 sweep_repack.py [--jobs N] [--out sweep.json]

Exit status is non-zero if ANY field raises, so it can gate a build.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# The build sets this from `field_bg_max_pages`; without it the module falls
# back to a hardcoded 12 and every measurement is against the wrong ceiling.
os.environ.setdefault('SEVENTH_NX_FIELD_BG_MAX_TOTAL_PAGES', '16')


def _one(name):
    """(name, ok, detail, tc_tiles, pal_tiles, pages) for one field."""
    import lgp
    import diag_common as DC
    import ff7nx_marginblack as MBk
    import ff7nx_marginart as MA
    import ff7nx_marginpage as MPG
    import field_bg_dense as FD
    import preflight_marginart as PF
    import field_bg_repack as R

    g = globals()
    if 'ARCH' not in g:
        g['ARCH'] = lgp.Archive(PF.DUMP)
        g['ENT'] = {e['name']: e for e in g['ARCH'].entries}
        g['PROV'] = R.ArtProvider([('mods/CosmosLimitBreak.iro', None)], 512)
        g['ART'] = MA.provider_source(g['PROV'])
        g['SCOPE'] = PF.build_scope()
    arch, ent = g['ARCH'], g['ENT']
    prov, art, scope = g['PROV'], g['ART'], g['SCOPE']
    try:
        raw = PF._with_mod_section9(arch.decompressed(ent[name]), name)
        new, _ = MA.fill_field(name, raw, lgp, art, scope=scope)
        if new is None:
            return (name, True, 'marginart declined', 0, 0, 0)
        MPG.ORIGIN.pop(name, None)
        s9, st = MPG.split_section9(lgp.split_sections(new)[8], field=name)
        MPG.ORIGIN[name] = st.get('origin') or {}
        out, _d = FD.dense_repack(lgp.split_sections(raw)[3], s9, name,
                                  prov.open(name), prov.palettes, 512,
                                  max_tc=3)
    except Exception:                                          # noqa: BLE001
        return (name, False, traceback.format_exc().strip().splitlines()[-1],
                0, 0, 0)
    surv = DC.survey(out)
    pages = {p.slot: p for p in surv['pages']}
    tc = pal = 0
    for t in MBk.read_tiles(out, surv, pages):
        p = pages.get(t.slot)
        if p is None:
            continue
        if p.depth == 2:
            tc += 1
        else:
            pal += 1
    return (name, True, '', tc, pal, len(pages))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument('--out', default=os.path.join(_HERE, 'sweep.json'))
    ap.add_argument('--start', type=int, default=0)
    ap.add_argument('--count', type=int, default=0)
    a = ap.parse_args(argv)

    import lgp
    import preflight_marginart as PF
    arch = lgp.Archive(PF.DUMP)
    names = [e['name'] for e in arch.entries
             if '.' not in e['name'] and arch.is_field(e)]

    if a.count:
        names = names[a.start:a.start + a.count]
    rows = []
    if a.jobs > 1:
        import multiprocessing as mp
        with mp.Pool(a.jobs) as pool:
            for i, r in enumerate(pool.imap_unordered(_one, names, 4)):
                rows.append(r)
                if (i + 1) % 50 == 0:
                    print('  ... %d/%d' % (i + 1, len(names)), flush=True)
    else:
        for i, n in enumerate(names):
            rows.append(_one(n))

    bad = [r for r in rows if not r[1]]
    tc = sum(r[3] for r in rows)
    pal = sum(r[4] for r in rows)
    prev = []
    if os.path.exists(a.out) and a.start:
        try:
            prev = json.load(open(a.out)).get('rows') or []
        except Exception:                                      # noqa: BLE001
            prev = []
    allrows = prev + rows
    with open(a.out, 'w') as fh:
        json.dump({'rows': allrows, 'failed':
                   len([r for r in allrows if not r[1]])}, fh, indent=1)

    print('\nSWEEP: %d field(s)' % len(rows))
    print('  RAISED (would log "not repacked" and lose ALL truecolor): %d'
          % len(bad))
    for n, _ok, why, _a, _b, _c in sorted(bad)[:25]:
        print('    !! %-12s %s' % (n, why[:90]))
    print('  truecolor tiles %d / %d  = %.1f%%'
          % (tc, tc + pal, 100.0 * tc / max(1, tc + pal)))
    print('  wrote %s' % a.out)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())

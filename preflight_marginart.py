#!/usr/bin/env python3
"""
preflight_marginart.py -- run a staged `ff7nx_marginart` change BOTH WAYS on
the real archive with the real mod art, and report what it does to the
PIXELS, before anyone builds.

    python3 preflight_marginart.py --flag KEEP0_CUTOUTS_ONLY \\
        --fields md8_1 mkt_mens mds6_3 nmkin_1 --render

WHY THIS EXISTS
===============
Build 51 shipped a change I had "verified" against counters and a two-field
sample. It was wrong by 180x and it put black outlines around every overlay
in the game. The specific failures, all of which this catches:

  1. BLAST RADIUS FROM A SAMPLE. I measured 2 fields, got ~21,000 texels,
     and reported that. The real number was 3,808,905 across 145,422 cells.
     `--fields all` measures the archive.

  2. ALPHA USED AS A PROXY FOR "THE MOD SHIPS ART". Cosmos's DDS have alpha
     255 across the WHOLE page -- `md8_1_00_00` has zero transparent texels
     -- so `cover > 0` is constant and carries no information. The art was
     black. This reports the RGB it is about to write, not the alpha.

  3. NO PICTURE. Every visual regression in this project was found by the
     user on hardware, 30 minutes at a time. `render_field.py --against`
     has been in the tree the whole time.

WHAT IT DOES NOT COVER
======================
`fill_field` is one pass. Downstream passes (dense repack, page cap,
transparency key) run after it and build 51 moved all three, so a clean
preflight here does NOT license a claim that nothing else moves. It answers
"what does this pass write", not "what does the build produce".
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp
import iro
import field_bg_repack
import ff7nx_fieldbg
import ff7nx_marginart as MA

DUMP = os.path.join(_HERE, 'game_data_files', 'field', 'flevel.lgp')
MOD = os.path.join(_HERE, 'mods', 'CosmosLimitBreak.iro')


def provider(px=512):
    """
    The ArtProvider, over every folder in the .iro.

    `allowed=None` deliberately. The build narrows this to the option folders
    the user has switched on; passing an EMPTY set instead of None silently
    matches nothing, the provider returns no art for any page, `fill_field`
    changes nothing, and the preflight prints a clean sheet of zeros. That is
    exactly how this script lied the first time it was run -- a harness whose
    failure mode is "no difference found" is worse than no harness. Asserted
    against below so it cannot happen quietly again.
    """
    return field_bg_repack.ArtProvider([(MOD, None)], px)


def build_scope():
    """
    The scope THE BUILD uses, read from settings.json.

    `MA.scope()` resolves a value the build injects, so calling it from a
    standalone script returns 'margin' -- and on the raw dump there are no
    margin tiles yet (they are created by the widescreen passes that run
    BEFORE this one), so every field reports 0 cells and the preflight prints
    zeros. That is the second way this script lied on its first run.

    settings.json `margin_art: 2` is what the build is actually configured
    with, and the log confirms it: "margin art scope: MARGIN + INTERIOR".
    """
    import json
    try:
        with open(os.path.join(_HERE, 'settings.json')) as fh:
            v = json.load(fh).get('__global__', {}).get('margin_art')
    except Exception:                                          # noqa: BLE001
        v = None
    return 'all' if str(v) in ('2', 'all', 'interior', 'full') else 'margin'


def _assert_art_reachable(px=512):
    """Fail loudly if the provider resolves nothing. See `provider`."""
    src = MA.provider_source(provider(px))
    for f, slot in (('md8_1', 0), ('mkt_mens', 0)):
        got = src(f, slot, 0)
        if got is None:
            raise SystemExit(
                'PREFLIGHT ABORTED: the art provider resolved nothing for '
                '%s slot %d. Every result would read as "no change" and be '
                'meaningless. Check the .iro path and the folder filter.'
                % (f, slot))
    return True


MOD_CHUNKS = os.path.join(_HERE, 'cache', 'CosmosLimitBreak', 'LIMIT BREAK',
                          'flevel.lgp')


def _with_mod_section9(raw, name):
    """
    Vanilla field with Cosmos's section 9 spliced in, as the BUILD does it.

    THE HARNESS WAS TESTING THE WRONG BYTES AND REPORTED A CLEAN ZERO.
    `DUMP` is Switch vanilla, where `mds6_3`'s layer 1 stops at dx 112 and
    there is no 16:9 margin at ALL -- so `placeholder` came back empty, every
    margin pass had nothing to decide, and a preflight of a margin change
    printed "0 changed" for reasons that had nothing to do with the change.
    FINDINGS-141 section 5 wrote this obstacle down ("the margin tiles do not
    exist in the dump or in flevel.wide.lgp") and it still caught me.

    `build.py` splices section 9 from the mod (SAFE_MOD_SECTIONS, ~line 4515)
    and that is where the margin comes from. Anything testing a margin pass
    has to start from the same bytes the pass sees in the build.
    """
    path = os.path.join(MOD_CHUNKS, '%s.chunk.9' % name)
    if not os.path.exists(path):
        return raw
    try:
        secs = lgp.split_sections(raw)
        with open(path, 'rb') as fh:
            secs[8] = fh.read()
        return lgp.join_sections(secs)
    except Exception:                                          # noqa: BLE001
        return raw


def _owner(flag):
    """
    The module that defines `flag` -- marginart, else marginpal.

    `choose()` lives in `ff7nx_marginpal` and runs INSIDE `fill_field`, so a
    marginpal threshold is just as preflightable as a marginart flag; this
    harness simply could not reach one. FINDINGS-148's gate is a marginpal
    constant, and a harness that cannot test the change it was built for is
    the same class of gap as build 54's untested summarise().
    """
    import ff7nx_marginpal as MP
    for mod in (MA, MP):
        if hasattr(mod, flag):
            return mod
    return None


def run(field_names, flag, values, px=512):
    """
    {field: {value: (new_raw, stats)}} -- `fill_field` run once per value of
    `flag`, on the same input, with the same art.
    """
    arch = lgp.Archive(DUMP)
    ent = {e['name']: e for e in arch.entries}
    art = MA.provider_source(provider(px))
    scope = build_scope()
    out = {}
    for name in field_names:
        e = ent.get(name)
        if e is None or not arch.is_field(e):
            print('  ! %s: not a field in the dump' % name)
            continue
        raw = _with_mod_section9(arch.decompressed(e), name)
        out[name] = {}
        for v in values:
            setattr(_owner(flag) or MA, flag, v)
            try:
                new, st = MA.fill_field(name, raw, lgp, art, scope=scope)
            except Exception as exc:                           # noqa: BLE001
                print('  ! %s @ %s=%r: %s: %s'
                      % (name, flag, v, type(exc).__name__, exc))
                new, st = None, {}
            out[name][v] = (new if new is not None else raw, st)
    return out


def compare(res, flag, values):
    """Pixel-level diff between the two runs, per field and in total."""
    import diag_common as DC
    import ff7nx_marginblack as MB
    a_v, b_v = values
    tot = dict(cells=0, changed=0, darker=0, to_black=0, lit=0)
    per_field = {}
    print()
    print('%-10s %10s %10s %10s %10s %10s'
          % ('field', 'idx px', 'changed', 'darker', '-> BLACK', 'brighter'))
    for name, byval in sorted(res.items()):
        ra, rb = byval[a_v][0], byval[b_v][0]
        pa = lgp.split_sections(ra)
        pb = lgp.split_sections(rb)
        try:
            cols, _h, npg, _c = MB.palette_colours(pa[MA.SECTION_PALETTE])
            sa = DC.survey(pa[MA.SECTION9])
            sb = DC.survey(pb[MA.SECTION9])
        except Exception as exc:                               # noqa: BLE001
            print('  ! %s: %s' % (name, exc))
            continue
        pgA = {p.slot: p for p in sa['pages'] if p.depth == 1}
        pgB = {p.slot: p for p in sb['pages'] if p.depth == 1}
        n = ch = dk = bl = lt = 0
        for slot, PA in pgA.items():
            PB = pgB.get(slot)
            if PB is None or len(PA.data) != len(PB.data):
                continue
            A = np.frombuffer(PA.data, np.uint8)
            B = np.frombuffer(PB.data, np.uint8)
            d = A != B
            n += A.size
            ch += int(d.sum())
            if not d.any():
                continue
            # what the CHANGED indices actually render as, through palette 0
            # (the page's own palette is per tile; palette 0 is the common
            # case and this is a magnitude check, not a render)
            pal = cols[0] if npg else None
            if pal is None:
                continue
            rgb = np.stack([(pal & 31), (pal >> 5) & 31,
                            (pal >> 10) & 31], -1).astype(int) * 255 // 31
            la = rgb[A[d]].max(1)
            lb = rgb[B[d]].max(1)
            dk += int((lb < la).sum())
            lt += int((lb > la).sum())
            bl += int((lb <= 24).sum() - (la <= 24).sum()
                      if (lb <= 24).sum() > (la <= 24).sum() else 0)
        print('%-10s %10d %10d %10d %10d %10d' % (name, n, ch, dk, bl, lt))
        per_field[name] = dict(changed=ch, darker=dk, to_black=bl, lit=lt)
        tot['cells'] += n
        tot['changed'] += ch
        tot['darker'] += dk
        tot['to_black'] += bl
        tot['lit'] += lt
    print('%-10s %10d %10d %10d %10d %10d'
          % ('TOTAL', tot['cells'], tot['changed'], tot['darker'],
             tot['to_black'], tot['lit']))
    _filled = sum(b[values[0]][1].get('filled', 0) for b in res.values())
    if not _filled:
        raise SystemExit(
            '\nPREFLIGHT ABORTED: the pass wrote 0 cells in every field, so '
            'the comparison above is meaningless. Scope or art resolution is '
            'wrong -- NOT evidence that the change is safe.')
    print()
    # THE VERDICT IS PER FIELD, NOT ON THE TOTAL.
    #
    # The total for build 51 was 17.3% new-black, under any threshold I would
    # have set. `md8_1` on its own was 42% new-black and 100% darker, and
    # md8_1 is a field the user looks at every time. Averaging a localised
    # catastrophe against five quiet fields is how it got shipped.
    bad = []
    for name, byval in sorted(res.items()):
        st = per_field.get(name)
        if not st or not st['changed']:
            continue
        dpc = 100.0 * st['darker'] / st['changed']
        bpc = 100.0 * st['to_black'] / st['changed']
        if dpc >= 60.0 or bpc >= 25.0:
            bad.append((name, st['changed'], dpc, bpc))
    if tot['changed']:
        print('  %.1f%% of changed texels got DARKER; %d newly render at or '
              'below 24/255.' % (100.0 * tot['darker'] / tot['changed'],
                                 tot['to_black']))
    if bad:
        print()
        print('  !! DO NOT BUILD. %d field(s) are mostly being DARKENED --'
              ' the build-45 / 49 / 51 signature:' % len(bad))
        for name, ch, dpc, bpc in bad:
            print('  !!   %-10s %7d texels changed, %5.1f%% darker, '
                  '%5.1f%% now at or below 24/255' % (name, ch, dpc, bpc))
    elif tot['changed']:
        print('  no field is predominantly darkened.')
    return tot


def write_pair(res, flag, values, outdir):
    """Two archives differing only in `flag`, for render_field --against."""
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for v in values:
        arch = lgp.Archive(DUMP)
        payloads = {}
        for name, byval in res.items():
            raw = byval[v][0]
            payloads[name] = arch.encode_field(raw)
        arch.replace(payloads)
        p = os.path.join(outdir, 'flevel.%s=%s.lgp' % (flag, v))
        arch.write(p)
        paths.append(p)
        print('  wrote %s' % p)
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--flag', default='KEEP0_CUTOUTS_ONLY')
    ap.add_argument('--values', default='False,True')
    ap.add_argument('--fields', nargs='+',
                    default=['md8_1', 'mkt_mens', 'mds6_3', 'nmkin_1'])
    ap.add_argument('--render', action='store_true',
                    help='also write two archives for render_field --against')
    ap.add_argument('--outdir', default=os.path.join(_HERE, '_preflight'))
    a = ap.parse_args(argv)

    # NOT BOOL-ONLY. `--values False,True` still works, but a threshold is a
    # number and the old parser turned every number into False, so a sweep
    # would have silently compared a flag against itself.
    import ast as _ast

    def _val(s):
        try:
            return _ast.literal_eval(s.strip())
        except Exception:                                      # noqa: BLE001
            return s.strip() == 'True'

    vals = [_val(v) for v in a.values.split(',')]
    mod = _owner(a.flag)
    if mod is None:
        print('no such flag on marginart or marginpal: %s' % a.flag)
        return 2
    if vals[0] == vals[1]:
        print('!! both values are %r -- this compares the flag against '
              'itself and will always report "identical".' % (vals[0],))
        return 2
    keep = getattr(mod, a.flag)
    print('PREFLIGHT  %s: %r -> %r   on %d field(s)'
          % (a.flag, vals[0], vals[1], len(a.fields)))
    _assert_art_reachable()
    try:
        res = run(a.fields, a.flag, vals)
        compare(res, a.flag, vals)
        if a.render:
            write_pair(res, a.flag, vals, a.outdir)
            print('\n  render with:\n    python3 render_field.py %s '
                  '--against %s %s -o cmp.png'
                  % (os.path.join(a.outdir, 'flevel.%s=%s.lgp'
                                  % (a.flag, vals[0])),
                     os.path.join(a.outdir, 'flevel.%s=%s.lgp'
                                  % (a.flag, vals[1])),
                     ' '.join(a.fields)))
    finally:
        setattr(mod, a.flag, keep)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

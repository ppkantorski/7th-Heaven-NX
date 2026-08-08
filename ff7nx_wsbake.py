#!/usr/bin/env python3
"""
ff7nx_wsbake.py -- write the widescreen config into flevel.lgp's own data.

    python3 ff7nx_wsbake.py --mod cache/CosmosLimitBreak -o flevel.wide.lgp
    python3 ff7nx_wsbake.py --mod cache/CosmosLimitBreak --dry-run

WHY
===
FFNx keeps the per-field widescreen camera range in a runtime object fed by
`config.toml`, and `field_clip_with_camera_range_float` reads THAT, not the
field's own trigger header:

    auto camera_range = field_triggers_header_ptr->camera_range;
    if (widescreen_enabled || enable_uncrop)
        camera_range = widescreen.getCameraRange();      // <- the override
    half_width = 160 + std::min(53, cameraRangeSize / 2 - 160);

We cannot build that object. A per-field lookup table in `main` would need
711 entries in a binary with about 31 KB of usable cave space, and the code
to walk it, and a way to know which field is loaded.

We do not have to. The packer already rewrites `flevel.lgp` -- that is how
Cosmos Limit Break's 683 section-9 background chunks get in. Writing the
overridden range into each field's **section 8** makes
`field_triggers_header->camera_range` correct at runtime by construction.
The code cave then only performs the `160 + min(53, ...)` arithmetic on data
the game already holds. No table, no lookup, no cave space, and the whole
data half becomes verifiable offline.

WHAT IT TOUCHES
===============
Four `int16` at offset +0x0C of section 8's body: left, bottom, right, top.
Section length does not change, so no other section moves and the field
header does not need rewriting. Every other byte of every field is passed
through untouched, and `--verify` reads the result back out of the rebuilt
archive and compares it against the plan.

Fields the config does not change are NOT re-encoded. Re-encoding costs a
full LZSS pass each and most fields do not need one.
"""
import argparse
import os
import shutil
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_wsdata as W                                     # noqa: E402

try:
    import lgp                                               # the packer's
except ImportError:                                          # pragma: no cover
    lgp = None

SECTION8_INDEX = 7          # section 8 of 9, zero-based


def ranges_from_archive(archive):
    """
    {field: range} read out of a `lgp.Archive`.

    Decompresses only as far as section 8. `archive.decompressed()` unpacks
    the WHOLE field, and section 9 -- the background -- is most of it: 35
    seconds for the archive, against under two for the same answer. Reading
    the ranges happens twice per bake (before and after, to verify), so the
    difference is the whole runtime of the tool.
    """
    out = {}
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            payload = entry['payload']          # 4-byte length + LZS stream
            head = W._lzss_head(payload, 42)
            starts = W.struct.unpack('<9I', head[6:42])
            body = starts[SECTION8_INDEX] + 4   # skip the section length
            data = W._lzss_head(payload, body + W.SECTION8_MIN_LEN)
            out[name] = W.read_section8_range(data[body:body + 64])
        except Exception:                                    # noqa: BLE001
            continue
    return out


def bake(src, dest, config, movie_config=None, log=print, encode=None,
         dry_run=False, store=False):
    """
    Rewrite `src` to `dest` with the config's camera ranges baked in.

    `encode` is the field encoder to use -- the packer passes its cached one
    (`_encode_field_cached`) so a rebuild does not pay the compressor twice.
    Returns (plan, before, after) where `after` is None for a dry run.
    """
    if lgp is None:
        raise RuntimeError('lgp.py not importable -- run this from the '
                           '7th_heaven_nx folder')
    archive = lgp.Archive(src)
    before = ranges_from_archive(archive)
    log('  read %d field camera ranges from %s'
        % (len(before), os.path.basename(src)))

    plan = W.bake_plan(before, config, movie_config)
    log('  %d field(s) have a range the config changes' % len(plan))
    if not plan:
        log('  nothing to bake')
        if not dry_run and os.path.abspath(src) != os.path.abspath(dest):
            shutil.copyfile(src, dest)
        return plan, before, before

    # Break it down, because "314 ranges change" turned out to hide the
    # thing that matters. Only a HORIZONTAL widening moves the gate; a
    # vertical change is framing, and neither is the same as an explicit
    # `mode` key, which section 8 has nowhere to put.
    widened = crossed = vertical_only = 0
    for f, r in plan.items():
        old_r = before[f]
        new_w = r['right'] - r['left']
        if new_w > old_r['width']:
            widened += 1
            if W.gate(new_w) and not W.gate(old_r['width']):
                crossed += 1
        elif new_w == old_r['width']:
            vertical_only += 1
    log('    %d widen the horizontal range' % widened)
    log('      of which %d cross the %d gate' % (crossed, W.WIDE_GATE))
    log('    %d change only the vertical pair (framing, not the gate)'
        % vertical_only)

    modes = sum(1 for v in config.values()
                if isinstance(v, dict) and 'mode' in v)
    if modes:
        log('    NOTE: %d config entries set an explicit `mode`, which is '
            'NOT expressible in section 8 -- see README-45 §8' % modes)

    if dry_run:
        for name in sorted(plan)[:8]:
            o, n = before[name], plan[name]
            log('    %-10s %d..%d (w %d)  ->  %d..%d (w %d)%s'
                % (name, o['left'], o['right'], o['width'],
                   n['left'], n['right'], n['right'] - n['left'],
                   '  GATE' if W.gate(n['right'] - n['left'])
                   and not W.gate(o['width']) else ''))
        if len(plan) > 8:
            log('    ... and %d more' % (len(plan) - 8))
        return plan, before, None

    # `store` writes literal-only LZS: identical bytes after decompression,
    # about 12% larger, and instant. The compressor is pure Python and a
    # field is most of a megabyte, so a standalone run over hundreds of
    # fields is minutes of CPU for a result that is only being VERIFIED.
    # The real build never uses this -- it re-encodes these fields anyway
    # for Cosmos's section-9 chunks, through a content-keyed cache.
    encode = encode or (lambda raw:
                        archive.encode_field(raw, compress=not store))
    payloads = {}
    t0 = time.time()
    for i, name in enumerate(sorted(plan)):
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            log('  ! no such field: %s' % name)
            continue
        parts = lgp.split_sections(archive.decompressed(entry))
        parts[SECTION8_INDEX] = W.write_section8_range(parts[SECTION8_INDEX],
                                                       plan[name])
        payloads[name] = encode(lgp.join_sections(parts))
        if len(plan) > 50 and (i + 1) % 50 == 0:
            log('    ... %d/%d fields (%.0fs elapsed)'
                % (i + 1, len(plan), time.time() - t0))
    log('  re-encoded %d field(s) in %.0fs' % (len(payloads),
                                               time.time() - t0))

    archive.replace(payloads)
    archive.write(dest)

    after = ranges_from_archive(lgp.Archive(dest))
    ok, problems = W.verify_bake(before, after, plan)
    if ok:
        log('  verified: every planned range is in the rebuilt archive and '
            'nothing else moved')
    else:
        log('  ! VERIFY FAILED (%d problem(s))' % len(problems))
        for p in problems[:10]:
            log('      ' + p)
    return plan, before, (after if ok else None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])
    ap.add_argument('flevel', nargs='?',
                    default='game_data_zips/field.zip',
                    help='flevel.lgp or field.zip (default: '
                         'game_data_zips/field.zip)')
    ap.add_argument('--mod', help='mod directory holding '
                                  'CONFIG/widescreen/config.toml')
    ap.add_argument('--config', help='config.toml directly')
    ap.add_argument('-o', '--out', help='where to write the baked archive')
    ap.add_argument('--dry-run', action='store_true',
                    help='say what would change and stop')
    ap.add_argument('--store', action='store_true',
                    help='literal-only LZS: identical data, ~12 percent '
                         'bigger, and instant -- for verifying the bake '
                         'without waiting on the pure-Python compressor')
    args = ap.parse_args(argv)

    import diag_widescreen as D
    flevel, _tmp = D.find_flevel(args.flevel)
    if not flevel:
        print('no flevel.lgp under %s' % args.flevel)
        return 2

    cfg = args.config
    if args.mod and not cfg:
        cfg, _mov, alts = W.find_configs(os.path.expanduser(args.mod))
        for a in alts:
            print('(also found, not used: %s)' % a)
    if not cfg:
        print('no config.toml -- nothing to bake. Pass --mod or --config.')
        return 2
    print('config: %s' % cfg)
    config = W.load_toml(cfg)

    dest = args.out or (os.path.splitext(flevel)[0] + '.wide.lgp')
    if args.dry_run:
        print('DRY RUN -- nothing will be written')
    plan, before, after = bake(flevel, dest, config, dry_run=args.dry_run,
                               store=args.store)

    if not args.dry_run and after:
        gated_before = sum(1 for r in before.values() if W.gate(r['width']))
        gated_after = sum(1 for r in after.values() if W.gate(r['width']))
        print()
        print('fields passing the gate WITHOUT any config, before -> after:')
        print('    %d  ->  %d   (of %d)'
              % (gated_before, gated_after, len(after)))
        print('wrote %s' % dest)
        print()
        print('check it with the config OUT of the picture:')
        print('    python3 diag_widescreen.py "%s"' % dest)
    return 0


if __name__ == '__main__':
    sys.exit(main())

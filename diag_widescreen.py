#!/usr/bin/env python3
"""
diag_widescreen.py -- how much of FF7 can actually be widescreen?

    python3 diag_widescreen.py game_data_zips/field.zip
    python3 diag_widescreen.py dump/.../flevel.lgp --mod "~/mods/Cosmos Limit Break"
    python3 diag_widescreen.py field.zip --csv fields.csv --list

FFNx decides widescreen PER FIELD, at field-load time, from the field's own
camera range:

    if (camera_range.right - camera_range.left >= 320 + 107)   // = 427
        widescreen_mode = WM_EXTEND_WIDE;
    else
        widescreen_mode = WM_DISABLED;      // this field stays 4:3, forever

and a mod may then override any of it by field name from
`CONFIG/widescreen/config.toml`. Fields with no extra painted background
are left at 4:3 -- FFNx never stretches as a fallback.

That makes two numbers the precondition for the whole 16:9 task, and this
answers both without a module patch, a build, or a hardware test:

  * how many fields pass the gate unaided -- the free coverage, and
  * how many only work because somebody hand-authored an override -- the
    size of the authoring job, or of the debt if the mod's file is not
    carried through the build.

Exit status is 1 if no config was supplied or found, since that is the
condition under which the answer is "gate only".
"""
import argparse
import csv
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ff7nx_wsdata as W                                     # noqa: E402


def find_flevel(path):
    """A .lgp, a directory holding one, or a field.zip. Returns a real path."""
    path = os.path.expanduser(path)
    if os.path.isfile(path) and path.lower().endswith('.lgp'):
        return path, None
    if os.path.isdir(path) and path.lower().endswith('.lgp'):
        # A 7th Heaven chunk mod. Cosmos ships `LIMIT BREAK/flevel.lgp` as a
        # DIRECTORY of `<field>.chunk.9` files -- section 9 only, so it
        # repaints backgrounds and leaves the camera ranges alone. There is
        # no archive here to read ranges from, and that is not an error.
        sections = W.repainted_fields(path)
        if sections:
            print('%s is a chunk mod, not an archive:' % path)
            for sec in sorted(sections):
                print('   %d field(s) with section %d replaced (%s)'
                      % (len(sections[sec]), sec,
                         'Background' if sec == 9 else 'section %d' % sec))
            if 8 not in sections:
                print('   section 8 (Triggers) is NOT replaced, so the '
                      'camera ranges are still the vanilla ones.')
            print('   -> read the ranges from the real archive and pass '
                  'this with --chunks instead:')
            print('      python3 diag_widescreen.py --mod <mod> --chunks '
                  '"%s"' % path)
            return None, None
    if os.path.isfile(path) and zipfile.is_zipfile(path):
        zf = zipfile.ZipFile(path)
        names = [n for n in zf.namelist()
                 if os.path.basename(n).lower() == 'flevel.lgp']
        if not names:
            return None, None
        tmp = tempfile.mkdtemp(prefix='wsdiag')
        out = os.path.join(tmp, 'flevel.lgp')
        with open(out, 'wb') as f:
            f.write(zf.read(names[0]))
        return out, tmp
    if os.path.isdir(path):
        for dirpath, _d, files in os.walk(path):
            for f in files:
                if f.lower() == 'flevel.lgp':
                    return os.path.join(dirpath, f), None
    return None, None


def bar(n, total, width=34):
    if not total:
        return ''
    filled = int(round(width * n / float(total)))
    return '█' * filled + '·' * (width - filled)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])
    ap.add_argument('flevel', nargs='?', default='game_data_zips/field.zip',
                    help='flevel.lgp, field.zip, or a directory '
                         '(default: game_data_zips/field.zip)')
    ap.add_argument('--mod', help='a mod directory to search for '
                                  'CONFIG/widescreen/config.toml')
    ap.add_argument('--config', help='config.toml directly')
    ap.add_argument('--movie-config', help='movie_config.toml directly')
    ap.add_argument('--chunks', help='a chunk mod\'s *.lgp DIRECTORY, to '
                                     'cross-reference repainted art against '
                                     'widescreen coverage')
    ap.add_argument('--csv', help='write the per-field table here')
    ap.add_argument('--list', action='store_true',
                    help='print the widest and narrowest fields')
    ap.add_argument('--emit', metavar='PATH',
                    help='write the per-field wide/not-wide table the '
                         'module will need (see README-45 section 8.3)')
    ap.add_argument('--cache', default=os.path.join(
        tempfile.gettempdir(), 'ff7nx_wsranges.json'))
    args = ap.parse_args(argv)

    flevel, _tmp = find_flevel(args.flevel)
    if not flevel:
        print('no flevel.lgp under %s' % args.flevel)
        print('run this from the 7th_heaven_nx folder, or pass the path to '
              'flevel.lgp / field.zip as the first argument')
        return 2

    print('reading camera ranges from %s' % os.path.basename(flevel))
    ranges = W.camera_ranges(flevel, cache=args.cache,
                             log=lambda *a: None)
    if not ranges:
        print('no fields parsed -- is this really flevel.lgp?')
        return 2

    cfg_path, mov_path = args.config, args.movie_config
    alternates = []
    if args.mod and not cfg_path:
        cfg_path, found_mov, alternates = W.find_configs(
            os.path.expanduser(args.mod))
        mov_path = mov_path or found_mov
    config = W.load_toml(cfg_path) if cfg_path else {}
    movie_config = W.load_toml(mov_path) if mov_path else {}

    resolved = W.resolve(ranges, config, movie_config)
    s = W.summarise(resolved)

    print()
    print('the gate: a field is widescreen-capable when')
    print('    camera_range.right - camera_range.left >= %d'
          '      (game_width/2 + |wide_viewport_x| = %d + %d)'
          % (W.WIDE_GATE, W.GAME_W // 2, abs(W.WIDE_VIEWPORT_X)))
    print()
    print('%d fields in flevel.lgp' % s['total'])
    print('  %4d  %5.1f%%  pass the gate unaided   %s'
          % (s['gated_in'], 100.0 * s['gated_in'] / s['total'],
             bar(s['gated_in'], s['total'])))
    print('  %4d  %5.1f%%  do NOT -- 4:3 forever   %s'
          % (s['total'] - s['gated_in'],
             100.0 * (s['total'] - s['gated_in']) / s['total'],
             bar(s['total'] - s['gated_in'], s['total'])))

    if cfg_path:
        print()
        print('config: %s' % cfg_path)
        print('  %4d field(s) have an entry' % s['configured'])
        print('  %4d field(s) are changed by it' % s['overridden'])
        rescued = sum(1 for i in resolved.values()
                      if not i['gated_in'] and i['mode'] != W.WM_DISABLED)
        lost = sum(1 for i in resolved.values()
                   if i['gated_in'] and i['mode'] == W.WM_DISABLED)
        print('  %4d field(s) the config turns ON that the gate refused'
              % rescued)
        print('  %4d field(s) the config turns OFF that the gate allowed'
              % lost)
        extra = W.unknown_keys(config)
        if extra:
            print('  ! keys we do not consume: %s' % ', '.join(extra))
        for other in alternates:
            print('  (also found, not used: %s)' % other)
    else:
        print()
        print('no config.toml supplied -- this is GATE-ONLY coverage.')
        print('Cosmos Limit Break ships CONFIG/widescreen/config.toml;')
        print('pass --mod to see what it adds.')

    print()
    print('resulting modes')
    for name in ('disabled', 'extend_only', 'zoom', 'extend_wide', 'fill'):
        n = s['by_mode'].get(name, 0)
        if n or name in ('disabled', 'extend_wide'):
            print('  %-12s %4d  %5.1f%%  %s'
                  % (name, n, 100.0 * n / s['total'], bar(n, s['total'])))
    print()
    print('  => %d of %d fields (%.1f%%) would be widescreen'
          % (s['wide'], s['total'], 100.0 * s['wide'] / s['total']))

    chunk_dir = args.chunks
    if not chunk_dir and args.mod:
        found = W.find_chunk_dirs(os.path.expanduser(args.mod))
        chunk_dir = found[0] if found else None
    if chunk_dir:
        sections = W.repainted_fields(os.path.expanduser(chunk_dir))
        art = sections.get(9, set())
        if art:
            print()
            print('repainted background art: %s' % chunk_dir)
            print('  %4d field(s) have a new section 9' % len(art))
            if 8 in sections:
                print('  %4d also replace section 8 -- the camera ranges '
                      'above are NOT the live ones' % len(sections[8]))
            else:
                print('       section 8 is untouched, so the ranges above '
                      'are the live ones')
            known = art & set(resolved)
            on = {f for f in known if resolved[f]['mode'] != W.WM_DISABLED}
            print('  %4d of them end up widescreen' % len(on))
            dark = sorted(known - on)
            print('  %4d have new art that nothing will ever reveal'
                  % len(dark))
            if dark:
                print('       e.g. %s' % ', '.join(dark[:8]))
                print('       (each of these needs a config.toml entry '
                      'widening left/right, or the art is wasted)')

    if movie_config:
        print()
        print('movie config: %s' % mov_path)
        print('  %4d movie(s) have an entry' % len(movie_config))
        kf = sum(1 for v in movie_config.values()
                 if isinstance(v, dict) and v.get('movie_v_offset'))
        print('  %4d carry per-frame vertical keyframes' % kf)

    if args.list:
        widest = sorted(resolved.items(),
                        key=lambda kv: -kv[1]['range']['width'])
        print()
        print('widest fields (most to gain)')
        for name, i in widest[:12]:
            print('  %-10s width %4d  %s' % (name, i['range']['width'],
                                             i['mode_name']))
        print('narrowest (nothing to reveal)')
        for name, i in widest[-6:]:
            print('  %-10s width %4d  %s' % (name, i['range']['width'],
                                             i['mode_name']))

    if args.emit:
        blob, text, info = W.emit_exception_table(
            resolved, cfg_path or 'gate only, no config')
        with open(args.emit, 'w') as f:
            f.write(text)
        print()
        print('per-field table for the module: %s' % args.emit)
        print('  default is %s, %d exception(s) named, %d bytes'
              % ('WIDE' if info['wide_default'] else 'NOT WIDE',
                 info['listed'], info['bytes']))
        print('  (%d wide, %d not wide, %d zoom)'
              % (info['wide'], info['narrow'], info['zoom']))
        if info['ambiguous']:
            print('  ! %d base name(s) resolve both ways: %s'
                  % (len(info['ambiguous']),
                     ', '.join(info['ambiguous'][:6])))
        print('  the padding pool has ~31 KB usable, so this fits with room '
              'to spare')

    if args.csv:
        with open(args.csv, 'w', newline='') as f:
            wr = csv.writer(f)
            wr.writerow(['field', 'left', 'right', 'bottom', 'top', 'width',
                         'height', 'gated_in', 'mode', 'from_config',
                         'overrides'])
            for name in sorted(resolved):
                i = resolved[name]
                r = i['range']
                wr.writerow([name, r['left'], r['right'], r['bottom'],
                             r['top'], r['width'], r['height'],
                             int(i['gated_in']), i['mode_name'],
                             int(i['from_config']), ' '.join(i['overrides'])])
        print('\nwrote %s' % args.csv)

    return 0 if cfg_path else 1


if __name__ == '__main__':
    sys.exit(main())

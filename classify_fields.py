#!/usr/bin/env python3
"""
classify_fields.py -- why each field is or is not widescreen, from the archives.

    python3 classify_fields.py CosmosLimitBreak.iro --flevel flevel.wide.lgp
    python3 classify_fields.py CosmosLimitBreak.iro --flevel <built> --class D

Three inputs, joined per field:

  * art span    -- x extent of the BACK layer in the MOD's own chunk.9. How
                   much background art actually exists.
  * camera range - `right - left` from section 8 of the BUILT flevel.lgp.
                   How far the camera is allowed to scroll.
  * config entry - the mod's CONFIG\\widescreen\\config.toml, and its `mode`.

16:9 needs 427 tile units (`320 + |wide_viewport_x|`, FFNx
widescreen.cpp:383).

The classification, and why a camera-range test alone is not enough: 306 of
Cosmos Limit Break's 317 config entries are `mode = 1` (extend_only), which
leaves the camera range narrow but still makes `is_fieldmap_wide()` true, so
the TILE WINDOW opens and side art is drawn without the camera scrolling
further (FFNx utils.h:49, background.cpp:128). A field can therefore be
correctly widescreen with a 320-unit camera range.

  A  no art                                    black bars, nothing else works
  B  art + a config entry                      widened by the mod
  C  art + no entry, own range already >= 427  widened by the default gate
  D  art + no entry + narrow range              NEVER WIDENED -- the defect

See HANDOFF-56 §6.
"""
import argparse
import collections
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp                                                       # noqa: E402
import field_bg_native as FN                                     # noqa: E402
import ff7nx_wsdata as WS                                        # noqa: E402
import audit_real                                                # noqa: E402
import field_bg_repack as RP                                     # noqa: E402

CONFIG_ENTRY = 'CONFIG\\widescreen\\config.toml'

CLASSES = {
    'A': 'no art -- black bars are the only answer',
    'B': 'art + config entry -- widened by the mod',
    'C': 'art + no entry, own range already wide -- widened by default',
    'D': 'art + no entry + narrow range -- NEVER WIDENED',
}


def art_span(sec9):
    """x extent of the BACK layer's tiles, in tile units."""
    _pages, tex_start, _tex_end = FN.parse_texture_block(sec9)
    offs = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    xs = [struct.unpack_from('<h', sec9, o + 2)[0] for o in offs]
    return (max(xs) - min(xs) + 16) if xs else 0


def mod_config(iro_path):
    """{field (lowercased): entry dict} from the mod's widescreen config."""
    reader = RP.IroReader(iro_path)
    with reader:
        raw = reader.read(CONFIG_ENTRY)
    if not raw:
        return {}
    parsed = WS._parse_toml_subset(raw.decode('utf-8', 'replace'))
    return {k.lower(): v for k, v in parsed.items() if isinstance(v, dict)}


def camera_widths(flevel_path):
    """{field (lowercased): section 8 camera width}."""
    archive = lgp.Archive(flevel_path)
    out = {}
    for entry in archive.entries:
        if not archive.is_field(entry):
            continue
        try:
            sections = lgp.split_sections(archive.decompressed(entry))
            out[entry['name'].lower()] = WS.read_section8_range(
                sections[7])['width']
        except Exception:                                      # noqa: BLE001
            continue
    return out


def classify(iro_path, flevel_path, gate=None):
    """{field: (class letter, camera width, art span, mode or None)}."""
    gate = gate or WS.WIDE_GATE
    cfg = mod_config(iro_path)
    cam = camera_widths(flevel_path)
    out = {}
    for field, sec9 in audit_real.mod_sections(iro_path).items():
        try:
            art = art_span(sec9)
        except Exception:                                      # noqa: BLE001
            continue
        if field not in cam:
            continue
        width = cam[field]
        entry = cfg.get(field)
        mode = int(entry['mode']) if entry and 'mode' in entry else None
        if art < gate:
            letter = 'A'
        elif entry is not None:
            letter = 'B'
        elif width >= gate:
            letter = 'C'
        else:
            letter = 'D'
        out[field] = (letter, width, art, mode)
    return out, gate


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])
    ap.add_argument('iro', help="the mod's .iro")
    ap.add_argument('--flevel', required=True,
                    help='the BUILT flevel.lgp the console will load')
    ap.add_argument('--gate', type=int, default=None)
    ap.add_argument('--class', dest='want', choices=sorted(CLASSES),
                    help='list the fields in one class')
    a = ap.parse_args(argv)

    rows, gate = classify(a.iro, a.flevel, a.gate)
    counts = collections.Counter(v[0] for v in rows.values())
    modes = collections.Counter(v[3] for v in rows.values() if v[0] == 'B')

    print('%s' % a.iro)
    print('  fields measured   %d' % len(rows))
    print('  gate              %d tile units' % gate)
    print()
    for letter in sorted(CLASSES):
        print('  %s  %-52s %4d' % (letter, CLASSES[letter], counts[letter]))
    print()
    print('  widescreen works on %d of %d   (B + C)'
          % (counts['B'] + counts['C'], len(rows)))
    print('  (B modes: %s)'
          % ', '.join('%s x%d' % ('mode %s' % m if m is not None else 'no mode',
                                  n) for m, n in sorted(
                                      modes.items(),
                                      key=lambda kv: (kv[0] is None, kv[0]))))

    if a.want:
        sel = sorted(f for f, v in rows.items() if v[0] == a.want)
        print()
        print('  class %s (%d):' % (a.want, len(sel)))
        for field in sel:
            _, width, art, mode = rows[field]
            print('     %-12s cam=%-5d art=%-6d mode=%s'
                  % (field, width, art,
                     mode if mode is not None else '-'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

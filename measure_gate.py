#!/usr/bin/env python3
"""
measure_gate.py -- art span vs section 8 camera range, per field.

    python3 measure_gate.py flevel.wide.lgp
    python3 measure_gate.py dump/romfs/ff7/workingdir/data/field/flevel.lgp

Two numbers per field:

  * camera width -- `right - left` from the section 8 trigger header, which
    is what `field_clip_with_camera_range` clamps the camera to and therefore
    how far the background can actually scroll.
  * art span     -- the x extent of the BACK layer tiles, i.e. how much
    background art exists.

16:9 needs 427 tile units (`320 + |wide_viewport_x|`, FFNx
widescreen.cpp:383). The two failure modes are opposite and both matter:

  * art >= 427 but camera < 427 -- the art is there and unreachable.
  * camera >= 427 but art < 427 -- widened onto nothing; these are the fields
    that show whatever the frame was cleared to. See HANDOFF-56 §4.
"""
import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp                                                       # noqa: E402
import field_bg_native as FN                                     # noqa: E402
import ff7nx_wsdata as WS                                        # noqa: E402


def art_span(sec9):
    """x extent of the BACK layer's tiles, in tile units."""
    _pages, tex_start, _tex_end = FN.parse_texture_block(sec9)
    offs = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    xs = [struct.unpack_from('<h', sec9, o + 2)[0] for o in offs]
    return (max(xs) - min(xs) + 16) if xs else 0


def survey(path):
    """[(field, camera_width, art_span)], plus the count that would not read."""
    archive = lgp.Archive(path)
    rows, unreadable = [], 0
    for entry in archive.entries:
        if not archive.is_field(entry):
            continue
        try:
            sections = lgp.split_sections(archive.decompressed(entry))
        except Exception:                                      # noqa: BLE001
            unreadable += 1
            continue
        if len(sections) < 9:
            unreadable += 1
            continue
        try:
            rng = WS.read_section8_range(sections[7])
            span = art_span(sections[8])
        except Exception:                                      # noqa: BLE001
            unreadable += 1
            continue
        rows.append((entry['name'], rng['width'], span))
    return rows, unreadable


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])
    ap.add_argument('flevel')
    ap.add_argument('--gate', type=int, default=WS.WIDE_GATE)
    ap.add_argument('--list', action='store_true',
                    help='print every field, not just the two failure sets')
    a = ap.parse_args(argv)

    rows, unreadable = survey(a.flevel)
    g = a.gate
    stuck = sorted(r for r in rows if r[2] >= g and r[1] < g)
    waste = sorted(r for r in rows if r[2] < g and r[1] >= g)

    print('%s' % a.flevel)
    print('  fields read                     %d  (%d unreadable)'
          % (len(rows), unreadable))
    print('  gate                            %d tile units' % g)
    print('  art span >= gate                %d'
          % sum(1 for r in rows if r[2] >= g))
    print('  camera width >= gate            %d'
          % sum(1 for r in rows if r[1] >= g))
    print('  art but camera still narrow     %d' % len(stuck))
    print('  camera widened but no art       %d' % len(waste))

    if a.list:
        print()
        print('  %-12s %6s %8s' % ('field', 'camera', 'art'))
        for name, cam, span in sorted(rows):
            print('  %-12s %6d %8d' % (name, cam, span))
        return 0

    for title, rowset in (('art exists, camera cannot reach it', stuck),
                          ('camera widened onto nothing', waste)):
        print()
        print('  %s (%d):' % (title, len(rowset)))
        for name, cam, span in rowset:
            print('     %-12s cam=%-6d art=%d' % (name, cam, span))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

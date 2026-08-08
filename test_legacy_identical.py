#!/usr/bin/env python3
"""
test_legacy_identical.py -- prove SEVENTH_NX_FIELD_BG_LEGACY=1 is a real
rollback.

    python3 test_legacy_identical.py <CosmosLimitBreak.iro> <orig_dir>

`orig_dir` holds the PRE-compaction `field_bg_repack.py` -- the copy out of
7th_heaven_nx-current2.7z. For each of the mod's own `chunk.9` sections this
runs both implementations over the real art and requires the output section 9
to be **byte identical**.

That is the only honest way to offer a rollback. "Set these four environment
variables" is not a rollback if nobody has checked what they produce, and the
last time a rollback was offered on that basis it silently left two of the new
passes running.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def load_orig(orig_dir):
    """Import the pre-compaction field_bg_repack under its own name."""
    path = os.path.join(orig_dir, 'field_bg_repack.py')
    spec = importlib.util.spec_from_file_location('orig_repack', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['orig_repack'] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('iro')
    ap.add_argument('orig_dir')
    ap.add_argument('--px', type=int, default=256)
    ap.add_argument('--limit', type=int, default=60)
    ap.add_argument('--slice', default=None)
    a = ap.parse_args(argv)
    sl_i, sl_n = (0, 1)
    if a.slice:
        sl_i, sl_n = (int(x) for x in a.slice.split('/'))

    os.environ['SEVENTH_NX_FIELD_BG_LEGACY'] = '1'
    import field_bg_native as FN
    import field_bg_repack as NEW
    import audit_real
    OLD = load_orig(a.orig_dir)

    secs = audit_real.mod_sections(a.iro)
    art_new = NEW.ArtProvider([(a.iro, None)], a.px, lambda *x: None)
    art_old = OLD.ArtProvider([(a.iro, None)], a.px, lambda *x: None)

    same = diff = skipped = 0
    bad = []
    for i, field in enumerate(sorted(secs)):
        if i % sl_n != sl_i or same + diff >= a.limit:
            continue
        try:
            base, _k = FN.resize_section9(secs[field], a.px)
        except Exception:                                        # noqa: BLE001
            skipped += 1
            continue
        if field not in art_new.fields():
            skipped += 1
            continue
        try:
            af = art_new.open(field)
            try:
                o_new, _s = NEW.repack_section9(
                    base, field, af, a.px, src_px=a.px,
                    pals_for=art_new.palettes)
            finally:
                art_new.close()
            af = art_old.open(field)
            try:
                o_old, _s = OLD.repack_section9(
                    base, field, af, a.px, src_px=a.px,
                    pals_for=art_old.palettes)
            finally:
                art_old.close()
        except Exception as exc:                                 # noqa: BLE001
            bad.append((field, 'EXCEPTION %r' % exc))
            diff += 1
            continue
        if o_new == o_old:
            same += 1
        else:
            diff += 1
            bad.append((field, 'section differs (%d vs %d bytes)'
                        % (len(o_new), len(o_old))))

    print('fields compared        %d' % (same + diff))
    print('byte identical         %d' % same)
    print('DIFFERENT              %d' % diff)
    for f in bad[:10]:
        print('    %-12s %s' % f)
    print('skipped (no art)       %d' % skipped)
    return 1 if diff else 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""
verify_pagesize.py -- prove the page-size ladder against a REAL flevel.

Answers three questions that no unit test can, because they need all 709
fields rather than a synthetic section:

  1. does resize_section9 survive every field at every offered page size,
     including the 128px DOWNSCALE path that did not exist before?
  2. is the round trip exact -- 256 -> px -> 256 byte-for-byte -- so the
     rescale is provably lossless in the directions where it must be?
  3. what does each field actually COST at each size, and how does that sit
     against the 18 MB where black bars were measured?

Chunked because it is slow (pure-Python LZS over a 131 MB archive) and the
harness running it has a short per-call timeout. State lives in a pickle, so
call it repeatedly with --chunk until it says DONE, then --report.

    python3 verify_pagesize.py cache  <flevel.lgp>     # once, in slices
    python3 verify_pagesize.py run    <px> [px ...]
    python3 verify_pagesize.py report
"""
import os
import pickle
import sys

import lgp
import field_bg_native as FN
import field_bg_repack as R

CACHE = '/tmp/sec9.pkl'
RESULT = '/tmp/pagesize_results.pkl'
MB = 1048576.0


def _load(path, default):
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return pickle.load(f)
    return default


def _save(path, obj):
    with open(path, 'wb') as f:
        pickle.dump(obj, f)


def cache(flevel, budget_s=35.0):
    """Decompress section 9 for every field, a slice at a time."""
    import time
    t0 = time.time()
    done = _load(CACHE, {})
    arc = lgp.Archive(flevel)
    names = [arc.index[k] for k in sorted(arc.index) if arc.is_field(arc.index[k])]
    todo = [e for e in names if e['name'] not in done]
    for e in todo:
        if time.time() - t0 > budget_s:
            break
        try:
            done[e['name']] = lgp.split_sections(arc.decompressed(e))[8]
        except Exception as exc:                                # noqa: BLE001
            done[e['name']] = ('ERR', str(exc)[:60])
    _save(CACHE, done)
    left = len(names) - len(done)
    print('cached %d/%d field(s), %d to go' % (len(done), len(names), left))
    return 0 if left else print('DONE') or 0


def run(sizes, budget_s=35.0):
    """Rescale every cached section to each size and record cost + exactness."""
    import time
    t0 = time.time()
    sec9 = _load(CACHE, {})
    if not sec9:
        print('! run "cache" first')
        return 2
    res = _load(RESULT, {})
    for px in sizes:
        bucket = res.setdefault(px, {})
        for name, blob in sec9.items():
            if name in bucket:
                continue
            if time.time() - t0 > budget_s:
                _save(RESULT, res)
                print('px=%d: %d/%d done, more to do'
                      % (px, len(bucket), len(sec9)))
                return 0
            if isinstance(blob, tuple):
                bucket[name] = ('SKIP', blob[1])
                continue
            try:
                new9, k = FN.resize_section9(blob, px)
                # re-parse at the NEW size: catches a page whose length no
                # longer matches its declared dimensions, which is exactly
                # what a wrong element size or a bad resize produces
                pages, _s, _e = FN.parse_texture_block(new9, px)
                live = [p for p in pages if p is not None]
                cost = sum(R._page_bytes(
                    FN.VANILLA_PX if p.depth == 1 else px, p.depth)
                    for p in live)
                # and back again -- must be byte-identical to what we started
                # with, for every size where the ratio is exact both ways
                back, _ = FN.resize_section9(new9, FN.VANILLA_PX, src_px=px)
                bucket[name] = ('OK', len(live), cost, k, back == blob)
            except Exception as exc:                            # noqa: BLE001
                bucket[name] = ('ERR', str(exc)[:70])
    _save(RESULT, res)
    print('DONE')
    return 0


def report():
    res = _load(RESULT, {})
    if not res:
        print('! nothing to report')
        return 2
    print('%-6s %5s %5s %6s  %-12s %8s %8s %7s %7s'
          % ('px', 'ok', 'err', 'exact', 'worst field', 'worst MB',
             'mean MB', '>6MB', '>18MB'))
    for px in sorted(res):
        rows = res[px]
        ok = [r for r in rows.values() if r[0] == 'OK']
        err = [(n, r[1]) for n, r in rows.items() if r[0] == 'ERR']
        if not ok:
            print('%-6d %5d %5d' % (px, 0, len(err)))
            continue
        costs = sorted(((n, r[2]) for n, r in rows.items() if r[0] == 'OK'),
                       key=lambda t: -t[1])
        exact = sum(1 for r in ok if r[4])
        print('%-6d %5d %5d %6s  %-12s %8.2f %8.2f %7d %7d'
              % (px, len(ok), len(err),
                 '%d/%d' % (exact, len(ok)),
                 costs[0][0], costs[0][1] / MB,
                 sum(c for _, c in costs) / len(costs) / MB,
                 sum(1 for _, c in costs if c / MB > 6),
                 sum(1 for _, c in costs if c / MB > 18)))
        for n, why in err[:4]:
            print('        ERR %-12s %s' % (n, why))
    print()
    print('"exact" is the 256 -> px -> 256 round trip being byte-identical.')
    print('The 18 MB column is the number of fields at or past the size where')
    print('black bars were MEASURED on hardware -- see HANDOFF-52 3.3.')
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == 'cache':
        return cache(argv[2])
    if cmd == 'run':
        return run([int(v) for v in argv[2:]] or [128, 256, 512, 768, 1024])
    if cmd == 'report':
        return report()
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Install the bounded texture cache in the current sdout module. No rebuild.

    python3 patch_texcache_sdout.py --dry-run
    python3 patch_texcache_sdout.py

Only ``sdout/.../exefs/main`` is touched. The default mode caches surfaces
through 256x256 while retaining the stock ten per exact size and 64 globally.
A backup is kept as ``main.pre-bounded-texcache``. Field, battle, character,
and world texture archives are not rebuilt or changed.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Reuse the tested system-lib fallback used by the other no-rebuild patcher.
from patch_glerror_sdout import ensure_lz4                 # noqa: E402
ensure_lz4()

import ff7nx_texcache as T                                 # noqa: E402
import nxmap                                               # noqa: E402

TITLE_ID = '0100A5B00BDC6000'
DEFAULT_MAIN = (HERE / 'sdout' / 'atmosphere' / 'contents' / TITLE_ID /
                'exefs' / 'main')
BACKUP_SUFFIX = '.pre-bounded-texcache-v2'


def state(target):
    img = nxmap.Main(str(target)).img
    st = T.read_state(img)
    print('module: %s' % target)
    print('  texture cache: %s' % (st or 'UNRECOGNIZED'))
    for line in T.verify(img):
        print('  ! ' + line)
    return img, st


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--main', type=Path, default=DEFAULT_MAIN)
    ap.add_argument('--mode', choices=T.MODES, default='small')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    target = args.main.resolve()
    if not target.is_file():
        raise SystemExit('no sdout module: %s' % target)
    img, before = state(target)
    bad = T.verify(img)
    if bad:
        raise SystemExit('refusing to write over an unrecognized module')
    if before == args.mode:
        print('\nalready in %s mode; nothing to do.' % args.mode)
        return 0

    fd, tmp_name = tempfile.mkstemp(prefix='.texcache-', dir=target.parent)
    os.close(fd)
    os.unlink(tmp_name)
    tmp = Path(tmp_name)
    try:
        if not T.apply_to_nso(target, tmp, print, args.mode):
            raise SystemExit('migration refused; original module unchanged')
        migrated = nxmap.Main(str(tmp)).img
        after = T.read_state(migrated)
        if after != args.mode or T.verify(migrated):
            raise SystemExit('post-write verification failed; original '
                             'module unchanged')
        print('verified: %s -> %s' % (before, after))
        if args.dry_run:
            print('dry run complete; no files changed')
            return 0
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(target, backup)
            print('backup: %s' % backup)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()

    print('patched: %s' % target)
    print('sha256: %s' % hashlib.sha256(target.read_bytes()).hexdigest())
    print('all texture archives and flevel.lgp were left untouched')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

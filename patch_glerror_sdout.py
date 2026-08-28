#!/usr/bin/env python3
"""Migrate a built sdout module to the non-fatal GL reporter. No rebuild.

    python3 patch_glerror_sdout.py --dry-run
    python3 patch_glerror_sdout.py

Builds 187/188 suppressed three end-of-frame failures by replacing the first
``glGetError`` call with ``mov w0, wzr``.  That also bypassed the error logger
and the second ``glGetError`` drain loop.  This tool restores those calls and
NOPs only the reporter's deliberate fatal UDF instructions.

Only ``sdout/.../exefs/main`` is touched.  The default ``flip`` mode changes
three reporters.  A backup is kept as ``main.pre-glerror-state-fix``.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types


def ensure_lz4():
    try:
        import lz4.block  # noqa: F401
        return
    except ImportError:
        pass
    library = ctypes.util.find_library('lz4')
    if not library:
        raise SystemExit('need python-lz4 or a system liblz4 installation')
    lib = ctypes.CDLL(library)
    lib.LZ4_decompress_safe.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                        ctypes.c_int, ctypes.c_int]
    lib.LZ4_decompress_safe.restype = ctypes.c_int
    lib.LZ4_compressBound.argtypes = [ctypes.c_int]
    lib.LZ4_compressBound.restype = ctypes.c_int
    lib.LZ4_compress_default.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                         ctypes.c_int, ctypes.c_int]
    lib.LZ4_compress_default.restype = ctypes.c_int
    block = types.ModuleType('lz4.block')

    def decompress(data, uncompressed_size):
        out = ctypes.create_string_buffer(uncompressed_size)
        n = lib.LZ4_decompress_safe(data, out, len(data), uncompressed_size)
        if n < 0:
            raise RuntimeError('LZ4 decompression failed (%d)' % n)
        return out.raw[:n]

    def compress(data, store_size=False, **_kw):
        if store_size:
            raise RuntimeError('the NSO fallback requires raw LZ4 blocks')
        cap = lib.LZ4_compressBound(len(data))
        out = ctypes.create_string_buffer(cap)
        n = lib.LZ4_compress_default(data, out, len(data), cap)
        if n <= 0:
            raise RuntimeError('LZ4 compression failed (%d)' % n)
        return out.raw[:n]

    block.decompress = decompress
    block.compress = compress
    pkg = types.ModuleType('lz4')
    pkg.block = block
    sys.modules['lz4'] = pkg
    sys.modules['lz4.block'] = block


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
ensure_lz4()

import ff7nx_glerror as G                                      # noqa: E402
import nxmap                                                   # noqa: E402

TITLE_ID = '0100A5B00BDC6000'
DEFAULT_MAIN = (HERE / 'sdout' / 'atmosphere' / 'contents' / TITLE_ID /
                'exefs' / 'main')
BACKUP_SUFFIX = '.pre-glerror-state-fix'


def show(target):
    img = nxmap.Main(str(target)).img
    legacy = G.read_legacy_state(img)
    state = G.read_state(img)
    print('module: %s' % target)
    print('  legacy glGetError skips: %s'
          % (legacy if legacy is not None else 'UNDECODABLE'))
    print('  non-fatal reporters:     %s'
          % ('%d of %d' % state if state else 'migration required'))
    bad = G.verify(img)
    for line in bad:
        print('  ! ' + line)
    return img, bad


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--main', type=Path, default=DEFAULT_MAIN)
    ap.add_argument('--mode', choices=G.MODES, default='flip')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    target = args.main.resolve()
    if not target.is_file():
        raise SystemExit('no sdout module: %s' % target)
    img, bad = show(target)
    if bad:
        raise SystemExit('refusing to write over an unrecognized module')
    ps = G.patches(img, args.mode)
    if not ps:
        print('\nalready in corrected %s mode; nothing to do.' % args.mode)
        return 0

    fd, tmp_name = tempfile.mkstemp(prefix='.glerror-', dir=target.parent)
    os.close(fd)
    os.unlink(tmp_name)
    tmp = Path(tmp_name)
    try:
        if not G.apply_to_nso(target, tmp, print, args.mode):
            raise SystemExit('migration refused; original module unchanged')
        migrated = nxmap.Main(str(tmp)).img
        want = len(G.gates_for(args.mode))
        if G.read_state(migrated) != (want, len(G.GATES)):
            raise SystemExit('post-write verification failed; original '
                             'module unchanged')
        if G.read_legacy_state(migrated) != 0:
            raise SystemExit('legacy glGetError skips remain; original '
                             'module unchanged')
        print('verified: all glGetError calls live; %d reporter trap(s) '
              'non-fatal' % want)
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
    print('flevel.lgp and all texture archives were left untouched')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

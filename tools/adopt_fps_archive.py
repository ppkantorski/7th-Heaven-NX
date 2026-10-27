#!/usr/bin/env python3
"""adopt_fps_archive.py -- one-shot, run it once, then delete it if you like.

BUILD 326 teaches the build cache that battle.lgp is written TWICE: once by
the archive builder, and again about ten seconds later by the 60 FPS pass,
which rewrites it in place with the animation waits scaled x4. From now on the
build records that second signature itself.

Your CURRENT cache predates that, so your next build would rebuild battle.lgp
one last time just to learn its own answer. This stamps the answer in, so the
reuse switch works on the very next run instead of the one after.

It only does anything if the archive on disk is consistent with the record it
is stamping: same size, and a LATER mtime -- which is exactly the fingerprint
of "the fps pass rewrote it in place". Anything else and it refuses, because
the whole value of these records is that they can be trusted.

    python3 tools/adopt_fps_archive.py

It lives in tools/ deliberately: `_archive_fingerprint` hashes every .py in
the build folder itself, so a new script dropped in beside build.py would
invalidate all five archives -- the exact thing this is here to avoid.
`os.listdir(HERE)` does not descend, so a subdirectory is free.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = os.path.join(HERE, 'cache', '_archive_fp', 'battle.lgp.fp')
TITLE_ID = '0100A5B00BDC6000'
ARCHIVE = os.path.join(HERE, 'sdout', 'atmosphere', 'contents', TITLE_ID,
                       'romfs', 'ff7', 'workingdir', 'data', 'battle',
                       'battle.lgp')


def main():
    if not os.path.exists(REC):
        print('no cache record at %s -- nothing to adopt (one normal build '
              'will create it)' % REC)
        return 1
    if not os.path.exists(ARCHIVE):
        print('no built archive at %s -- nothing to adopt' % ARCHIVE)
        return 1

    parts = open(REC).read().split('\n')
    if len(parts) < 4:
        print('the cache record is not the expected shape; refusing')
        return 1
    if any(l.startswith('fps=') for l in parts[4:]):
        print('already stamped -- nothing to do')
        return 0

    st = os.stat(ARCHIVE)
    try:
        rec_size, rec_mtime = int(parts[1]), int(parts[2])
    except ValueError:
        print('the cache record is not the expected shape; refusing')
        return 1

    if st.st_size != rec_size:
        print('REFUSED: the archive is %d bytes and the record says %d.\n'
              '         The 60 FPS pass rewrites battle.lgp at the SAME size,'
              ' so a\n         different size means this is not that file. '
              'One normal build\n         will record it correctly.'
              % (st.st_size, rec_size))
        return 1
    if st.st_mtime_ns <= rec_mtime:
        print('REFUSED: the archive is not NEWER than its record (%+.3f s), '
              'so the\n         60 FPS pass is not what last wrote it.'
              % ((st.st_mtime_ns - rec_mtime) / 1e9))
        return 1

    with open(REC, 'w') as f:
        f.write('\n'.join(parts[:4]) + '\nfps=%d,%d\n'
                % (st.st_size, st.st_mtime_ns))
    print('stamped battle.lgp: %s bytes, rewritten %.3f s after the archive '
          'builder\n        finished it -- that is the 60 FPS pass, and it is '
          'now on record.\n        Your next build can reuse this archive.'
          % (format(st.st_size, ','), (st.st_mtime_ns - rec_mtime) / 1e9))
    return 0


if __name__ == '__main__':
    sys.exit(main())

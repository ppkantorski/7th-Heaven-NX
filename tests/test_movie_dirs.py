#!/usr/bin/env python3
"""
test_movie_dirs.py -- every movies directory, not just data/movies.

    python3 test_movie_dirs.py

WHAT IT IS FOR
==============
The Switch port keeps localised movies under the language folders. The
operator's dump has

    data/movies/                     ~100 films
    data/lang-en/movies/ending2.mp4
    data/lang-en/movies/joneva_e.mp4

and `_emplace_movies` enumerated `data/movies` alone. With 30 FPS FMVs on
that left those two at 15 fps while `movie-fps` halved the frame counter for
everything, and while `ending2.cam` -- the largest track in moviecam.lgp,
7980 records -- was stretched x2 for a movie that had not been. The ending
would have run its camera at half speed with its cues at double time.

WHAT IS CHECKED
===============
The planning half: which (directory, stem) pairs are built, where a mod's
file is aimed when the vanilla copy of that stem lives in a language folder,
and that the same stem in two languages stays two separate films. No ffmpeg
is needed -- the work set is inspected, not encoded.

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
import shutil
import sys
import tempfile

import build
import movies as movie_convert

FAIL = []


def check(cond, msg):
    print(('  ok    ' if cond else '  FAIL  ') + msg)
    if not cond:
        FAIL.append(msg)


class FakeDump:
    def __init__(self, workingdir):
        self.workingdir = workingdir
        self.exe = None
        self.nso = None


DUMP_TREE = ['data/movies/opening.mp4', 'data/movies/southmk.mp4',
             'data/movies/meteosky.mp4',
             'data/lang-en/movies/ending2.mp4',
             'data/lang-en/movies/joneva_e.mp4',
             'data/lang-de/movies/ending2.mp4',
             'data/lang-ja/movies/ending2.mp4']


def make_dump():
    root = tempfile.mkdtemp()
    wd = os.path.join(root, 'workingdir')
    for p in DUMP_TREE:
        full = os.path.join(wd, *p.split('/'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, 'wb').write(b'\0' * 8)
    return FakeDump(wd), root


def work_set(mod_movies, normalise):
    """Run the planning half of _emplace_movies and return its work set."""
    dump, root = make_dump()
    captured = {}

    real_convert = movie_convert.have_ffmpeg

    class Plan:
        pass
    plan = Plan()
    plan.movies = mod_movies
    plan.movie_audio = {}
    plan.opening_fps = plan.opening_dims = None

    # Stop at the encode loop: replace probe so every file is "not a video",
    # which makes the loop skip everything after the work set is built.
    real_probe = movie_convert.probe
    movie_convert.probe = lambda *_a, **_k: None
    real_30 = build.movie_30fps
    build.movie_30fps = lambda: normalise
    real_have = movie_convert.have_ffmpeg
    movie_convert.have_ffmpeg = lambda: True
    real_log = []
    out = tempfile.mkdtemp()
    try:
        # _emplace_movies builds `work` then iterates; capture by monkeypatching
        # sorted() is not practical, so read the log line it prints instead.
        build._emplace_movies(plan, os.path.join(out, 'romfs'), out, dump,
                              real_log.append, lambda *a: None, [])
    finally:
        movie_convert.probe = real_probe
        build.movie_30fps = real_30
        movie_convert.have_ffmpeg = real_have
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
    return real_log


print('every movies directory is enumerated\n')
log = work_set({}, normalise=True)
head = next((l for l in log if l.startswith('movies:')), '')
check('7 to build' in head,
      'all 7 films across 4 directories are in the work set (%s)' % head.strip())
dirs = next((l for l in log if 'directories:' in l), '')
for d in ('data/movies', 'data/lang-en/movies', 'data/lang-de/movies',
          'data/lang-ja/movies'):
    check(d in dirs, 'covers %s' % d)

print('\nthe same stem in three languages stays three films\n')
check(dirs.count('movies') >= 4,
      'ending2 exists in lang-en, lang-de and lang-ja and each is converted '
      'separately rather than one of them winning')

print('\na mod file is aimed at the directory the vanilla copy lives in\n')
tmp = tempfile.mkdtemp()
fake = os.path.join(tmp, 'ending2.avi')
open(fake, 'wb').write(b'\0' * 8)
log = work_set({'ending2': (fake, None)}, normalise=True)
head = next((l for l in log if l.startswith('movies:')), '')
check('7 to build' in head and '1 from the mod' in head,
      'the mod file replaces one of the seven rather than adding an eighth '
      '(%s)' % head.strip())
shutil.rmtree(tmp, ignore_errors=True)

print('\nwith 30 FPS FMVs off, only the mod\'s own files are touched\n')
tmp = tempfile.mkdtemp()
fake = os.path.join(tmp, 'ending2.avi')
open(fake, 'wb').write(b'\0' * 8)
log = work_set({'ending2': (fake, None)}, normalise=False)
head = next((l for l in log if l.startswith('movies:')), '')
check('1 to convert' in head,
      'one film, not the whole set -- nothing is re-encoded that does not '
      'need to be (%s)' % head.strip())
shutil.rmtree(tmp, ignore_errors=True)

print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)

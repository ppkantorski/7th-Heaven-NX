#!/usr/bin/env python3
"""BUILD 764 diagnostic: which movie entry points the Additional Credits run.

    python3 tools/diag/credits_music_diag.py IN_MAIN OUT_MAIN

  fw_movie_update entry  -> music volume to 0 at once      (instant silence)
  fw_movie_prepare entry -> music volume to 0 over ~5 s    (slow fade)
Only once the title track (id 127) has a stream; no restore: restart the game after the test.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os, sys, struct
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src'))
import nxpaths; nxpaths.install(tools=True)
import a64 as A, ff7nx_cave, nxmap
import ff7nx_audio_cave as AC
import ff7nx_titlemusic as T

PREPARE, PREPARE_ORIG = 0x10F11E0, 0xD10183FF          # sub sp, sp, #0x60

def cave(orig, resume, frames):
    def build(c, addr):
        a = AC.Asm(c, addr)
        T._save_args(a); AC.save_host(a)
        T._player(a)                                   # only once the title track exists
        a.emit(A.ldr64(9, 0, 0x20 + 8 * T.TRACK_ID))
        a.cbz64(9, 'skip')
        a.emit(A.movz(20, 0))
        T._vol_trans(a, 20, frames)
        a.label('skip')
        AC.restore_host(a); T._restore_args(a)
        a.emit(orig); a.emit(A.b(a.pc(), resume))
        return a.resolve()
    return build

def main(src, dst):
    with open(src, 'rb') as f: blob = f.read()
    segs, raw = AC.segments(blob); text = bytearray(raw[0])
    AC.expect_word(text, PREPARE, PREPARE_ORIG, 'fw_movie_prepare entry')
    m = nxmap.Main(src)
    pool = ff7nx_cave.HolePool(text, starts=set(m.arm_starts))
    out = {}
    for site, orig, frames in ((T.MOVIE_SITE, T.MOVIE_ORIG, 0), (PREPARE, PREPARE_ORIG, 300)):
        e, placed = ff7nx_cave.emit_laid_out(pool, cave(orig, site + 4, frames))
        placed[site] = A.b(site, e); out.update(placed)
    for va, w in out.items(): struct.pack_into('<I', text, va, w)
    raw[0] = bytes(text)
    open(dst, 'wb').write(AC.pack(blob, raw, 0))
    print('%d words' % len(out))

if __name__ == '__main__':
    main(*sys.argv[1:3])

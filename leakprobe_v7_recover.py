import sys, os, struct
sys.path.insert(0, os.getcwd())
import leakprobe_read as R, ff7nx_audio_cave as AC, leakprobe as L, nsosyms
log = open('logs/crash_lp7_606c_01790647756.log').read()
import re
th = log.split('Threads[01]')[0]
sd = th.split('Stack Dump:')[1].split('TLS Address')[0]
by = bytearray()
for line in sd.strip().splitlines()[1:]:
    parts = line.split()
    by += bytes(int(x,16) for x in parts[1:17])
dump = 'dump/exefs/main'
b = open(dump,'rb').read(); segs, raw = AC.segments(b)
slots = L.gfx_slots(segs, raw); m, plt = nsosyms.imports(dump)
F = 1108
print('nslots', len(slots))
rows=[]
for k in range(32):
    a, us = struct.unpack_from('<II', by, 8*k)
    s = a & 0xFFFF
    calls = us*96/5
    r = s-1
    if r < 0 or r >= len(slots) or not us: print("skip", s, us); continue
    _e, slot, tgt = slots[r]
    rows.append((calls/F, r, tgt, slot, ', '.join(R._gl_calls(m.img, plt, tgt)[:6])))
for c, r, tgt, slot, gl in sorted(rows, reverse=True):
    print('%4d vt+0x%X  +0x%X  %8.1f calls/frame  %s' % (r, slot, tgt, c, gl))

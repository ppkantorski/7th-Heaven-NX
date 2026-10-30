#!/usr/bin/env python3
"""
glprobe_read.py -- decode a BUILD 860e GL state probe crash report
(src/analysis/ff7nx_glprobe.py).

    python3 tools/probes/glprobe_read.py <crash report .log>

Prints every distinct GL state the recorded frame drew with, in draw order:
viewport, scissor (when on), the framebuffer, depth test / func / mask,
blend, cull, and how many consecutive draws used it.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import re
import struct
import sys

MAGIC = 0x42504C47
DFUNC = ('NEVER', 'LESS', 'EQUAL', 'LEQUAL', 'GREATER', 'NOTEQUAL', 'GEQUAL',
         'ALWAYS')


def parse_registers(text):
    regs = {}
    for m in re.finditer(r'X\[(\d{1,2})\]\s*:\s*(?:0x)?([0-9A-Fa-f]{1,16})',
                         text):
        regs.setdefault(int(m.group(1)), int(m.group(2), 16))
    for name, idx in (('FP', 29), ('LR', 30)):
        m = re.search(r'\b%s\s*:\s*(?:0x)?([0-9A-Fa-f]{1,16})' % name, text)
        if m:
            regs.setdefault(idx, int(m.group(1), 16))
    return regs


def parse_dump(text, title):
    lines = text.splitlines()
    out = bytearray()
    for i, line in enumerate(lines):
        if line.strip().startswith(title):
            for row in lines[i + 1:]:
                m = re.match(r'\s+([0-9A-Fa-f]{6,16}):?((?:\s+[0-9A-Fa-f]{2}){1,16})'
                             r'\s*(?:\|.*)?$', row)
                if not m:
                    break
                out += bytes(int(b, 16) for b in m.group(2).split())
            break
    return bytes(out)


def entries_after(blob, tag):
    """The 15 entries after the 16-byte header `tag` (found at any offset)."""
    at = blob.find(tag)
    if at < 0:
        return []
    out = []
    for i in range(15):
        o = at + 16 + 16 * i
        if o + 16 > len(blob):
            break
        out.append(struct.unpack_from('<QQ', blob, o))
    return out


def v12(v):
    return v - 4096 if v >= 3072 else v


def decode(a, b):
    vp = [v12((a >> (12 * k)) & 0xFFF) for k in range(4)]
    sc = [v12((b >> (12 * k)) & 0xFFF) for k in range(4)]
    flags = (a >> 56) & 0xF
    return {
        'viewport': vp, 'fb': (a >> 48) & 0xFF,
        'scissor': sc if flags & 1 else None,
        'scissor_box': sc, 'depth_test': bool(flags & 2),
        'blend': bool(flags & 4), 'cull': bool(flags & 8),
        'depth_mask': bool((a >> 60) & 1),
        'depth_func': DFUNC[(b >> 48) & 7], 'draws': b >> 51,
    }


def main_v2(text, regs):
    """BUILD 860g probe v2: per-draw pairs of slots (state, caller chain)."""
    slots = slots_after(parse_dump(text, 'Stack Dump'), b'GLPBSTK0')
    slots += slots_after(parse_dump(text, 'TLS Dump'), b'GLPBTLS0')
    slots += [(regs.get(2, 0), regs.get(3, 0)), (regs.get(4, 0), regs.get(5, 0)),
              (regs.get(6, 0), regs.get(7, 0)), (regs.get(29, 0), regs.get(30, 0))]
    n = regs.get(1, 0) & 0xFFFFFFFF
    print('probe v2: %d draws in the frame (first %d kept)' % (n, min(n, 17)))
    for i in range(min(n, 17)):
        a, b = slots[2 * i]
        c, d = slots[2 * i + 1]
        st = decode(a, b)
        chain = [(c & 0xFFFFFFFF), c >> 32, (d & 0xFFFFFFFF), d >> 32]
        sc = ('scissor %s' % st['scissor']) if st['scissor'] else 'no scissor'
        print('%2d  vp %-22s %-30s fb %d mode %-2d rt %04x  depth %s %s %s  '
              'blend %s  <- %s'
              % (i, st['viewport'], sc, (a >> 48) & 0xF, (a >> 52) & 0xF,
                 b >> 51, 'on' if st['depth_test'] else 'off',
                 st['depth_func'], 'write' if st['depth_mask'] else 'nowrite',
                 'on' if st['blend'] else 'off',
                 ' '.join('%x' % v for v in chain)))
    return 0


def slots_after(blob, tag):
    at = blob.find(tag)
    if at < 0:
        return []
    return [struct.unpack_from('<QQ', blob, at + 16 + 16 * i)
            for i in range(15) if at + 32 + 16 * i <= len(blob)]


def main(path):
    text = open(path, errors='replace').read()
    regs = parse_registers(text)
    x0 = regs.get(0, 0)
    if x0 & 0xFFFFFFFF != MAGIC:
        print('not a GL probe report (X0 = 0x%X)' % x0)
        return 1
    if x0 >> 32 >= 2:
        return main_v2(text, regs)
    n, draws = regs.get(1, 0) & 0xFFFFFFFF, regs.get(1, 0) >> 32
    ents = entries_after(parse_dump(text, 'Stack Dump'), b'GLPBSTK0')
    ents += entries_after(parse_dump(text, 'TLS Dump'), b'GLPBTLS0')
    ents += [(regs.get(2, 0), regs.get(3, 0)), (regs.get(4, 0), regs.get(5, 0)),
             (regs.get(6, 0), regs.get(7, 0)), (regs.get(29, 0), regs.get(30, 0))]
    print('probe v%d: %d state entries, %d draws in the frame'
          % (x0 >> 32, n, draws))
    for i, (a, b) in enumerate(ents[:min(n, 34)]):
        d = decode(a, b)
        sc = ('scissor %s' % d['scissor']) if d['scissor'] else 'no scissor'
        print('%2d  x%-4d vp %-22s %-30s fb %d  depth %s %s %s  blend %s'
              % (i, d['draws'], d['viewport'], sc, d['fb'],
                 'on' if d['depth_test'] else 'off', d['depth_func'],
                 'write' if d['depth_mask'] else 'nowrite',
                 'on' if d['blend'] else 'off'))
    if n > 34:
        print('(%d more entries were not kept)' % (n - 34))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))

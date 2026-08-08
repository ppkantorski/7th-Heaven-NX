#!/usr/bin/env python3
"""
Find executable space in `main` beyond the 2,464-byte tail gap the cave
allocator currently lives in.

THE PROBLEM
-----------
Caves are appended between the end of .text (module +0x1152660) and the
start of .rodata (+0x1153000). That is 2,464 bytes, page-alignment slack
and nothing more. The shipping 60 FPS preset uses 2,460 of them. Four
bytes left, which is why 360 movement will not fit and why a 32-byte
widescreen cave has nowhere to go.

WHERE THE ROOM IS
-----------------
The recompiled functions in .text are 16-byte aligned, so almost every one
ends with 1-3 words of zero padding. Across 18 MB that is ~80 KB. It is
not contiguous -- the largest single hole is 12 bytes -- but there are
thousands of them, and a cave can be chained across holes at a cost of one
`b` per hole.

WHAT "DEAD" MEANS HERE
----------------------
A hole is only reported when all four hold:

  1. every word in it is zero;
  2. the instruction immediately before it is `ret`, so nothing falls in;
  3. the word immediately after it is a known function start;
  4. no branch anywhere in .text targets any word inside it -- checked
     against every b/bl/b.cond/cbz/cbnz/tbz/tbnz in the module;
  5. no aligned u32 or u64 anywhere in .rodata or .data holds the address
     of any word inside it.

(4) and (5) are the ones that matter. Alignment padding is normally
unreachable, but a compiler is free to put a branch target there, and a
jump table would look like padding to a naive scan.

(4) alone was the original test and it only sees DIRECT branches. (5) closes
the obvious remaining hole: an address reached indirectly has to be written
down somewhere, and in a statically linked module with no runtime codegen
that somewhere is .rodata or .data. Scanning both for any word that names a
byte inside a candidate hole rejects 128 of 7,659 on the stock 1.0.3 module
-- a small price, and it turns "no direct branch goes here" into "nothing in
the image says this address at all".

It is still not a proof: an address synthesised arithmetically at runtime
would evade both. Treat the output as a candidate pool to allocate from with
verification, not as a licence to overwrite blindly. The re-check that a hole
is STILL ZERO in the module being patched, plus the NSO self-check and the
.text diff report, are the backstop.

Usage:
    python3 cave_space.py [path/to/main]           report
    python3 cave_space.py [path/to/main] --json holes.json
"""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import nxmap                                                    # noqa: E402

TEXT_END = 0x1152660
RODATA = 0x1153000
RET = 0xD65F03C0


def named_targets(blob, lo=TEXT_END):
    """
    Every .text address (< `lo`) that some aligned u32 or u64 in `blob` holds.

    Test 5: an address reached indirectly has to be written down somewhere,
    and in a statically linked module with no runtime codegen that somewhere
    is the read-only data. `blob` is everything from RODATA on -- .rodata and
    .data, which the caller can hand over either as the tail of a full module
    image or as the two segment blobs concatenated.

    R_AARCH64_RELATIVE addends were checked too and hit zero holes (they name
    function entries, which a hole is never part of), so they are not
    re-scanned here.
    """
    out = set()
    for (v,) in struct.iter_unpack('<I', blob[:len(blob) & ~3]):
        if v < lo:
            out.add(v)
    for (v,) in struct.iter_unpack('<Q', blob[:len(blob) & ~7]):
        if v < lo:
            out.add(v)
    return out


def branch_targets(words):
    """Every address any direct branch in .text can reach."""
    out = set()
    for i, w in enumerate(words):
        a = i * 4
        if (w & 0xFC000000) in (0x14000000, 0x94000000):        # b / bl
            imm = w & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            out.add(a + imm * 4)
        elif (w & 0xFF000000) == 0x54000000:                    # b.cond
            imm = (w >> 5) & 0x7FFFF
            if imm & 0x40000:
                imm -= 0x80000
            out.add(a + imm * 4)
        elif (w & 0x7E000000) == 0x34000000:                    # cbz / cbnz
            imm = (w >> 5) & 0x7FFFF
            if imm & 0x40000:
                imm -= 0x80000
            out.add(a + imm * 4)
        elif (w & 0x7E000000) == 0x36000000:                    # tbz / tbnz
            imm = (w >> 5) & 0x3FFF
            if imm & 0x2000:
                imm -= 0x4000
            out.add(a + imm * 4)
    return out


def find_holes_in(img, starts=None, named=None):
    """Holes in an already-loaded module image (see find_holes)."""
    words = struct.unpack('<%dI' % (TEXT_END // 4), img[:TEXT_END])
    if starts is None:
        starts = _starts_cache(img)
    targets = branch_targets(words)
    if named is None:
        named = named_targets(img[RODATA:])

    holes, i, n = [], 0, len(words)
    rejected = {'not after ret': 0, 'not before a function': 0,
                'branch target inside': 0, 'named by a data word': 0}
    while i < n:
        if words[i]:
            i += 1
            continue
        j = i
        while j < n and words[j] == 0:
            j += 1
        if i == 0 or words[i - 1] != RET:
            rejected['not after ret'] += 1
        elif (j * 4) not in starts:
            rejected['not before a function'] += 1
        elif any(((i + k) * 4) in targets for k in range(j - i)):
            rejected['branch target inside'] += 1
        elif any(((i + k) * 4) in named for k in range(j - i)):
            rejected['named by a data word'] += 1
        else:
            holes.append((i * 4, j - i))
        i = j
    return holes, rejected


_STARTS = {}


def _starts_cache(img):
    key = id(img)
    if key not in _STARTS:
        raise ValueError('pass starts= explicitly, or use find_holes(path)')
    return _STARTS[key]


def find_holes(path):
    m = nxmap.Main(path)
    _STARTS[id(m.img)] = set(m.arm_starts)
    return find_holes_in(m.img, set(m.arm_starts))


def default_nso():
    """The dump's exefs/main, found the same way the rest of the tool does."""
    try:
        import build
        dump = build.find_game_dump(HERE)
        if dump and dump.nso and os.path.exists(dump.nso):
            return dump.nso
    except Exception:
        pass
    return os.path.join(HERE, 'dump', 'exefs', 'main')


def usable(holes):
    """
    Instructions available once each hole spends one word on the `b` that
    leaves it. A 1-word hole is worth nothing: the branch fills it.
    """
    return sum(max(0, ln - 1) for _, ln in holes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('nso', nargs='?', default=None)
    ap.add_argument('--json')
    args = ap.parse_args()
    if args.nso is None:
        args.nso = default_nso()
    if not os.path.exists(args.nso):
        sys.exit('no such module: %s (pass the path to exefs/main)' % args.nso)

    holes, rejected = find_holes(args.nso)
    tail = RODATA - TEXT_END
    total = sum(ln for _, ln in holes) * 4
    use = usable(holes) * 4

    print('current cave region')
    print(f'  .text end {TEXT_END:#x} -> .rodata {RODATA:#x} '
          f'= {tail:,} bytes total')
    print(f'  the shipping 60 FPS preset uses 2,460 of them\n')
    print('reclaimable inter-function padding')
    print(f'  holes that pass every safety test : {len(holes):,}')
    print(f'  raw bytes                         : {total:,}')
    print(f'  usable after one `b` per hole     : {use:,}'
          f'   ({use / tail:.1f}x the tail gap)')
    by = {}
    for _, ln in holes:
        by[ln] = by.get(ln, 0) + 1
    for ln in sorted(by):
        print(f'     {ln}-word holes: {by[ln]:>6}   '
              f'{by[ln] * ln * 4:>9,} bytes raw, '
              f'{by[ln] * max(0, ln - 1) * 4:>9,} usable')
    print('\n  rejected, and why:')
    for k, v in rejected.items():
        print(f'     {v:>6,}  {k}')

    if args.json:
        with open(args.json, 'w') as f:
            json.dump({'text_end': TEXT_END, 'rodata': RODATA,
                       'holes': [{'va': a, 'words': ln} for a, ln in holes]},
                      f, indent=1)
        print(f'\nwrote {args.json} ({len(holes):,} holes)')


if __name__ == '__main__':
    main()

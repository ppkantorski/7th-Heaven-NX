#!/usr/bin/env python3
"""ff7nx_ripplefade.py -- the pond ripples of las2_2 / las2_3 in true colour,
their fade-out kept as brightness levels. BUILD 618z14.

The user (hardware 10-06): "there are water ripple effects in las2_2 and
las2_3 ... as the ripple fades out it becomes super pixellated ... i think
these pond animations should be true color".

WHAT THEY ARE. Three entities per field (ring1..3) each play a ripple as
tile frames (BGON/BGOFF of params P, states 0..7 then P+1, 0..5), additive,
on paletted 1x FX pages, each ripple with its own palette q. The last frame
is then held while a loop fades it out through the palette:

    bgon P+1,5
    loop: if v > 0 { v -= 1; MPPAL buf A -> buf B by (v, v, v) /64;
                     CPPAL; LDPAL buf B -> palette q; back }
    wait 1; bgoff P+1,5; LDPAL buf A -> palette q (restored)

(v starts at 66: 66 frames from full to black). The rest of the time the
palette is the original. A palette fade cannot be done on a truecolor page,
which is why the build left these at 1x, and at low brightness the 1x
5-bit palette steps are what the user saw.

WHAT THIS DOES (every check passes or the field is untouched):
  * every record of q (all ripple frames) becomes a truecolor additive
    record on the FX band (slots 15..23), Cosmos's HD frame for that
    sheet and palette (the brightest of Cosmos's per-palette-state frames:
    the full-brightness one);
  * the held frame's records instead become L copies, one background
    parameter per brightness level (params the field does not use), each
    the HD frame times that level's factor: level j shows v in
    [jD, jD + D) at ((j + 0.5) D)/64 -- the MPPAL multiply, quantised to
    L steps instead of 1x palette steps;
  * the loop's MPPAL+CPPAL+LDPAL (20 bytes) become a long jump to a stub
    appended after the last script (BGCLR the levels; tmp = v; tmp /= D;
    tmp += first level param; BGON tmp,0; long jump back to the loop's IF,
    so the loop still takes one backward jump per pass); the restoring LDPAL
    after it becomes a jump to a stub that clears the levels, does that
    LDPAL and jumps back. Skipped bytes become RET (unreachable). Section 1
    otherwise moves only its string table and AKAO offsets.
L is the largest of 16/12/10/8 that fits the FX band, the parameter range
and the field memory cap.

BUILD 619 -- WHAT HARDWARE SHOWED, AND THE REDESIGN. The 618z14 stub made
the fade flicker: the ripple vanished for one or two of every three frames.
MEASURED on the user's capture (60 fps): each fade pass took 3 frames, the
held ripple was absent on 1 of 3 at the high levels and 2 of 3 at the low
ones. That is exactly what the field interpreter does with an entity that
runs more than EIGHT opcodes in one tick: it stops there and resumes on the
next. The stub was IF, DEC, LSKIP, 10 x BGCLR, SET, DIV, PLUS, BGON, LBACK --
18 opcodes, so the levels were cleared in one tick and the new one switched
on two ticks later. (The vanilla pass is 6 opcodes: IF, DEC, MPPAL, CPPAL,
LDPAL, BACK -- one tick, which is also why the fade now takes its vanilla
time again instead of three times as long.)

So the pass is now written IN PLACE of the 20 bytes of MPPAL/CPPAL/LDPAL,
and is 7 opcodes, one tick, with nothing in it that can be seen half done:
    IF v > 0; DEC v; SET t = v; DIV t, 9; BGCLR lvl; BGON lvl, t; BACK
The levels are the 8 STATES of ONE free background parameter (state t shows
v in [9t, 9t + 9) at ((t + 0.5) * 9) / 64 of the held frame), so one BGCLR
clears them all and the BGON follows it in the same tick. The held frame's
own BGON becomes a jump straight into that body (without IF/DEC, v = 66 ->
state 7, full brightness) so the hand-over from the last ripple frame is in
the tick that switches that frame off, and the restoring LDPAL (palette q is
no longer touched) becomes BGCLR lvl. Section 1 does not change size.

SEVENTH_NX_NO_RIPPLE_FADE=1 disables.
"""
from __future__ import annotations

import collections
import math
import os
import re
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_RIPPLE_FADE'
LEVELS_ENV = 'SEVENTH_NX_RIPPLE_LEVELS'
FIELDS = ('las2_2', 'las2_3')
FX_LO, FX_HI = 0x0F, 0x18          # additive truecolor band (15..23)
GRID = 16
REC = 52
UV_CELL = 625000
MAX_BIND = 256
MAX_PARAM = 63
LEVEL_CHOICES = (8,)                 # the 8 states of one parameter
STATES = 8
V_START = 66
LOOP = re.compile(
    rb'\xe0\x00(.)(.)\x14\x50(.)\x00\x02.\x78\x50\3\x01'
    rb'\xea\x00\x55\x50(.)(.)\3\3\3\x7f\xe7\x00\4\5\x00\xe6\x00\5(.)\x7f'
    rb'\x12\x1e\x24\x01\x00\xe1\x00\1\2\xe6\x00\4\6\x7f\x12.', re.S)


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def forced_levels():
    v = os.environ.get(LEVELS_ENV, '').strip()
    return int(v) if v.isdigit() and 2 <= int(v) <= 32 else None


# ------------------------------------------------------------------ script
def find_loops(sec0):
    out = []
    for m in LOOP.finditer(sec0):
        out.append({'at': m.start(), 'P': m.group(1)[0], 'S': m.group(2)[0],
                    'v': m.group(3)[0], 'A': m.group(4)[0],
                    'B': m.group(5)[0], 'q': m.group(6)[0]})
    return out


def _code_bytes(sec0):
    import field_dis as FD
    m, _n, _e = FD.parse(sec0)
    return m


def patch_script(sec0, loops, levels):
    """loops: [(loop dict, level param)]; levels: (L, D). In place: section 1
    keeps its size and every offset; see the module note (BUILD 619)."""
    import field_dis as FD
    s = bytes(sec0)
    meta, names, entries = FD.parse(s)
    code_end = meta['string_table']
    L, D = levels
    if L > STATES:
        raise ValueError('%d levels do not fit one parameter' % L)
    code = s[meta['code_start']:code_end]
    free = [t for t in range(0xFF, 0x7F, -1) if bytes([t]) not in code]
    if len(free) < len(loops):
        raise ValueError('no free scratch variable')
    body = bytearray(s)
    tmps = []
    for n, (lp, base) in enumerate(loops):
        at = lp['at']
        t = free[n]
        tmps.append(t)
        if not (meta['code_start'] <= at and at + 50 <= code_end):
            raise ValueError('loop outside the code')
        if body[at + 34:at + 36] != b'\x12\x1e' or body[at + 48] != 0x12:
            raise ValueError('loop shape')
        # at+0: the held frame's BGON -> jump into the body (v = 66: top state)
        body[at:at + 4] = bytes([0x10, 13, 0x00, 0x00])
        # at+14: the pass, 15 bytes, then BACK to the IF at at+4
        blk = bytes([0x80, 0x55, t, lp['v'],          # SET t = v
                     0x8B, 0x50, t, D,                # DIV t, D
                     0xE4, 0x00, base,                # BGCLR lvl
                     0xE0, 0x05, base, t,             # BGON lvl, t
                     0x12, (at + 29) - (at + 4)])     # BACK -> IF
        body[at + 14:at + 36] = blk + b'\x00' * (22 - len(blk))
        # at+43: the restoring LDPAL -> BGCLR lvl; SKIP to the next opcode
        body[at + 43:at + 48] = bytes([0xE4, 0x00, base, 0x10, 0x01])
    out = bytes(body)
    m2, n2, e2 = FD.parse(out)
    if n2 != names or e2 != entries or len(out) != len(s) \
            or out[code_end:] != s[code_end:]:
        raise ValueError('re-parse disagrees')
    for (lp, base), t in zip(loops, tmps):
        at = lp['at']
        ops = [i[1] for i in FD.walk(out, at, code_end)[:1]]
        body_ops = [(i[0], i[1]) for i in FD.walk(out, at + 4, code_end)[:7]]
        want = ['if', 'mins!', 'set', 'div', 'bgclr', 'bgon', 'back']
        if ops != ['skip'] or [o for _a, o in body_ops] != want:
            raise ValueError('pass decodes as %s' % [o for _a, o in body_ops])
        if [i[1] for i in FD.walk(out, at + 36, code_end)[:5]] != \
                ['wait', 'bgoff', 'bgclr', 'skip', 'back']:
            raise ValueError('exit decodes wrong')
    return out, tmps


# ------------------------------------------------------------------- tiles
def _rows(sec9):
    import diag_common as DC
    import ff7nx_parallaxfill as PF
    sv = DC.survey(sec9)
    return PF._layers(sec9, sv['back_start'], sv['tex_start'])


def _key(r):
    return (struct.unpack_from('<hh', r, 2), struct.unpack_from('<I', r, 38)[0],
            r[22], r[26], r[27])


def _twins(targets, vsec9):
    vmap = collections.defaultdict(set)
    for _l, _c, first, n in _rows(vsec9):
        for i in range(n):
            r = vsec9[first + i * REC:first + (i + 1) * REC]
            if r[28]:
                sx, sy = struct.unpack_from('<hh', r, 14)
                vmap[_key(r)].add((r[34], sx // 16, sy // 16))
    out = {}
    for _l, _i, r in targets:
        got = vmap.get(_key(r))
        if not got:
            continue                # the build's own 16:9 margin records
        if len(got) != 1:
            raise ValueError('two vanilla twins at %s'
                             % (struct.unpack_from('<hh', r, 2),))
        out[r] = next(iter(got))
    if len(out) < 0.9 * len(targets):
        raise ValueError('only %d of %d records have vanilla twins'
                         % (len(out), len(targets)))
    return out


def _upscaled_1x(sec9, pm, r, pal, k):
    """A record's own 1x paletted cell, through its palette, upscaled -- for
    the build's margin records, which Cosmos has no sheet for."""
    from PIL import Image
    p = pm[r[34]]
    sx, sy = struct.unpack_from('<hh', r, 14)
    a = np.frombuffer(p.data, np.uint8, count=65536).reshape(256, 256)
    idx = a[sy:sy + 16, sx:sx + 16].astype(np.int64)
    c = pal[r[22]][idx]
    rgb = np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31], -1) * 8.0
    rgb[idx == 0] = 0
    im = Image.fromarray(rgb.astype(np.uint8)).resize((k, k), Image.BICUBIC)
    return np.asarray(im).astype(np.float32)


def plan_field(name, parts, vparts, art, mb_cap=35.0, raw_cap=None):
    import diag_common as DC
    import field_bg_native as FN
    import field_bg_pagecap as PC
    import field_bg_repack as FR
    import ff7nx_palstates as PS
    sec0 = bytes(parts[0])
    loops = find_loops(sec0)
    if not loops:
        raise ValueError('no ripple fade loop')
    if len({lp['q'] for lp in loops}) != len(loops):
        raise ValueError('two loops on one palette')
    sec9 = bytes(parts[8])
    pages_l, _ts, _te, px = DC.parse_pages(sec9)
    if px != 768:
        raise ValueError('page size %d' % px)
    pm = {p.slot: p for p in pages_l if p is not None}
    k = px // GRID
    rows = list(_rows(sec9))
    used_params = set()
    targets = collections.defaultdict(list)       # q -> [(layer, i, rec)]
    for layer, _c, first, n in rows:
        for i in range(n):
            r = sec9[first + i * REC:first + (i + 1) * REC]
            used_params.add(r[26])
            if r[28] and any(r[22] == lp['q'] for lp in loops):
                if not FX_LO <= r[34] < FX_HI or r[30] != 1:
                    raise ValueError('palette %d record off the additive '
                                     'band' % r[22])
                p = pm.get(r[34])
                if p is None or p.depth != 1 or p.size_flag:
                    raise ValueError('palette %d record not on a paletted '
                                     'page' % r[22])
                targets[r[22]].append((layer, i, bytes(r)))
    for lp in loops:
        if not targets[lp['q']]:
            raise ValueError('no records on palette %d' % lp['q'])
    # HD art: the brightest Cosmos frame per (vanilla sheet, palette)
    full = {}                           # (q, vpage, cx, cy) -> (k,k,3)
    twins = {}
    for lp in loops:
        q = lp['q']
        tw = _twins(targets[q], vparts[8])
        twins.update(tw)
        byp = collections.defaultdict(set)
        for c in tw.values():
            byp[c[0]].add(c[1:])
        for vpage, cells in byp.items():
            cells = sorted(cells)
            recs = PS._frames(art, name, vpage, q)
            if not recs:
                raise ValueError('no Cosmos frames for sheet %d palette %d'
                                 % (vpage, q))
            fr = [PS._cells_of(art.provider, rc, cells, px) for rc in recs]
            best = fr[int(np.argmax([float(f.mean()) for f in fr]))]
            for j, c in enumerate(cells):
                full[(q, vpage) + c] = best[j]
    free_params = [p for p in range(1, MAX_PARAM + 1) if p not in used_params]
    choices = LEVEL_CHOICES
    last = None
    for L in choices:
        try:
            return _build(name, parts, sec0, sec9, pm, px, k, rows, loops,
                          targets, twins, full, free_params, L, mb_cap,
                          raw_cap, DC, FN, PC, FR, PS)
        except _NoRoom as exc:
            last = exc
    raise ValueError(str(last))


class _NoRoom(ValueError):
    pass


def _build(name, parts, sec0, sec9, pm, px, k, rows, loops, targets, twins,
           full, free_params, L, mb_cap, raw_cap, DC, FN, PC, FR, PS):
    import ff7nx_marginblack as MB
    cols, _hdr, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    D = int(math.ceil(V_START / float(L)))
    bases = list(free_params[:len(loops)])
    if len(bases) < len(loops):
        raise _NoRoom('no %d free params' % len(loops))
    new = []                          # (layer, rec template, param, bit, img)
    gone = set()
    plan_loops = []
    for n_, lp in enumerate(loops):
        q = lp['q']
        base = bases[n_]
        plan_loops.append((lp, base))
        for layer, i, r in targets[q]:
            gone.add((layer, i))
            v = twins.get(r)
            img = full[(q,) + v] if v is not None else \
                _upscaled_1x(sec9, pm, r, pal, k)
            held = r[26] == lp['P'] and r[27] == (1 << lp['S'])
            if not held:
                new.append((layer, r, r[26], r[27], img))
                continue
            for j in range(L):
                f = min(1.0, ((j + 0.5) * D) / 64.0)
                new.append((layer, r, base, 1 << j, img * f))
    new = [t for t in new if float(t[4].max()) >= PS.EMPTY]
    keep_all = []
    for lyr, _c, first, n in rows:
        for i in range(n):
            if (lyr, i) not in gone:
                keep_all.append(sec9[first + i * REC:first + (i + 1) * REC])
    used = collections.defaultdict(set)
    bind = collections.Counter()
    for r in keep_all:
        sx, sy = struct.unpack_from('<hh', r, 10)
        used[r[32]].add((sx // 16, sy // 16))
        if r[28]:
            fx = r[34]
            sx, sy = struct.unpack_from('<hh', r, 14)
            used[fx].add((sx // 16, sy // 16))
            bind[fx if fx in pm else r[32]] += 1
        else:
            bind[r[32]] += 1
    dests = [(s, 'old') for s in range(FX_LO, FX_HI)
             if s in pm and pm[s].depth == 2 and not pm[s].size_flag]
    dests += [(s, 'reuse') for s in range(FX_LO, FX_HI)
              if s in pm and pm[s].depth == 1 and not used[s]]
    dests += [(s, 'new') for s in range(FX_LO, FX_HI) if s not in pm]
    place, kinds = {}, {}
    page_cells = collections.defaultdict(dict)
    page_orig = collections.defaultdict(dict)   # 620j: screen-keyed dither
    todo = list(range(len(new)))
    for s, kind in dests:
        if not todo:
            break
        free = [n for n in range(GRID * GRID)
                if (n % GRID, n // GRID) not in used[s]]
        room = min(len(free), MAX_BIND - bind[s])
        if room <= 0:
            continue
        kinds[s] = kind
        for n in free[:room]:
            if not todo:
                break
            j = todo.pop(0)
            place[j] = (s, n)
            page_cells[s][n] = new[j][4]
            page_orig[s][n] = struct.unpack_from('<hh', new[j][1], 2)
            bind[s] += 1
    if todo:
        raise _NoRoom('%d levels: %d cells do not fit the FX band'
                      % (L, len(todo)))
    layers = {t[0] for t in new} | {g[0] for g in gone}
    if len(layers) != 1:
        raise ValueError('ripples span layers %s' % sorted(layers))
    lyr = layers.pop()
    rowmap = {row[0]: row for row in rows}
    _l, count_at, first, count = rowmap[lyr]
    keep = [sec9[first + i * REC:first + (i + 1) * REC] for i in range(count)
            if (lyr, i) not in gone]
    added = []
    for j, (_l2, r, param, bit, _img) in enumerate(new):
        s_, n = place[j]
        nx, ny = n % GRID, n // GRID
        b_ = bytearray(r)
        b_[26], b_[27] = param, bit
        b_[28], b_[30], b_[34] = 1, 1, s_
        struct.pack_into('<hh', b_, 14, nx * 16, ny * 16)
        struct.pack_into('<II', b_, 42, nx * UV_CELL, ny * UV_CELL)
        added.append(bytes(b_))
    buf = bytearray(sec9)
    buf[first:first + count * REC] = b''.join(keep + added)
    struct.pack_into('<H', buf, count_at, len(keep) + len(added))
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    for s, cells in page_cells.items():
        enc = PS._encode_cells(cells, px, page_orig[s])
        if kinds[s] == 'old':
            cur = np.frombuffer(plist[s].data, '<u2').reshape(px, px).copy()
            for n in cells:
                nx, ny = n % GRID, n // GRID
                sl = (slice(ny * k, (ny + 1) * k), slice(nx * k, (nx + 1) * k))
                cur[sl] = enc[sl]
            enc = cur
        plist[s] = FN.Page(s, 0, 2, enc.astype('<u2').tobytes(), px)
    dropped = []
    for s in range(FX_LO, FX_HI):
        p = plist[s] if s < len(plist) else None
        if p is not None and p.depth == 1 and not used[s] and s not in kinds:
            plist[s] = None
            dropped.append(s)
    run = sum(FR._page_bytes(p.px, p.depth) for p in plist if p is not None)
    if run > mb_cap * 1048576.0:
        raise _NoRoom('%d levels: %.2f MB over the %.1f MB cap'
                      % (L, run / 1048576.0, mb_cap))
    out9 = FN.replace_texture_block(bytes(buf), plist, t0, t1)
    over = {s: v for s, v in PC.effective_counts(out9, px).items()
            if v > MAX_BIND}
    if over:
        raise _NoRoom('binding cap: %s' % over)
    out0, tmp = patch_script(sec0, plan_loops, (L, D))
    other = sum(len(x) for x in parts) - len(parts[8]) - len(parts[0])
    if raw_cap is not None and other + len(out9) + len(out0) > raw_cap:
        raise _NoRoom('raw over cap')
    return out0, out9, {
        'levels': L, 'step': D, 'loops': len(loops), 'tmp': tmp,
        'palettes': [lp['q'] for lp in loops],
        'params': [b for _lp, b in plan_loops],
        'removed': len(gone), 'added': len(new),
        'slots': {s: (kinds[s], len(c)) for s, c in sorted(page_cells.items())},
        'dropped': dropped, 'mb': round(run / 1048576.0, 2)}


def repair_cells(sec9, vsec9):
    """BUILD 619: paletted blend tiles whose cell no longer holds their
    vanilla art get it back.

    The user's capture (las2_2, 10-06): "a weird glowing blob appears on the
    right side by the other ripple". MEASURED: seven palette-13 frames of the
    right-hand ripple (p5 s5/s6/s7, p6 s0 at x 160..240, y 112..128) sample
    page-15 cells whose indices are not their vanilla art (an earlier page
    pass gave those cells to palette-14 ripple frames). Through palette 13
    that art is a flat, bright rectangle. Each such cell whose users ALL want
    the same vanilla 16x16 index block gets that block back in place; a cell
    another tile still uses correctly is left alone (counted, not written).
    Returns (new sec9, repaired cells, refused cells)."""
    import diag_common as DC
    import field_bg_native as FN
    s9 = bytes(sec9)

    def walk(b):
        pl, ts, _te, _px = DC.parse_pages(b)
        pm = {p.slot: p for p in pl if p is not None}
        out = []
        for layer, offs in DC.walk_layers(b, b.find(b'BACK'), ts):
            for o in offs:
                r = b[o:o + REC]
                eff = r[34] if (r[28] and r[34]) else r[32]
                pg = pm.get(eff)
                if pg is None or pg.depth != 1:
                    continue
                u, v = struct.unpack_from('<II', r, 42)
                cx, cy = int(round(u / float(UV_CELL))), int(round(
                    v / float(UV_CELL)))
                a = np.frombuffer(pg.data, np.uint8, count=65536).reshape(
                    256, 256)[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
                out.append((layer, r, eff, cx, cy, a))
        return out, pm
    twins = collections.defaultdict(list)
    for layer, r, _e, _x, _y, a in walk(bytes(vsec9))[0]:
        twins[(layer, _key(r))].append(a.copy())
    recs, pm = walk(s9)
    users = collections.defaultdict(list)
    for layer, r, eff, cx, cy, a in recs:
        users[(eff, cx, cy)].append((layer, r, a))
    fix = {}
    refused = 0
    for cell, us in users.items():
        bad = []
        for layer, r, a in us:
            tw = twins.get((layer, _key(r)))
            if tw is None:
                bad = None                       # a tile with no vanilla twin
                break
            if not any(np.array_equal(a, t) for t in tw):
                bad.append(tw)
        if not bad:
            continue
        if bad is None or len(bad) != len(us):
            refused += 1
            continue
        want = bad[0][0]
        if not all(any(np.array_equal(want, t) for t in tw) for tw in bad):
            refused += 1
            continue
        fix[cell] = want
    if not fix:
        return s9, 0, refused
    plist, t0, t1 = FN.parse_texture_block(s9, DC.parse_pages(s9)[3])
    for (slot, cx, cy), want in fix.items():
        q = plist[slot]
        d = bytearray(q.data)
        for row in range(16):
            o = (cy * 16 + row) * 256 + cx * 16
            d[o:o + 16] = bytes(want[row])
        plist[slot] = FN.Page(slot, q.size_flag, q.depth, bytes(d), q.px)
    return FN.replace_texture_block(s9, plist, t0, t1), len(fix), refused


def apply_to_flevel(archive, payloads, art, encode=None, mb_cap=35.0,
                    raw_cap=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled() or art is None or getattr(art, 'provider', None) is None:
        return st
    encode = encode or archive.encode_field
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            van = archive.decompressed(entry)
            raw = lgp.lzs_decompress(p[4:]) if p else van
            parts = list(lgp.split_sections(raw))
            vparts = list(lgp.split_sections(van))
            parts[0], parts[8], info = plan_field(name, parts, vparts, art,
                                                  mb_cap, raw_cap)
            parts[8], info['repaired'], info['kept'] = repair_cells(
                parts[8], vparts[8])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:110]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append(
            '%s: %d ripples (palettes %s) truecolor, fade as %d levels of '
            '%d steps (params %s); %d records -> %d, pages %s, dropped '
            'paletted %s, %.1f MB; %d paletted cell(s) given back their '
            'vanilla art'
            % (name, info['loops'], info['palettes'], info['levels'],
               info['step'], info['params'], info['removed'], info['added'],
               info['slots'], info['dropped'], info['mb'], info['repaired']))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  RIPPLE FADE (BUILD 619): pond ripples in true colour, '
                   'the palette fade as 8 brightness states of one parameter, '
                   'one 7-opcode pass per tick (%s). %s=1 '
                   'disables.' % (' | '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! ripple fade %s: %s' % (name, why))
    return '\n'.join(out)

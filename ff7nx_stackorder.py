#!/usr/bin/env python3
"""
ff7nx_stackorder.py -- tiles stacked on one spot keep the mod's draw order.
BUILD 608.

THE FAULT (hardware, 2026-09-28: zz2, the weaponsmith's shack)
When the owner opens the chest, a tall rectangle of the CLOSED lid stays on
top of the open chest.

WHY
The chest is two sets of layer-2 tiles at the SAME destination and the SAME
depth: the closed art (param 0) and the open art (param 2, state 2), each
open tile immediately BEFORE its closed twin in the file. At equal depth the
player sees whichever the engine lets win, and that is decided by draw order
alone.

The engine keeps one tile list PER TEXTURE PAGE (`add_page_tile`, x86
0x6464BA, only appends to a per-page list) and draws page by page. The rule,
MEASURED against the hardware report rather than assumed:

    of two tiles at one spot and one depth, the one on the HIGHER page wins;
    on the same page, the one EARLIER in the file wins.

Rendering the built zz2 under that rule puts a tall closed-lid rectangle at
column x=24, rows 120..152 of the open chest -- the report exactly. The
mirror rule (lower page wins) predicts a wide strip along the lid's top edge
instead, which is not what the hardware shows (diag_out/zz2_models.png).

Vanilla keeps both chest twins on one page, so the open tile wins. The dense
repack (field_bg_dense) places CELLS, not stacks; it split five of the
fifteen chest pairs across truecolor pages 26/27, and for three of them the
closed twin's page is the higher one.

MEASURED across the whole built archive (build 606c): 140 fields have stacks
whose winner changed.

THE FIX (only stacks whose winner actually changed are touched)
The reference is the field as the mod ships it, snapshotted before any
background pass (`snapshot`). For each stack whose winners under the rule
above differ from the reference:

  1. REFERENCE ON ONE PAGE -> PUT IT BACK ON ONE PAGE. Every member off the
     target page gets a copy of its own cell in a FREE cell there (no tile
     samples it as a base or an fx frame); its page byte, packed u,v and
     small src bytes are repointed. A member with an fx page needs the same
     free cell index on its fx page too (an fx frame is sampled with the
     base's u,v) and gets its fx cell copied alongside. Within one page the
     order is the file order, which no pass changes -- so this restores the
     mod's winner whatever the cross-page rule is.
  2. OTHERWISE, ON LAYER 2 -> DISTINCT DEPTHS. Layer 2 is depth-tested and a
     tile's z is IDBig / 1e7 read straight from the file (x86 0x62BCB2..
     0x62BCE6). Equal-depth members are pulled NUDGE units apart in the order
     the reference drew them, the reference winner nearest, so it wins under
     LESS and LEQUAL alike whatever page it sits on. The new depths are
     re-checked against the reference before anything is written. Blended
     (byte 28) tiles are never nudged.
  3. Anything else is left as built and counted.

No page is added, resized or renumbered; no pixel another tile samples
changes; the stack's records keep their order in the file.

SEVENTH_NX_NO_STACKORDER=1 disables the pass.
"""
from __future__ import annotations

import collections
import os
import struct

OFF_ENV = 'SEVENTH_NX_NO_STACKORDER'

T_DSTX, T_DSTY = 2, 4
T_SRC_X, T_SRC_Y = 8, 10
T_PARAM, T_STATE = 26, 27
T_BLENDING = 28
T_TEXID, T_FX = 32, 34
T_IDBIG = 38
T_U, T_V = 42, 46
UV_SCALE = 10_000_000
# Depth step between nudged members, in IDBig units (1e-7 of z each). Two
# keeps neighbours more than one float32 ULP apart right up to z = 1.
NUDGE = 2


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _walk(sec9):
    """[(layer, off)] in the file's order, plus the parsed texture block."""
    import diag_common as DC
    pages, tex_start, tex_end, px = DC.parse_pages(sec9)
    out = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), tex_start):
        for o in offs:
            out.append((layer, o))
    return out, pages, tex_start, tex_end, px


def _xy(sec9, o):
    return struct.unpack_from('<hh', sec9, o + T_DSTX)


def _z(sec9, o):
    return struct.unpack_from('<I', sec9, o + T_IDBIG)[0]


def _by_spot(sec9, tiles):
    g = collections.defaultdict(list)
    for layer, o in tiles:
        g[(layer,) + _xy(sec9, o)].append(o)
    return g


def stacks(sec9):
    """{(layer, x, y): [off, ...]} for every destination drawn by 2+ tiles
    at least two of which share a depth."""
    tiles, *_ = _walk(sec9)
    out = {}
    for k, offs in _by_spot(sec9, tiles).items():
        zs = collections.Counter(_z(sec9, o) for o in offs)
        if len(offs) > 1 and any(n > 1 for n in zs.values()):
            out[k] = offs
    return out


def _covisible(a, b):
    """Two members can be drawn together unless they are two states of one
    param (a param shows one state at a time)."""
    return not (a[0] and a[0] == b[0] and a[1] != b[1])


def winners(members):
    """{(i, j): True when i beats j} for co-visible pairs i < j.

    `members`: [(param, state, page, z)] in file order. Nearer (smaller) z
    wins; at equal z the higher page wins, then the earlier tile."""
    out = {}
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            a, b = members[i], members[j]
            if not _covisible(a, b):
                continue
            if a[3] != b[3]:
                out[(i, j)] = a[3] < b[3]
            else:
                out[(i, j)] = (a[2], -i) > (b[2], -j)
    return out


def _members(sec9, offs):
    return [(sec9[o + T_PARAM], sec9[o + T_STATE], sec9[o + T_TEXID],
             _z(sec9, o)) for o in offs]


def signature(sec9):
    """Per stack of the mod's own field: the members' (param, state, page, z)
    in file order."""
    return {k: tuple(_members(sec9, offs)) for k, offs in stacks(sec9).items()}


def snapshot(archive, payloads, decompress):
    """{field: signature} for every field as it stands now. `decompress(name,
    entry)` returns the field's raw bytes (payload first, then archive)."""
    import lgp
    out = {}
    for name in archive.index:
        entry = archive.index[name]
        if not archive.is_field(entry):
            continue
        try:
            raw = decompress(name, entry)
            if raw is None:
                continue
            sig = signature(lgp.split_sections(raw)[8])
        except Exception:                                      # noqa: BLE001
            continue
        if sig:
            out[name] = sig
    return out


def _cell(sec9, o, page):
    grid = 8 if page.size_flag else 16
    u, v = struct.unpack_from('<II', sec9, o + T_U)
    return (int(round(u / UV_SCALE * grid)), int(round(v / UV_SCALE * grid)))


def _band(slot, depth):
    """How the port blends a page, from its slot and depth.

    BUILD 608b. `field_bg_dense._band_of` has no depth-2 group below 0x1A and
    so calls a depth-2 page in 15..23 opaque. It is not: the FINDINGS-194
    module ladder draws depth-2 slots 0x0F..0x17 ADDITIVE (that is how
    ff7nx_fxpages' converted FX pages render). 608 trusted _band_of, found
    fship_3's converted page 15 "opaque with free cells", and moved the two
    table-corner stacks onto it -- opaque art drawn additively over whatever
    was behind, the two patches reported on hardware. Depth-2 0x0F..0x17 is
    its own band now, and 0x18/0x19 (no proven depth-2 behaviour) are each a
    band of one, so nothing can be moved into or out of them."""
    import field_bg_dense as FD
    if depth == 2 and slot < 0x1A:
        if 0x0F <= slot < 0x18:
            return 'd2_additive_fx'
        if slot >= 0x18:
            return ('d2_slot', slot)
    return FD._band_of(slot, depth)


def _kind(p):
    return (p.depth, p.px, p.size_flag, _band(p.slot, p.depth))


def flipped_pairs(members, ref):
    """Co-visible pairs whose winner differs from the reference."""
    want = winners(list(ref))
    have = winners(list(members))
    return [ij for ij, w in want.items() if have.get(ij) != w]


def fix_section9(sec9, ref, name=''):
    """Return (section9, stats). `ref` is `signature()` of the mod's field."""
    import field_bg_native as FN
    st = collections.Counter()
    if not ref:
        return sec9, st
    tiles, plist, tex_start, tex_end, _px = _walk(sec9)
    pages = {p.slot: p for p in plist if p is not None}
    here = _by_spot(sec9, tiles)

    used = collections.defaultdict(set)
    for _layer, o in tiles:
        b, f = sec9[o + T_TEXID], sec9[o + T_FX]
        if b in pages:
            used[b].add(_cell(sec9, o, pages[b]))
        if f and f in pages:
            used[f].add(_cell(sec9, o, pages[f]))

    out = bytearray(sec9)
    data = {}
    changed = False

    def _pix(sl):
        if sl not in data:
            data[sl] = bytearray(pages[sl].data)
        return data[sl]

    def _copy(src_sl, sc, dst_sl, dc):
        dp = pages[dst_sl]
        grid = 8 if dp.size_flag else 16
        side = dp.px if dp.depth == 2 else 256
        step = side // grid
        bpp = 2 if dp.depth == 2 else 1
        src, dst = _pix(src_sl), _pix(dst_sl)
        for r in range(step):
            s0 = ((sc[1] * step + r) * side + sc[0] * step) * bpp
            d0 = ((dc[1] * step + r) * side + dc[0] * step) * bpp
            dst[d0:d0 + step * bpp] = src[s0:s0 + step * bpp]

    def _consolidate(offs):
        slots = {sec9[o + T_TEXID] for o in offs}
        if any(s not in pages for s in slots):
            return 'missing_page'
        kinds = {_kind(pages[s]) for s in slots}
        if len(kinds) != 1:
            return 'mixed_pages'
        kind = next(iter(kinds))
        count = collections.Counter(sec9[o + T_TEXID] for o in offs)
        order = sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))
        order += [(sl, 0) for sl in sorted(pages)
                  if sl not in count and _kind(pages[sl]) == kind]
        for target, _n in order:
            tp = pages[target]
            grid = 8 if tp.size_flag else 16
            movers = [o for o in offs if sec9[o + T_TEXID] != target]
            taken = set()
            plan = []
            for o in movers:
                f = sec9[o + T_FX]
                if f and (f not in pages or f == target
                          or pages[f].size_flag != tp.size_flag):
                    plan = None
                    break
                seat = None
                for cy in range(grid):
                    for cx in range(grid):
                        c = (cx, cy)
                        if c in used[target] or c in taken:
                            continue
                        if f and c in used[f]:
                            continue
                        seat = c
                        break
                    if seat is not None:
                        break
                if seat is None:
                    plan = None
                    break
                taken.add(seat)
                plan.append((o, seat))
            if plan is None:
                continue
            edge = 32 if tp.size_flag else 16
            for o, seat in plan:
                sp = pages[sec9[o + T_TEXID]]
                _copy(sp.slot, _cell(sec9, o, sp), target, seat)
                used[target].add(seat)
                f = sec9[o + T_FX]
                if f:
                    _copy(f, _cell(sec9, o, pages[f]), f, seat)
                    used[f].add(seat)
                out[o + T_TEXID] = target
                out[o + T_SRC_X] = (seat[0] * edge) & 0xFF
                out[o + T_SRC_Y] = (seat[1] * edge) & 0xFF
                struct.pack_into('<II', out, o + T_U,
                                 seat[0] * (UV_SCALE // grid),
                                 seat[1] * (UV_SCALE // grid))
            st['moved_tiles'] += len(plan)
            return None
        return 'no_room'

    def _nudge(key, offs, ref):
        if key[0] != 2:
            return 'not_layer2'
        if any(sec9[o + T_BLENDING] for o in offs):
            return 'blended'
        n = len(offs)
        zs = [_z(sec9, o) for o in offs]
        new = list(zs)
        groups = collections.defaultdict(list)
        for i in range(n):
            groups[zs[i]].append(i)
        for z, idx in groups.items():
            if len(idx) < 2:
                continue
            # Back to front as the reference drew them at this depth: lower
            # page first, and on a page the LATER tile first.
            idx.sort(key=lambda i: (ref[i][2], -i))
            for pos, i in enumerate(idx):
                if z < NUDGE * pos:
                    return 'z_floor'
                new[i] = z - NUDGE * pos
        mem = [(m[0], m[1], m[2], z) for m, z in
               zip(_members(sec9, offs), new)]
        if flipped_pairs(mem, ref):
            return 'nudge_conflict'
        for o, z in zip(offs, new):
            struct.pack_into('<I', out, o + T_IDBIG, z)
        st['nudged_tiles'] += sum(1 for a, b in zip(zs, new) if a != b)
        return None

    for key in sorted(ref):
        r = ref[key]
        offs = here.get(key)
        if not offs:
            continue
        if len(offs) != len(r) or [
                (sec9[o + T_PARAM], sec9[o + T_STATE]) for o in offs] != [
                (m[0], m[1]) for m in r]:
            st['changed_stack'] += 1
            continue
        if not flipped_pairs(_members(sec9, offs), r):
            continue
        st['flipped_stacks'] += 1
        why = None
        if len({m[2] for m in r}) == 1:
            why = _consolidate(offs)
            if why is None:
                st['consolidated'] += 1
                changed = True
                continue
        why2 = _nudge(key, offs, r)
        if why2 is None:
            st['nudged'] += 1
            changed = True
            continue
        st['left_%s' % (why2 if why is None else why + '+' + why2)] += 1

    if not changed:
        return sec9, st
    newlist = list(plist)
    for sl, arr in data.items():
        p = pages[sl]
        newlist[sl] = FN.Page(sl, p.size_flag, p.depth, bytes(arr), p.px)
    result = FN.replace_texture_block(bytes(out), newlist, tex_start, tex_end)
    st['fields'] = 1
    return result, st


def remaining(sec9, ref):
    """(stacks, pairs) still drawn with a different winner than the mod."""
    tiles, *_ = _walk(sec9)
    here = _by_spot(sec9, tiles)
    ns = npairs = 0
    for key, r in ref.items():
        offs = here.get(key)
        if not offs or len(offs) != len(r):
            continue
        f = flipped_pairs(_members(sec9, offs), r)
        if f:
            ns += 1
            npairs += len(f)
    return ns, npairs


def apply_to_flevel(archive, payloads, ref, encode, log=lambda *_: None):
    import lgp
    total = collections.Counter()
    names = []
    if disabled():
        return total, names
    for name in sorted(ref):
        entry = archive.index.get(name)
        if entry is None:
            continue
        blob = payloads.get(name)
        try:
            raw = (lgp.lzs_decompress(blob[4:]) if blob is not None
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            new9, st = fix_section9(parts[8], ref[name], name)
        except Exception as exc:                               # noqa: BLE001
            total['refused_fields'] += 1
            log('  ! stack order: %s left as built -- %s: %s'
                % (name, type(exc).__name__, str(exc)[:80]))
            continue
        total.update(st)
        if st.get('fields'):
            parts[8] = new9
            payloads[name] = encode(lgp.join_sections(parts))
            names.append(name)
    return total, names


def summarise(total, names):
    if not total.get('flipped_stacks'):
        return ''
    line = ('  stack order: %d stack(s) in %d field(s) had a different tile '
            'winning than the mod draws -- %d put back on one page (%d tile(s) '
            'given a copy of their cell), %d given distinct layer-2 depths'
            % (total['flipped_stacks'], total.get('fields', 0),
               total.get('consolidated', 0), total.get('moved_tiles', 0),
               total.get('nudged', 0)))
    left = {k[5:]: v for k, v in total.items() if k.startswith('left_') and v}
    if left:
        line += '; left as built: ' + ', '.join(
            '%s %d' % kv for kv in sorted(left.items()))
    if names:
        line += '\n    e.g. ' + ', '.join(names[:12])
    return line

#!/usr/bin/env python3
"""Put back opaque Cosmos pixels the truecolor background conversion keyed.

BUILD 582. The general form of ``ff7nx_trnaddetail`` (hardware-proven on
trnad_4's rock silhouettes).

The dense conversion keys every native pixel the 1997 page leaves
transparent.  Where Cosmos painted such a pixel opaque, the Switch draws a
hole: at 3x output a square notch or a missing sliver on a silhouette.  The
reports were ghotel's rock at the left 4:3 edge and small pieces of the
machinery in wcrimb_1 / wcrimb_2; an archive scan finds the same pattern in
many fields, concentrated in the two cells either side of the 4:3 picture
edge (x -160 and 144) where the margin passes trim the 1997 outline.

A lost unit (one native 16-unit-cell pixel = 3x3 texels at 768px) is
restored from the SAME Cosmos image FFNx draws for that record (exact
palette, else palette 0) when ALL of these hold:

  * the record is opaque layer-1/2 art (use_fx off) on a 768px depth-2 page,
    and it is the only record that samples that cell;
  * every texel of the unit is keyed on our page and every texel of Cosmos's
    unit is opaque (its alpha >= 128 -- ``PageArt.hmask``);
  * the connected component of lost units touches art that is drawn, and it
    is either small (<= 13 units, trnaddetail's proven bound) or lies in one
    of the two cells on the 4:3 picture edge.

Large transparent regions away from the edge are left alone -- trnad_4
proved some of those are authored.  trnad_4 itself keeps its own pass.
Only page texels change; records, UVs, palettes, layers, slots and sizes are
byte-identical (checked).

SEVENTH_NX_NO_LOST_DETAIL=1 disables the pass.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import ff7nx_fxpages as XP
import ff7nx_trnaddetail as TD

OFF_ENV = 'SEVENTH_NX_NO_LOST_DETAIL'
SKIP_FIELDS = frozenset({TD.TARGET})
TILE = 16
SCALE = 3
PX = TILE * 16 * SCALE           # 768
MAX_COMPONENT = TD.MAX_COMPONENT
EDGE_CELLS = (-160, 144)          # the cells either side of the 4:3 edge
BACKDROP_OFF_ENV = 'SEVENTH_NX_NO_BACKDROP_KEY'


class LostDetailError(Exception):
    pass


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _layers(s):
    sv = DC.survey(s)
    return {layer: list(offs) for layer, offs
            in DC.walk_layers(s, sv['back_start'], sv['tex_start'])}


def _key(s, o):
    return (s[o + 2:o + 6], s[o + 26], s[o + 27])


def plan_section9(name, sec9, cosmos9, provider):
    """[(slot, cx, cy, component, art_block)] and stats. Raises on drift."""
    pl, _ts, _te, px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pl if p}
    lb, lc = _layers(sec9), _layers(cosmos9)
    refs = {}
    for offs in lb.values():
        for o in offs:
            c = TD._cell(sec9, o, pages)
            if c is not None:
                refs[c[:3]] = refs.get(c[:3], 0) + 1
    opened = provider.open(name)
    plans = []
    st = {'units': 0, 'components': 0, 'skipped_large': 0}
    for layer in (1, 2):
        ob, oc = lb.get(layer, []), lc.get(layer, [])
        if len(ob) < len(oc):
            raise LostDetailError('layer %d lost records' % layer)
        for o, q in zip(ob, oc):
            if _key(sec9, o) != _key(cosmos9, q):
                raise LostDetailError('layer %d record order drifted' % layer)
            if (struct.unpack_from('<H', sec9, o + 28)[0]
                    or struct.unpack_from('<H', cosmos9, q + 28)[0]):
                continue
            c = TD._cell(sec9, o, pages)
            if c is None:
                continue
            slot, cx, cy, step = c
            page = pages[slot]
            if (page.depth != 2 or page.px != PX or step != TILE * SCALE
                    or refs.get((slot, cx, cy)) != 1):
                continue
            cslot, cpal = cosmos9[q + 32], cosmos9[q + 22]
            sel = XP._selected_palette(provider, name, cslot, cpal)
            if sel is None:
                continue
            art = opened(cslot, sel)
            if art is None or art.px != PX:
                continue
            sx, sy = cosmos9[q + 10] * SCALE, cosmos9[q + 12] * SCALE
            abuf = np.frombuffer(art.buf, '<u2').reshape(PX, PX)
            art_block = abuf[sy:sy + step, sx:sx + step]
            hm = np.asarray(art.hmask).reshape(PX, PX)[sy:sy + step,
                                                       sx:sx + step]
            block = np.frombuffer(page.data, '<u2').reshape(PX, PX)[
                cy:cy + step, cx:cx + step]
            if art_block.shape != (step, step) or block.shape != (step, step):
                continue
            keyed = (block == FN.EMPTY).reshape(
                TILE, SCALE, TILE, SCALE).all(axis=(1, 3))
            opaque = (hm & (art_block != FN.EMPTY)).reshape(
                TILE, SCALE, TILE, SCALE).all(axis=(1, 3))
            cand = keyed & opaque
            if not cand.any():
                continue
            dx = struct.unpack_from('<h', sec9, o + 2)[0]
            edge = dx in EDGE_CELLS
            for comp in TD._components(cand):
                if not TD._touches_drawn(comp, keyed):
                    continue
                if len(comp) > MAX_COMPONENT and not edge:
                    st['skipped_large'] += len(comp)
                    continue
                plans.append((slot, cx, cy, comp, art_block.copy()))
                st['units'] += len(comp)
                st['components'] += 1
    if not backdrop_disabled():
        try:
            _plan_backdrop(name, sec9, cosmos9, provider, pages, lb, lc,
                           opened, plans, st)
        except LostDetailError as exc:
            st['backdrop_refused'] = str(exc)
    return plans, st


def backdrop_disabled():
    return os.environ.get(BACKDROP_OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _plan_backdrop(name, sec9, cosmos9, provider, pages, lb, lc, opened,
                   plans, st):
    """BUILD 617. Layer 3 -- the backdrop -- gets its keyed holes filled.

    The dense conversion keys every texel the 1997 page leaves at index 0,
    on layer 3 as on the others. Layer 3 is the bottom of the picture (the
    dense repack's PARALLAX ATLAS MARGIN rests on the same fact), so a keyed
    texel there shows the clear colour: trnad_2's sky had a 78-unit grey blob
    and specks where the 1997 sky has index-0 pixels and Cosmos paints
    opaque cloud (hardware video, build 382). No size limit is needed for
    that reason -- nothing drawn behind the backdrop can be revealed -- but
    every other guard stays strict:

      * the field has no layer 4 at all;
      * the cell is sampled by opaque layer-3 records ONLY (no layer 1/2
        record and no FX record anywhere reads it);
      * every Cosmos-aligned record on the cell names the same Cosmos
        pixels (parallax-fill copies of a record agree by construction);
      * a unit is restored only if all its texels are keyed here and all of
        Cosmos's are opaque (alpha >= 128, ``PageArt.hmask``).
    """
    if lb.get(4):
        # Conservative: with a layer 4 in the field, "nothing is behind
        # layer 3" is not proven here, so the field is left alone.
        return
    readers = {}
    for layer, offs in lb.items():
        for o in offs:
            use_fx = sec9[o + 28]
            for sl in ((sec9[o + 32], sec9[o + 34]) if use_fx
                       else (sec9[o + 32],)):
                page = pages.get(sl)
                if page is None:
                    continue
                grid = 8 if page.size_flag else 16
                step = page.px // grid
                if use_fx and sl == sec9[o + 34]:
                    u, v = struct.unpack_from('<II', sec9, o + 42)
                else:
                    u, v = struct.unpack_from('<II', sec9, o + 42)
                key = (sl, int(round(u / TD.UV_SCALE * grid)) * step,
                       int(round(v / TD.UV_SCALE * grid)) * step)
                readers.setdefault(key, set()).add(
                    'fx' if use_fx else layer)
    ob, oc = lb.get(3, []), lc.get(3, [])
    if not ob or not oc:
        return
    if len(ob) < len(oc):
        raise LostDetailError('layer 3 lost records')
    cells = {}
    for o, q in zip(ob, oc):
        if _key(sec9, o) != _key(cosmos9, q):
            raise LostDetailError('layer 3 record order drifted')
        if (struct.unpack_from('<H', sec9, o + 28)[0]
                or struct.unpack_from('<H', cosmos9, q + 28)[0]):
            continue
        c = TD._cell(sec9, o, pages)
        if c is None:
            continue
        slot, cx, cy, step = c
        page = pages[slot]
        if page.depth != 2 or page.px != PX:
            continue
        if readers.get((slot, cx, cy)) != {3}:
            cells[(slot, cx, cy)] = None
            continue
        cslot, cpal = cosmos9[q + 32], cosmos9[q + 22]
        sel = XP._selected_palette(provider, name, cslot, cpal)
        art = opened(cslot, sel) if sel is not None else None
        if art is None or art.px != PX:
            cells[(slot, cx, cy)] = None
            continue
        sx = struct.unpack_from('<H', cosmos9, q + 10)[0] * SCALE
        sy = struct.unpack_from('<H', cosmos9, q + 12)[0] * SCALE
        abuf = np.frombuffer(art.buf, '<u2').reshape(PX, PX)
        blk = abuf[sy:sy + step, sx:sx + step]
        hm = np.asarray(art.hmask).reshape(PX, PX)[sy:sy + step,
                                                   sx:sx + step]
        if blk.shape != (step, step):
            cells[(slot, cx, cy)] = None
            continue
        prev = cells.get((slot, cx, cy), False)
        if prev is None:
            continue
        if prev is not False and not np.array_equal(prev[0], blk):
            cells[(slot, cx, cy)] = None
            continue
        cells[(slot, cx, cy)] = (blk.copy(), hm.copy())
    fixed = {}
    for (slot, cx, cy), got in sorted(cells.items(),
                                      key=lambda kv: kv[0]):
        if not got:
            continue
        blk, hm = got
        step = blk.shape[0]
        n = step // SCALE
        block = np.frombuffer(pages[slot].data, '<u2').reshape(PX, PX)[
            cy:cy + step, cx:cx + step]
        keyed = (block == FN.EMPTY).reshape(n, SCALE, n, SCALE).all(
            axis=(1, 3))
        opaque = (hm & (blk != FN.EMPTY)).reshape(n, SCALE, n, SCALE).all(
            axis=(1, 3))
        cand = keyed & opaque
        if not cand.any():
            continue
        comp = [tuple(p) for p in np.argwhere(cand)]
        plans.append((slot, cx, cy, comp, blk))
        fixed[block.tobytes()] = (comp, blk)
        st['backdrop_units'] = st.get('backdrop_units', 0) + len(comp)
        st['backdrop_cells'] = st.get('backdrop_cells', 0) + 1
    # Widescreen/parallax fill tiles sample COPIES of those cells, often on
    # another page (ff7nx_parallaxwide). They have no Cosmos-aligned record,
    # so a copy is repaired only when it is byte-identical to a cell repaired
    # above and is itself read only by opaque layer-3 records.
    if not fixed:
        return
    for (slot, cx, cy), who in sorted(readers.items(), key=lambda kv: kv[0]):
        if who != {3} or (slot, cx, cy) in cells:
            continue
        page = pages.get(slot)
        if page is None or page.depth != 2 or page.px != PX:
            continue
        for comp, blk in fixed.values():
            step = blk.shape[0]
            block = np.frombuffer(page.data, '<u2').reshape(PX, PX)[
                cy:cy + step, cx:cx + step]
            if block.shape == blk.shape and block.tobytes() in fixed \
                    and fixed[block.tobytes()][1] is blk:
                plans.append((slot, cx, cy, comp, blk))
                st['backdrop_units'] = st.get('backdrop_units', 0) + len(comp)
                st['backdrop_copies'] = st.get('backdrop_copies', 0) + 1
                break


def apply_plans(sec9, plans):
    plist, tex_start, tex_end = FN.parse_texture_block(sec9, PX)
    pages = {p.slot: p for p in plist if p is not None}
    arrays = {}
    for slot, cx, cy, comp, art_block in plans:
        if slot not in arrays:
            p = pages[slot]
            arrays[slot] = np.frombuffer(p.data, '<u2').reshape(
                p.px, p.px).copy()
        span = art_block.shape[0]
        dst = arrays[slot][cy:cy + span, cx:cx + span]
        for uy, ux in comp:
            ys = slice(uy * SCALE, (uy + 1) * SCALE)
            xs = slice(ux * SCALE, (ux + 1) * SCALE)
            if not np.all(dst[ys, xs] == FN.EMPTY):
                raise LostDetailError('destination no longer empty')
            src = art_block[ys, xs] & np.uint16(0xFFDF)
            src[src == FN.EMPTY] = 0x0020        # never write the key
            dst[ys, xs] = src
    before = sec9[:tex_start] + sec9[tex_end:]
    for slot, arr in arrays.items():
        p = pages[slot]
        plist[slot] = FN.Page(slot, p.size_flag, p.depth, arr.tobytes(), p.px)
    out = FN.replace_texture_block(sec9, plist, tex_start, tex_end)
    _p, s2, e2 = FN.parse_texture_block(out, PX)
    if out[:s2] + out[e2:] != before or len(out) != len(sec9):
        raise LostDetailError('non-texture bytes changed')
    return out, len(arrays)


def _cosmos_chunk9(provider, name):
    """Cosmos's own section 9 for the field, read from the same .iro and
    the same active option folders the art provider indexed."""
    import field_bg_repack as FR
    prefixes = {}
    for path, entry in provider.slots.values():
        k = entry.lower().replace('\\', '/')
        if '/field/' in k:
            prefixes.setdefault(path, set()).add(k.split('/field/')[0])
    want = '/flevel.lgp/%s.chunk.9' % name.lower()
    for path, folders in prefixes.items():
        reader = provider.readers.get(path)
        if reader is None:
            reader = provider.readers[path] = FR.IroReader(path)
        for key in reader.by_name:
            k = key.lower().replace('\\', '/')
            if k.endswith(want) and k[:-len(want)] in folders:
                blob = reader.read(key)
                if blob:
                    return blob
    return None


def apply_to_flevel(archive, payloads, art, cosmos_chunk=None, encode=None,
                    log=lambda *_: None):
    import lgp
    total = {'fields': 0, 'pages': 0, 'units': 0, 'components': 0,
             'skipped_large': 0, 'names': [], 'refused': []}
    if disabled():
        return total
    provider = getattr(art, 'provider', None)
    if provider is None:
        return total
    encode = encode or archive.encode_field
    for name in archive.names():
        if name.lower() in SKIP_FIELDS:
            continue
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            c9 = (cosmos_chunk(name) if cosmos_chunk is not None
                  else _cosmos_chunk9(provider, name))
            if not c9:
                continue
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            plans, st = plan_section9(name, parts[8], c9, provider)
            if not plans:
                continue
            parts[8], npages = apply_plans(parts[8], plans)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append(
                (name, '%s: %s' % (type(exc).__name__, str(exc)[:60])))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['fields'] += 1
        total['pages'] += npages
        total['units'] += st['units']
        total['backdrop_units'] = total.get('backdrop_units', 0) + st.get(
            'backdrop_units', 0)
        if st.get('backdrop_units'):
            total.setdefault('backdrop_names', []).append(
                '%s:%d' % (name, st['backdrop_units']))
        total['components'] += st['components']
        total['skipped_large'] += st['skipped_large']
        total['names'].append('%s:%d' % (name, st['units']))
    return total


def summarise(st):
    if not st.get('fields') and not st.get('refused') \
            and not st.get('backdrop_units'):
        return ''
    line = ('  lost Cosmos detail restored: %d unit(s) in %d component(s), '
            '%d field(s) [%s]; %d unit(s) in large regions off the 4:3 edge '
            'left alone (%s=1 disables)'
            % (st['units'], st['components'], st['fields'],
               ', '.join(st['names'][:30]), st['skipped_large'], OFF_ENV))
    if st.get('backdrop_units'):
        line += ('\n  BACKDROP KEY (BUILD 617): %d keyed layer-3 unit(s) '
                 'that Cosmos paints opaque were filled with its art (%s) -- '
                 'layer 3 is the bottom of the picture, so a keyed texel '
                 'there is a hole showing the clear colour (trnad_2 sky). '
                 'Cells read only by opaque layer-3 records. %s=1 disables.'
                 % (st['backdrop_units'],
                    ', '.join(st.get('backdrop_names', [])[:12]),
                    BACKDROP_OFF_ENV))
    if st.get('refused'):
        line += ('\n  ! lost detail: %d field(s) unchanged (%s)'
                 % (len(st['refused']), ', '.join(
                     '%s: %s' % r for r in st['refused'][:4])))
    return line

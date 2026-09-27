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
    return plans, st


def apply_plans(sec9, plans):
    plist, tex_start, tex_end = FN.parse_texture_block(sec9, PX)
    pages = {p.slot: p for p in plist if p is not None}
    arrays = {}
    for slot, cx, cy, comp, art_block in plans:
        if slot not in arrays:
            p = pages[slot]
            arrays[slot] = np.frombuffer(p.data, '<u2').reshape(
                p.px, p.px).copy()
        dst = arrays[slot][cy:cy + TILE * SCALE, cx:cx + TILE * SCALE]
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
        total['components'] += st['components']
        total['skipped_large'] += st['skipped_large']
        total['names'].append('%s:%d' % (name, st['units']))
    return total


def summarise(st):
    if not st.get('fields') and not st.get('refused'):
        return ''
    line = ('  lost Cosmos detail restored: %d unit(s) in %d component(s), '
            '%d field(s) [%s]; %d unit(s) in large regions off the 4:3 edge '
            'left alone (%s=1 disables)'
            % (st['units'], st['components'], st['fields'],
               ', '.join(st['names'][:30]), st['skipped_large'], OFF_ENV))
    if st.get('refused'):
        line += ('\n  ! lost detail: %d field(s) unchanged (%s)'
                 % (len(st['refused']), ', '.join(
                     '%s: %s' % r for r in st['refused'][:4])))
    return line

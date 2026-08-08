"""Does the entry-0 de-fringe reach palettes drawn on ADDITIVE/AVERAGE pages?

ff7nx_palkey.blacken_keys uses `used = range(npg)` -- EVERY palette page of any
field that has margin tiles, not only the ones margin tiles name. So the set it
can rewrite is bounded only by which palettes draw an index-0 pixel. This counts
how many of those are drawn on a depth-1 page in the additive or average blend
band, where black is the identity element and a non-black key ADDS light.
"""
import struct, collections
import field_bg_native as FN, field_bg_compact as FC, lgp
import ff7nx_palkey as PK, ff7nx_marginblack as MB, diag_common as DC

arc = lgp.Archive('dump/romfs/ff7/workingdir/data/field/flevel.lgp')
tot = collections.Counter(); worst = []
for nm in sorted(arc.index):
    e = arc.index[nm]
    if not arc.is_field(e): continue
    try:
        raw = arc.decompressed(e); parts = lgp.split_sections(raw); sec9 = parts[8]
        pages, ts, te = FN.parse_texture_block(sec9, FN.VANILLA_PX)
        hdr, npg, cpp = MB.palette_block(parts[3])
        if not npg or not cpp: continue
    except Exception:
        continue
    pmap = {p.slot: p for p in pages if p is not None}
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), ts)
    band = collections.defaultdict(set); keyed = set()
    arrs = {}
    for off in spans:
        slot = sec9[off + FC.T_TEXID]; p = pmap.get(slot)
        if p is None or p.depth != 1: continue
        pal = sec9[off + 22]
        band[pal].add('opaque' if slot < 15 else
                      'additive' if slot < 24 else 'average')
    if not band: continue
    tot['fields'] += 1
    cols = struct.unpack_from('<%dH' % (cpp*npg), parts[3], hdr)
    for pal, bs in band.items():
        if pal >= npg: continue
        blend = bool(bs - {'opaque'})
        e0 = cols[pal*cpp]
        tot['pal'] += 1
        if blend:
            tot['pal_blend'] += 1
            if e0 & 0x7FFF: tot['pal_blend_e0_nonblack'] += 1
    nb = sum(1 for pal, bs in band.items()
             if pal < npg and (bs - {'opaque'}))
    if nb: worst.append((nm, nb, len(band)))
print()
print('  entry-0 de-fringe vs BLEND BAND -- vanilla flevel, fields palkey touches')
print()
print(f"  fields                                          {tot['fields']:>6,}")
print(f"  (field, palette) pairs on depth-1 pages         {tot['pal']:>6,}")
print(f"  ...drawn on an ADDITIVE or AVERAGE page         {tot['pal_blend']:>6,}")
print(f"  ...of those, entry 0 ALREADY non-black in vanilla {tot['pal_blend_e0_nonblack']:>4,}")
print()
print('  palkey rewrites entry 0 for EVERY palette of these fields (range(npg)),')
print('  so every one of the above is in range of it.')
print()
for nm, nb, n in sorted(worst, key=lambda r: -r[1])[:15]:
    print(f'    {nm:<12} {nb:>3} of {n} palettes drawn on a blend page')

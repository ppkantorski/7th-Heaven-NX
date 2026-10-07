#!/usr/bin/env python3
"""ff7nx_artpatch.py -- the user's own retouches of field art. BUILD 618z10 (618z12: green LSB).

mds7pb_1 (hardware 10-06): "the L shaped edge always looked out of place,
and the texture creates a peaking triangular edge along the side of the
wood" -- the bright L beside the TV's right side and the shelf edge below
it, in layer 1 (Cosmos's art).

WHAT IS SHIPPED: A PATCH, NOT A PICTURE. The asset holds no image at all.
It holds, for the pixels the user changed only:
  * where they are (a mask over the region);
  * the XOR of the game's 16-bit texel there with the user's result
    (patched = installed XOR delta), the way ROM-hack patches (IPS/xdelta)
    are shipped;
  * a SHA-1 of the installed texels under the mask.
The delta is meaningless without the art it was made against; it cannot be
viewed or used as artwork. At build time the patch is applied only if the
installed texels hash to exactly the same SHA-1, so the result is the
user's edit bit for bit, or nothing (logged).

    python3 ff7nx_artpatch.py export mds7pb_1 out.png [flevel.lgp]
    python3 ff7nx_artpatch.py make mds7pb_1 original.png edited.png [flevel]

`export` writes the layer-1 region (from the user's own install) to edit;
`make` writes assets/artpatch/<field>.npz. A cell shared with another place
is copied to a free truecolor cell first, so only this place changes.
SEVENTH_NX_NO_ART_PATCH=1 disables.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import struct
import sys

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_ART_PATCH'
HERE = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(HERE, 'assets', 'artpatch')
K = 3                                   # px per field unit (768 pages)
# field -> (x0, y0, w, h) in field units, layer 1
REGIONS = {'mds7pb_1': (-224, -112, 128, 128),
           # BUILD 620: the whole layer 1 of the Jenova-capsule room (the
           # user: "the staircase upscaled texture is a freaking mess, i
           # think i can create a better patch for it"). nvmkin21 and
           # nvmkin22 share that layer's art (only nvmkin21's one animated
           # layer-1 tile differs, and animated tiles are not part of the
           # export), so one edit is made once and applied to both.
           'nvmkin21': (-160, -144, 256, 400),
           'nvmkin22': (-160, -144, 256, 400)}
# One edit, made for the first field, also patches these (each gets its own
# asset: nvmkin22's copies of some cells differ from nvmkin21's by one 5-bit
# level, so a shared SHA would refuse it; the edited pixels get the same
# final colour in both).
ALSO = {'nvmkin21': ('nvmkin22',)}
ASSET_OF = {}
# BUILD 620b. 'stairs' (the user: "all i care about is the full staircase,
# not the overlapping stuff that is added to the field"): layer 1, plus a
# layer-2 tile only where layer 1 has NO tile (that art is the staircase's
# own -- the top flight by the door and the bottom steps), none of the
# layer-2 overlays (capsules, pipes, rails) on top of layer 1. 'composite'
# is the whole field as drawn. Composite, for reference: the export is the field AS DRAWN (every static
# tile of layers 1 and 2, in draw order: layer 1, then layer 2 far to near,
# a layer-2 texel 0 transparent), so there are no holes where the art lives
# in the other layer; an edited pixel is written into whichever tile draws
# it. Animated tiles (a background parameter) are not part of it -- their
# spots stay black (nvmkin21/22: the door at the top of the stairs and one
# light). The user (10-06): "all of the field images for nvkim are missing
# squares ... are you sure thats the image i should be modifying".
MODES = {'nvmkin21': 'composite', 'nvmkin22': 'composite'}
# 620c: the user saw 'stairs' squares that do not match ("is there supposed
# to be garbage squares"): at those spots the game draws the stairs from
# layer 1 AND layer 2 together plus the door's animated tiles, so taking only
# some of them is patchwork. The export is now the stair area exactly as the
# game draws it at load: every static tile of both layers and the animated
# tiles of the field's own init state (nvmkin21/22's door entity: BGON 1,0;
# BGCLR 2 -> the closed door).
STATES = {'nvmkin21': {1: 1}, 'nvmkin22': {1: 1}}
CHANGED = 6                             # 0..255: a pixel counts as edited
FEATHER = 4                             # px: softer changes next to edits
MATCH_TOL = 3.0                         # export vs flevel at `make` time
UV_CELL = 625000
FX_BAND = (15, 26)


def _src(b, o):
    """(sx, sy) of the cell a record DRAWS: from its UV (+42), which is what
    the engine reads. BUILD 620c: 21 of nvmkin21's records carry a stale
    source x/y at +10 (the export showed the wrong cells there: the user's
    "garbage squares")."""
    u, v = struct.unpack_from('<II', b, o + 42)
    return (int(round(u / float(UV_CELL))) * 16,
            int(round(v / float(UV_CELL))) * 16)


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rgb(v):
    """As the engine shows a depth-2 texel: 5 bits each (x86 0x63F350 keeps
    the top 5 of green's 6), on the level*8 grid."""
    v = v.astype(np.uint32)
    return np.stack([((v >> 11) & 31) << 3, ((v >> 6) & 31) << 3,
                     (v & 31) << 3], -1).astype(np.uint8)


def _enc(rgb):
    """R5G6B5 with green's LOW BIT CLEAR (BUILD 618z12). The port converts
    depth-2 texels with ((in & 0x07E0) >> 1), so an odd green lands on
    blue's top bit: +16/31 blue on a quarter of 618z10's patched texels --
    the blue specks the user saw (field_bg_native, "THE GREEN LSB MUST BE
    ZERO"). Rounded onto the level*8 grid like field_bg_native.rgb_to_565;
    0 (transparent) becomes 0x0001."""
    c = rgb.astype(np.float64)
    q = lambda x: np.clip((x * 0.125 + 0.5).astype(np.int64), 0, 31)
    v = (q(c[..., 0]) << 11) | ((q(c[..., 1]) << 1) << 5) | q(c[..., 2])
    v = np.where(v == 0, 1, v)
    return v.astype(np.uint16)


def _layer1(sec9, region):
    """(raw 565 texels of the region, records by position, page map, px,
    cell users, used cells, binds)."""
    import diag_common as DC
    x0, y0, w, h = region
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    if k != 16 * K:
        raise ValueError('pages are %d px' % px)
    pos = collections.defaultdict(list)
    users = collections.Counter()
    used = collections.defaultdict(set)
    binds = collections.Counter()
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            pg = sec9[o + 34] if bl else sec9[o + 32]
            sx, sy = _src(sec9, o)
            users[(pg, sx // 16, sy // 16)] += 1
            used[pg].add((sx // 16, sy // 16))
            binds[pg] += 1
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            if (layer == 1 and not bl and not sec9[o + 26]
                    and x0 <= x < x0 + w and y0 <= y < y0 + h):
                pos[(x, y)].append(o)
    raw = np.zeros((h * K, w * K), np.uint16)
    for (x, y), offs in pos.items():
        if len(offs) != 1:
            raise ValueError('two layer-1 records at %d,%d' % (x, y))
        o = offs[0]
        p = pm[sec9[o + 32]]
        if p.depth != 2:
            raise ValueError('layer-1 page %d is paletted' % p.slot)
        sx, sy = _src(sec9, o)
        Y, X = (y - y0) * K, (x - x0) * K
        raw[Y:Y + k, X:X + k] = np.frombuffer(p.data, '<u2').reshape(
            px, px)[sy // 16 * k:sy // 16 * k + k,
                    sx // 16 * k:sx // 16 * k + k]
    return raw, pos, pm, px, users, used, binds



def _composite(sec9, region, fill_only=False, states=None):
    """(drawn 565 texels, owner record index per px (-1 none), local cell
    y/x per px, record list [(o, layer)], page map, px, users, used, binds)"""
    import diag_common as DC
    x0, y0, w, h = region
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    if k != 16 * K:
        raise ValueError('pages are %d px' % px)
    users = collections.Counter()
    used = collections.defaultdict(set)
    binds = collections.Counter()
    recs = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            pg = sec9[o + 34] if bl else sec9[o + 32]
            sx, sy = _src(sec9, o)
            users[(pg, sx // 16, sy // 16)] += 1
            used[pg].add((sx // 16, sy // 16))
            binds[pg] += 1
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            par = sec9[o + 26]
            on = not par or bool((states or {}).get(par, 0) & sec9[o + 27])
            if (layer in (1, 2) and not bl and on
                    and x0 <= x < x0 + w and y0 <= y < y0 + h
                    ):
                tn = 16 if layer == 1 else (
                    max(struct.unpack_from('<HH', sec9, o + 18)) or 16)
                if tn != 16:
                    raise ValueError('a %d-unit tile at %d,%d' % (tn, x, y))
                z = struct.unpack_from('<I', sec9, o + 38)[0]
                recs.append((layer, -z if layer == 2 else 0, o))
    if fill_only:
        # only the layer-2 tiles where layer 1 has none: the art layer 1 is
        # missing there, not something drawn over it
        l1 = {struct.unpack_from('<hh', sec9, o + 2)
              for layer, _z, o in recs if layer == 1}
        recs = [r for r in recs if r[0] == 1
                or struct.unpack_from('<hh', sec9, r[2] + 2) not in l1]
    recs.sort()
    raw = np.zeros((h * K, w * K), np.uint16)
    own = np.full((h * K, w * K), -1, np.int32)
    for i, (layer, _z, o) in enumerate(recs):
        p = pm[sec9[o + 32]]
        if p.depth != 2:
            raise ValueError('layer-%d page %d is paletted' % (layer, p.slot))
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        sx, sy = _src(sec9, o)
        t = np.frombuffer(p.data, '<u2').reshape(px, px)[
            sy // 16 * k:sy // 16 * k + k, sx // 16 * k:sx // 16 * k + k]
        Y, X = (y - y0) * K, (x - x0) * K
        m = np.ones_like(t, bool) if layer == 1 else (t != 0)
        raw[Y:Y + k, X:X + k][m] = t[m]
        own[Y:Y + k, X:X + k][m] = i
    return raw, own, [(o, l) for l, _z, o in recs], pm, px, users, used, \
        binds

def _sha(raw, mask):
    return hashlib.sha1(raw[mask].astype('<u2').tobytes()
                        + np.packbits(mask).tobytes()).hexdigest()


def _drawn(sec9, name):
    if MODES.get(name) in ('composite', 'stairs'):
        return _composite(sec9, REGIONS[name],
                          MODES[name] == 'stairs', STATES.get(name))[0]
    return _layer1(sec9, REGIONS[name])[0]


def export(parts, name, out):
    from PIL import Image
    raw = _drawn(bytes(parts[8]), name)
    Image.fromarray(_rgb(raw)).save(out)
    return raw.shape


def make(parts, name, original, edited, dest=None):
    """The patch: mask + XOR delta + SHA-1 of the texels it applies to."""
    from PIL import Image
    from scipy import ndimage as ND
    org = np.asarray(Image.open(original).convert('RGB')).astype(int)
    ed = np.asarray(Image.open(edited).convert('RGB')).astype(int)
    if org.shape != ed.shape:
        raise ValueError('edited image is %s, original %s'
                         % (ed.shape, org.shape))
    raw = _drawn(bytes(parts[8]), name)
    if np.abs(_rgb(raw).astype(int) - org).mean() > MATCH_TOL:
        raise ValueError('the original does not match this flevel')
    d = np.abs(ed - org).max(-1)
    # the edits, plus their soft (feathered) edge
    mask = (d > 0) & ND.binary_dilation(d > CHANGED, iterations=FEATHER)
    target = _enc(ed.astype(np.uint8))
    mask &= target != raw                     # nothing to do where equal
    if MODES.get(name) in ('composite', 'stairs'):
        own = _composite(bytes(parts[8]), REGIONS[name],
                         MODES[name] == 'stairs', STATES.get(name))[1]
        mask &= own >= 0                      # an animated tile's spot
    delta = (raw[mask] ^ target[mask]).astype('<u2')
    dest = dest or os.path.join(ASSET_DIR, name + '.npz')
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    np.savez_compressed(
        dest, mask=np.packbits(mask), shape=np.array(mask.shape),
        delta=delta, region=np.array(REGIONS[name]),
        sha1=np.frombuffer(_sha(raw, mask).encode(), np.uint8))
    return int(mask.sum()), dest


def make_also(parts, name, original, edited, master_parts, master,
              dest=None):
    """`edited` was made on `master`'s export; patch `name` so the pixels the
    user changed there get exactly the same colours here."""
    from PIL import Image
    from scipy import ndimage as ND
    org = np.asarray(Image.open(original).convert('RGB')).astype(int)
    ed = np.asarray(Image.open(edited).convert('RGB')).astype(int)
    if REGIONS[name] != REGIONS[master]:
        raise ValueError('%s and %s have different regions' % (name, master))
    mraw = _drawn(bytes(master_parts[8]), master)
    if np.abs(_rgb(mraw).astype(int) - org).mean() > MATCH_TOL:
        raise ValueError('the original does not match %s' % master)
    raw = _drawn(bytes(parts[8]), name)
    d = np.abs(ed - org).max(-1)
    mask = (d > 0) & ND.binary_dilation(d > CHANGED, iterations=FEATHER)
    target = _enc(ed.astype(np.uint8))
    mask &= target != raw
    if MODES.get(name) in ('composite', 'stairs'):
        mask &= _composite(bytes(parts[8]), REGIONS[name],
                           MODES[name] == 'stairs', STATES.get(name))[1] >= 0
    delta = (raw[mask] ^ target[mask]).astype('<u2')
    dest = dest or os.path.join(ASSET_DIR, name + '.npz')
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    np.savez_compressed(
        dest, mask=np.packbits(mask), shape=np.array(mask.shape),
        delta=delta, region=np.array(REGIONS[name]),
        sha1=np.frombuffer(_sha(raw, mask).encode(), np.uint8))
    return int(mask.sum()), dest


def load(name, path=None):
    path = path or os.path.join(ASSET_DIR, ASSET_OF.get(name, name) + '.npz')
    if not os.path.exists(path):
        return None
    z = np.load(path)
    shape = tuple(int(v) for v in z['shape'])
    mask = np.unpackbits(z['mask'])[:shape[0] * shape[1]].reshape(
        shape).astype(bool)
    return {'mask': mask, 'delta': z['delta'].astype(np.uint16),
            'region': tuple(int(v) for v in z['region']),
            'sha1': bytes(z['sha1']).decode()}


def plan_field(name, parts, patch):
    """(new section 9, info) -- raises on any doubt."""
    if MODES.get(name) in ('composite', 'stairs'):
        return _plan_composite(name, parts, patch)
    import field_bg_native as FN
    region = REGIONS[name]
    if patch['region'] != tuple(region):
        raise ValueError('patch region %s, expected %s'
                         % (patch['region'], region))
    sec9 = bytes(parts[8])
    raw, pos, pm, px, users, used, binds = _layer1(sec9, region)
    mask = patch['mask']
    if mask.shape != raw.shape or int(mask.sum()) != len(patch['delta']):
        raise ValueError('patch is %s, region %s' % (mask.shape, raw.shape))
    if not mask.any():
        raise ValueError('empty patch')
    if _sha(raw, mask) != patch['sha1']:
        raise ValueError('the installed art under the patch is not the art '
                         'it was made against')
    out = raw.copy()
    out[mask] = raw[mask] ^ patch['delta']
    if ((out[mask] >> 5) & 1).any() or (out[mask] == 0).any():
        raise ValueError('patch writes texels with an odd green (blue specks '
                         'on the port) or transparent ones -- remake it')
    x0, y0 = region[:2]
    k = px // 16
    datas = {}
    buf = bytearray(sec9)
    cells = 0
    free = None
    for (x, y), (o,) in sorted(pos.items()):
        Y, X = (y - y0) * K, (x - x0) * K
        if not mask[Y:Y + k, X:X + k].any():
            continue
        pg = sec9[o + 32]
        sx, sy = _src(sec9, o)
        if users[(pg, sx // 16, sy // 16)] > 1:
            if free is None:
                free = [(s, cx, cy) for s in sorted(pm)
                        if pm[s].depth == 2 and pm[s].px == px
                        and not FX_BAND[0] <= s < FX_BAND[1]
                        for cy in range(16) for cx in range(16)
                        if (cx, cy) not in used[s]]
            j = next((i for i, (s, _cx, _cy) in enumerate(free)
                      if binds[s] < 256), None)
            if j is None:
                raise ValueError('no free truecolor cell for a shared one')
            s, cx, cy = free.pop(j)
            binds[s] += 1
            struct.pack_into('<hh', buf, o + 10, cx * 16, cy * 16)
            struct.pack_into('<II', buf, o + 42, cx * UV_CELL, cy * UV_CELL)
            buf[o + 32] = s
            pg, sx, sy = s, cx * 16, cy * 16
        if pg not in datas:
            datas[pg] = np.frombuffer(pm[pg].data, '<u2').reshape(
                px, px).copy()
        datas[pg][sy // 16 * k:sy // 16 * k + k,
                  sx // 16 * k:sx // 16 * k + k] = out[Y:Y + k, X:X + k]
        cells += 1
    s9n = bytes(buf)
    plist, t0, t1 = FN.parse_texture_block(s9n, px)
    for pg, d in datas.items():
        q = plist[pg]
        plist[pg] = FN.Page(pg, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(s9n, plist, t0, t1), {
        'cells': cells, 'pixels': int(mask.sum())}


def _plan_composite(name, parts, patch):
    """Every edited pixel goes into the tile that draws it (BUILD 620b)."""
    import field_bg_native as FN
    region = REGIONS[name]
    if patch['region'] != tuple(region):
        raise ValueError('patch region %s, expected %s'
                         % (patch['region'], region))
    sec9 = bytes(parts[8])
    raw, own, recs, pm, px, users, used, binds = _composite(
        sec9, region, MODES[name] == 'stairs', STATES.get(name))
    mask = patch['mask']
    if mask.shape != raw.shape or int(mask.sum()) != len(patch['delta']):
        raise ValueError('patch is %s, region %s' % (mask.shape, raw.shape))
    if not mask.any():
        raise ValueError('empty patch')
    if (own[mask] < 0).any():
        raise ValueError('patch covers a spot no static tile draws')
    if _sha(raw, mask) != patch['sha1']:
        raise ValueError('the installed art under the patch is not the art '
                         'it was made against')
    out = raw.copy()
    out[mask] = raw[mask] ^ patch['delta']
    if ((out[mask] >> 5) & 1).any() or (out[mask] == 0).any():
        raise ValueError('patch writes texels with an odd green (blue specks '
                         'on the port) or transparent ones -- remake it')
    x0, y0 = region[:2]
    k = px // 16
    datas = {}
    buf = bytearray(sec9)
    free = None
    cells = 0
    for i in sorted(set(own[mask].tolist())):
        o, _layer = recs[i]
        sel = mask & (own == i)
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        Y, X = (y - y0) * K, (x - x0) * K
        pg = buf[o + 32]
        sx, sy = _src(buf, o)
        if users[(pg, sx // 16, sy // 16)] > 1:
            if free is None:
                free = [(s, cx, cy) for s in sorted(pm)
                        if pm[s].depth == 2 and pm[s].px == px
                        and not FX_BAND[0] <= s < FX_BAND[1]
                        for cy in range(16) for cx in range(16)
                        if (cx, cy) not in used[s]]
            j = next((n for n, (s, _cx, _cy) in enumerate(free)
                      if binds[s] < 256), None)
            if j is None:
                raise ValueError('no free truecolor cell for a shared one')
            s, cx, cy = free.pop(j)
            binds[s] += 1
            if s not in datas:
                datas[s] = np.frombuffer(pm[s].data, '<u2').reshape(
                    px, px).copy()
            if pg not in datas:
                datas[pg] = np.frombuffer(pm[pg].data, '<u2').reshape(
                    px, px).copy()
            datas[s][cy * k:cy * k + k, cx * k:cx * k + k] = \
                datas[pg][sy // 16 * k:sy // 16 * k + k,
                          sx // 16 * k:sx // 16 * k + k]
            users[(pg, sx // 16, sy // 16)] -= 1
            struct.pack_into('<hh', buf, o + 10, cx * 16, cy * 16)
            struct.pack_into('<II', buf, o + 42, cx * UV_CELL, cy * UV_CELL)
            buf[o + 32] = s
            pg, sx, sy = s, cx * 16, cy * 16
        if pg not in datas:
            datas[pg] = np.frombuffer(pm[pg].data, '<u2').reshape(
                px, px).copy()
        blk = datas[pg][sy // 16 * k:sy // 16 * k + k,
                        sx // 16 * k:sx // 16 * k + k]
        m = sel[Y:Y + k, X:X + k]
        blk[m] = out[Y:Y + k, X:X + k][m]
        cells += 1
    s9n = bytes(buf)
    plist, t0, t1 = FN.parse_texture_block(s9n, px)
    for pg, d in datas.items():
        q = plist[pg]
        plist[pg] = FN.Page(pg, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(s9n, plist, t0, t1), {
        'cells': cells, 'pixels': int(mask.sum())}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in REGIONS:
        try:
            patch = load(name)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, 'asset: %s' % str(exc)[:80]))
            continue
        if patch is None:
            continue
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], info = plan_field(name, parts, patch)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s: %d px in %d cells' % (name, info['pixels'],
                                                      info['cells']))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  ART PATCH (BUILD 618z12): the user\'s own retouches, '
                   'applied as XOR patches against the installed art (%s). '
                   '%s=1 disables.' % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! art patch %s: not applied -- %s' % (name, why))
    return '\n'.join(out)


if __name__ == '__main__':
    import lgp
    if len(sys.argv) >= 4 and sys.argv[1] in ('export', 'make'):
        name = sys.argv[2]
        rest = sys.argv[3:]
        src = os.path.join(HERE, 'game_data_files', 'field', 'flevel.lgp')
        if sys.argv[1] == 'export':
            if len(rest) > 1:
                src = rest[1]
            a_ = lgp.Archive(src)
            print(export(list(lgp.split_sections(a_.decompressed(
                a_.index[name]))), name, rest[0]))
        else:
            if len(rest) > 2:
                src = rest[2]
            a_ = lgp.Archive(src)
            mp = list(lgp.split_sections(a_.decompressed(a_.index[name])))
            print(make(mp, name, rest[0], rest[1]))
            for other in ALSO.get(name, ()):
                print(other, make_also(list(lgp.split_sections(
                    a_.decompressed(a_.index[other]))), other, rest[0],
                    rest[1], mp, name))
    else:
        print(__doc__)

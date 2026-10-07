#!/usr/bin/env python3
"""ff7nx_debugwarp.py -- DEBUG ONLY: make one field stand in for another.

BUILD 618. Some fields are hard to reach for a texture check (cosmo2 has
no gateway into it at all -- only md_e1/blackbg6 MAPJUMP there, in the
ending). With

    SEVENTH_NX_DEBUG_WARP=ujunon2=cosmo2,prisila=fr_e,ujun_w=las0_2,jumin=kuro_1

(any number of src=dst pairs; each source once, no chains)

the build makes every entry into the first field land in the second:

  * the archive entry of the FIRST field gets the SECOND field's finished
    data (after every other flevel pass, so it shows exactly what the real
    field would show) -- so walking in, loading a save there, or arriving
    from the world map all show the target;
  * every gateway in every field that leads into the first field gets its
    arrival point moved onto the target's walkmesh (the triangle nearest the
    walkmesh's middle), so arriving on foot never names a triangle the
    target does not have. The world map and saves keep their own arrival
    point: prefer walking in from a neighbouring field.

The target's own scripts run as they would in the real field (a cut-scene
field may do nothing, or move you on); the background is what this is for.
OFF unless the variable is set; the build log says DEBUG WARP loudly. Do not
ship a build with it on.
"""
from __future__ import annotations

import os
import struct

ENV = 'SEVENTH_NX_DEBUG_WARP'
SEC_WALK = 4
SEC_TRIG = 7
GATE_AT = 0x38
GATES = 12
GATE_SIZE = 24


def pairs(env=None):
    raw = (os.environ if env is None else env).get(ENV, '').strip().lower()
    out = []
    for item in raw.replace(';', ',').split(','):
        if '=' in item:
            a, b = (s.strip() for s in item.split('=', 1))
            if a and b and a != b:
                out.append((a, b))
    # each source once, and no field both a source and a target (a chain
    # would show whichever pair ran last)
    seen, dsts, keep = set(), {b for _a, b in out}, []
    for a, b in out:
        if a in seen or a in dsts:
            continue
        seen.add(a)
        keep.append((a, b))
    return keep


def spawn_point(walk):
    """(x, y, triangle) at the triangle nearest the walkmesh's middle."""
    n = struct.unpack_from('<I', walk, 0)[0]
    if n == 0 or len(walk) < 4 + 24 * n:
        raise ValueError('no walkmesh')
    cents = []
    for t in range(n):
        v = struct.unpack_from('<12h', walk, 4 + 24 * t)
        cents.append(((v[0] + v[4] + v[8]) / 3.0, (v[1] + v[5] + v[9]) / 3.0))
    mx = sum(c[0] for c in cents) / n
    my = sum(c[1] for c in cents) / n
    t = min(range(n), key=lambda i: (cents[i][0] - mx) ** 2
            + (cents[i][1] - my) ** 2)
    return int(round(cents[t][0])), int(round(cents[t][1])), t


def retarget_gates(trig, src_id, point):
    """section 7 with every gateway into src_id arriving at point; count."""
    out = bytearray(trig)
    n = 0
    for i in range(GATES):
        o = GATE_AT + GATE_SIZE * i
        if o + GATE_SIZE > len(out):
            break
        v = struct.unpack_from('<6h3hH', out, o)
        if v[9] == src_id and any(v[:6]):
            struct.pack_into('<3h', out, o + 12, *point)
            n += 1
    return bytes(out), n


def maplist_of(archive):
    blob = archive.index['maplist']['payload']
    n = struct.unpack_from('<H', blob, 0)[0]
    return [blob[2 + 32 * i:2 + 32 * (i + 1)].split(b'\0')[0]
            .decode('latin1').strip().lower() for i in range(n)]


def apply_to_flevel(archive, payloads, maplist=None, encode=None,
                    log=lambda *_: None):
    import lgp
    todo = pairs()
    st = {'pairs': [], 'refused': []}
    if not todo:
        return st
    encode = encode or archive.encode_field
    ids = {n: i for i, n in enumerate(maplist or maplist_of(archive))}

    def raw_of(name):
        p = payloads.get(name)
        return (lgp.lzs_decompress(p[4:]) if p
                else archive.decompressed(archive.index[name]))

    for src, dst in todo:
        try:
            if src not in archive.index or dst not in archive.index:
                raise ValueError('unknown field')
            if src not in ids:
                raise ValueError('%s not in maplist' % src)
            target = raw_of(dst)
            point = spawn_point(list(lgp.split_sections(target))[SEC_WALK])
            gates = 0
            for name in archive.names():
                entry = archive.index[name]
                if name == src or not archive.is_field(entry):
                    continue
                try:
                    raw = raw_of(name)
                    parts = list(lgp.split_sections(raw))
                    new7, n = retarget_gates(parts[SEC_TRIG], ids[src], point)
                except Exception:                              # noqa: BLE001
                    continue
                if n:
                    parts[SEC_TRIG] = new7
                    payloads[name] = encode(lgp.join_sections(parts))
                    gates += n
            payloads[src] = encode(target)
            st['pairs'].append('%s -> %s (%d gateway(s) re-aimed at '
                               '%d,%d triangle %d)' % (src, dst, gates,
                                                       *point))
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((src, dst, str(exc)[:80]))
    return st


def summarise(st):
    out = []
    if st.get('pairs'):
        out.append('  !!! DEBUG WARP (BUILD 618) ACTIVE: %s. Unset %s before '
                   'a real build.' % ('; '.join(st['pairs']), ENV))
    for src, dst, why in st.get('refused', ()):
        out.append('  ! debug warp %s -> %s: %s' % (src, dst, why))
    return '\n'.join(out)

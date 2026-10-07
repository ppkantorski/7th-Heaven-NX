#!/usr/bin/env python3
"""ff7nx_easteregg.py -- the port developer's orange cat. BUILD 618z14.

The user (10-06): "when using echo-s i would like for tsuna's domain ... to
have one of the orange cats in a laying position facing forward chilling on
the top ... box ... a reference to me as a developer, since my dev profile
is an orange cat" -- and then: "lets put the cat on top of this crate, nice
in the center" (the crate right of where Cloud stands, two crates high).

WHERE. mds7_w2, the crate cage in Sector 7 where Echo-S puts Tsuna (the
modder) -- only when the field's script is Echo-S's (an entity named Tsuna
and his name in its text); with the stock script nothing is added.

WHAT, all from the user's own install:
  * MODEL: utmin1's orange cat (BDGA.HRC, "animal_cat1": mean vertex colour
    RGB 142/83/31 -- the other Wutai cat, EZCC, is grey/white), its loader
    record copied with its six animations and scale 800 (the same ratio to
    Cloud's 512 as in Wutai), lit with this field's own light block (Cloud's
    record), so it is not lit like a sunny Wutai house;
  * ENTITY "nekodev" appended last (no existing entity index moves):
        char 4; xyzi 127, 62, z 156, triangle 2; dir 224; tlkon 01; ret
        dfanm <anim> 1; ret
    i.e. on walkmesh triangle 2, the top of the crate stack two crates high
    (z 156 = 2 x 78), at the point that projects (this field's camera) to
    the middle of that crate's top face as the user framed it, facing the
    camera. FF7's facing byte: the world angle is dir x 360/256 - 90 degrees
    (fitted on 637 gateway arrivals across the archive, 83 % within 25
    degrees); the camera looks along +x+y, so facing it is 225 degrees,
    dir 224. Solid (Cloud bumps into it on the crate), no talk.
  * ANIMATION: FAAB, the cat sitting low and licking its raised paw
    (forward kinematics of the clip: the right foreleg lifts to the head,
    head moving; 159 frames). The Wutai cats have no clip that does both
    lying and licking: EZCB is lying flat, head on its paws (asleep).
    Hardware (10-06): the user wants it lying coiled -- EZCB is now the
    default; SEVENTH_NX_EASTER_CAT_ANIM=groom picks FAAB.
    BUILD 619 (10-06, evening): "i want the pose where its just sitting
    upright but not doing any gestures except wagging its tail. and i want
    it facing the camera". That clip exists: FAAC. Forward kinematics of
    all its frames: only o1/o2/o3 (the tail) move; every other bone keeps
    FAAB's frame-0 sitting pose (back upright, forelegs straight under the
    chest, hind legs folded), the head and forepaws towards the model's -Z
    -- the same forward as Cloud's feet in ACFE, i.e. the direction DIR
    turns. The lying clip (EZCB) twists the body 47-55 degrees on dou1, which
    is why it did not read as facing the camera. DIR stays 224: the field
    camera's view axis is (0.556, 0.556, -0.617), along +x+y, and 224 turns
    the model's forward to 225 degrees, -x-y, straight back at it.
    FAAC is the default; SEVENTH_NX_EASTER_CAT_ANIM=lie / groom pick EZCB /
    FAAB.

HOW. Section 1 gets one more entity name (8 bytes) and script row (64
bytes) before the code, and the new code is appended after the last script;
every stored offset after those points moves by the same amounts (string
table, AKAO blocks, script entries). Jumps inside scripts are relative, so
existing code is byte for byte unchanged. Section 3 gets the record, both
model counts +1. The result is re-parsed and every script disassembled
before it is accepted.

SEVENTH_NX_NO_EASTER_CAT=1 disables.
"""
from __future__ import annotations

import os
import struct

OFF_ENV = 'SEVENTH_NX_NO_EASTER_CAT'
ANIM_ENV = 'SEVENTH_NX_EASTER_CAT_ANIM'
FIELD = 'mds7_w2'
SOURCE = 'utmin1'
HRC = b'BDGA'
ANIMS = ('FAAC', 'EZBF', 'EZCA', 'HFEF', 'EZCB', 'FAAB')
ANIM_GROOM = 5                         # FAAB
ANIM_LIE = 4                           # EZCB
ANIM_SIT = 0                           # FAAC: sitting, only the tail moves
NAME = b'nekodev'
# BUILD 620e (hardware, sitting FAAC): the cat's outline (body + tail,
# 780..854 x 146..203 px) sat right of and below the crate face's centre
# (corners 711,154 / 825,114 / 891,177 / 777,222 -> 801,167). "moved up just
# a little bit, and maybe to the left just a little bit ... an equal amount of
# visual space on each direction". The tail swings, so the body's centre and
# the outline's are averaged: -10, -7 px. Through this field's camera (C =
# R.P + o, zoom 2926, 3 px per unit) that is +0.6, +8.5 world units: (128, 71),
# still walkmesh triangle 2 (the crate top), -10.1 / -7.6 px.
POS = (128, 71, 156)            # was (127, 62): the crate top face centre
TRIANGLE = 2
DIR = 212                      # 620f: 224 faced the camera; turned 17 deg toward Tsuna (screen left, the cat's right) -- user: "angled very slightly towards the left"
N_SCRIPTS = 32
MARKER = bytes(c - 0x20 for c in b'Tsuna')     # FF7 field text


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def anim_index():
    """BUILD 619: sitting upright, tail wagging (FAAC) is the default.
    SEVENTH_NX_EASTER_CAT_ANIM=lie picks EZCB (lying coiled), =groom FAAB
    (sitting, licking a paw)."""
    v = os.environ.get(ANIM_ENV, '').strip().lower()
    if v in ('lie', 'lying', 'sleep', 'coiled'):
        return ANIM_LIE
    if v in ('groom', 'lick'):
        return ANIM_GROOM
    return ANIM_SIT


def _records(sec3):
    import echo_s_flevel as ES
    return ES.model_loader_records(sec3)


def cat_record(src3, light):
    """utmin1's cat record with all six animations, lit by `light`."""
    for _name, hrc, (a, b) in _records(src3):
        if not hrc.startswith(HRC):
            continue
        rec = bytes(src3[a:b])
        ln = struct.unpack_from('<H', rec, 0)[0]
        at = 2 + ln + 2 + 8 + 4
        n = struct.unpack_from('<H', rec, at)[0]
        names = []
        p = at + 2 + 30
        for _ in range(n):
            al = struct.unpack_from('<H', rec, p)[0]
            names.append(rec[p + 2:p + 2 + al].split(b'\0')[0].split(b'.')[0]
                         .decode('ascii').upper())
            p += 2 + al + 2
        if tuple(names) != ANIMS:
            continue
        rec = rec[:at + 2] + light + rec[at + 2 + 30:]
        return _shades(rec, 2 + ln + 2)
    raise ValueError('%s has no %s record with %s'
                     % (SOURCE, HRC.decode(), '/'.join(ANIMS)))


def model_name():
    """The HRC the cat record names: NKDA (BUILD 620e, the copy with the
    sunglasses that ff7nx_catshades put in char.lgp THIS build) or BDGA."""
    try:
        import ff7nx_catshades
        if ff7nx_catshades.READY:
            return ff7nx_catshades.NEW_HRC.upper().encode('ascii')
    except Exception:                                          # noqa: BLE001
        pass
    return HRC


def _shades(rec, at):
    """BUILD 620e: point the record's 8-byte HRC id at the sunglasses copy
    -- only when char.lgp was confirmed to hold it (ff7nx_catshades.READY),
    so a missing NKDA can never be referenced."""
    name = model_name()
    if name == HRC or rec[at:at + 4] != HRC:
        return rec
    return rec[:at] + name + rec[at + 4:]


# IFUB, IFUBL, IFSW, IFSWL, IFUW, IFUWL: (size, jump-field bytes)
_IFS = {0x14: (6, 1), 0x15: (7, 2), 0x16: (8, 1), 0x17: (9, 2),
        0x18: (8, 1), 0x19: (9, 2)}
# anything else that branches: refuse rather than guess
_BRANCHES = set(range(0x10, 0x20)) | {0x30, 0x31, 0x32, 0xCA, 0xCB}


def char_gates(s1, meta, names, entries):
    """The IF instructions (raw, jump field included) that decide whether
    an entity's init script reaches its CHAR, for every entity. Raises on
    anything that is not a plain forward IF over the CHAR."""
    import field_dis as FD
    gates = []
    for ai, row in enumerate(entries):
        start = row[0]
        pending = []
        branch = 0
        for off, nm, size, raw, err in FD.walk(s1, start,
                                               meta['string_table']):
            if err:
                raise ValueError('%s init does not decode' % names[ai])
            op = raw[0]
            if op == 0x00:                        # ret: no CHAR in init
                break
            if op == 0xA1:                        # CHAR
                if branch:
                    raise ValueError('%s: branch 0x%02X before CHAR'
                                     % (names[ai], branch))
                gates += [g for g, tgt in pending if tgt > off]
                break
            if op in _IFS:
                sz, js = _IFS[op]
                if size != sz:
                    raise ValueError('%s: IF of size %d' % (names[ai], size))
                jmp = int.from_bytes(raw[sz - js:sz], 'little')
                pending.append((bytes(raw), off + sz - js + jmp))
            elif op in _BRANCHES:
                branch = branch or op
    return gates


def _gated(gates, body):
    """`gates` then `body` then RET; every gate jumps to that RET."""
    head = b''.join(gates)
    out = bytearray(head + body + b'\x00')
    ret = len(head) + len(body)
    o = 0
    for g in gates:
        sz, js = _IFS[g[0]]
        p = o + sz - js
        jmp = ret - p
        if jmp >= 1 << (8 * js):
            raise ValueError('gate jump too long')
        out[p:p + js] = jmp.to_bytes(js, 'little')
        o += sz
    return bytes(out)


def plan(sec1, sec3, walk, src3):
    """(new section 1, new section 3, info) -- raises on any doubt."""
    import field_dis as FD
    s1 = bytes(sec1)
    meta, names, entries = FD.parse(s1)
    if 'Tsuna' not in names or MARKER not in s1:
        raise ValueError('not Echo-S\'s script (no Tsuna)')
    if NAME.decode() in names:
        raise ValueError('already has the cat')
    recs = _records(sec3)
    if len(recs) != meta['n_models']:
        raise ValueError('loader has %d models, script says %d'
                         % (len(recs), meta['n_models']))
    # the crate top: walkmesh triangle 2 at z 156, containing the point
    n = struct.unpack_from('<I', walk, 0)[0]
    if TRIANGLE >= n:
        raise ValueError('no triangle %d' % TRIANGLE)
    t = struct.unpack_from('<12h', walk, 4 + 24 * TRIANGLE)
    vs = [t[0:3], t[4:7], t[8:11]]
    if any(v[2] != POS[2] for v in vs):
        raise ValueError('triangle %d is not the crate top' % TRIANGLE)

    def side(p, a, b):
        return (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])
    d = [side(POS, vs[i], vs[(i + 1) % 3]) for i in range(3)]
    if not (all(x >= 0 for x in d) or all(x <= 0 for x in d)):
        raise ValueError('the cat is not on triangle %d' % TRIANGLE)
    # model
    a0, b0 = recs[0][2]
    r0 = bytes(sec3[a0:b0])
    ln = struct.unpack_from('<H', r0, 0)[0]
    lat = 2 + ln + 2 + 8 + 4 + 2
    light = r0[lat:lat + 30]
    rec = cat_record(src3, light)
    model = len(recs)
    s3 = bytearray(bytes(sec3) + rec)
    struct.pack_into('<H', s3, 2, model + 1)
    # script. BUILD 620f: models go to entities in the order they run CHAR
    # (the CHAR argument is not an index), so when an earlier entity skips
    # its CHAR -- Echo-S's Tsuna does when he is not in the cage -- the cat
    # would get THAT entity's model (the user saw a dark Tsuna on the
    # crate). The cat therefore runs CHAR only under the same conditions,
    # copied from those entities' init scripts, and its main part (dfanm)
    # behind the same gates.
    gates = char_gates(s1, meta, names, entries)
    init = bytes([0xA1, model])
    init += bytes([0xA5, 0, 0]) + struct.pack('<hhhH', *POS, TRIANGLE)
    init += bytes([0xB3, 0, DIR, 0x7E, 1])
    main = bytes([0xA2, anim_index(), 1])
    code = _gated(gates, init) + _gated(gates, main)
    ret_at = len(code) - 1
    n_ent = meta['n_entities']
    head = 32 + 8 * n_ent
    akao_at = head
    rows_at = akao_at + 4 * meta['n_akao']
    code_at = meta['code_start']
    strtab = meta['string_table']
    grow = 8 + 2 * N_SCRIPTS
    shift = grow + len(code)
    akao = struct.unpack_from('<%dI' % meta['n_akao'], s1, akao_at)
    if any(o < strtab for o in akao):
        raise ValueError('an AKAO block before the string table')
    if not all(code_at <= o < strtab for r in entries for o in r):
        raise ValueError('a script entry outside the code')
    out = bytearray(s1[:32])
    out += s1[32:head] + NAME.ljust(8, b'\0')
    out += struct.pack('<%dI' % len(akao), *(o + shift for o in akao))
    for r in entries:
        out += struct.pack('<%dH' % N_SCRIPTS, *(o + grow for o in r))
    new_at = strtab + grow
    out += struct.pack('<%dH' % N_SCRIPTS, new_at,
                       *([new_at + ret_at] * (N_SCRIPTS - 1)))
    out += s1[code_at:strtab] + code + s1[strtab:]
    out[2] = n_ent + 1
    out[3] = meta['n_models'] + 1
    struct.pack_into('<H', out, 4, strtab + shift)
    if strtab + shift > 0xFFFF or max(akao) + shift > 0xFFFFFFFF:
        raise ValueError('section 1 too large')
    out = bytes(out)
    # verify: parse, every script decodes, old code and text unchanged
    m2, n2, e2 = FD.parse(out)
    if n2 != names + [NAME.decode()] or m2['n_models'] != model + 1:
        raise ValueError('re-parse disagrees')
    if out[m2['code_start']:m2['code_start'] + (strtab - code_at)] != \
            s1[code_at:strtab]:
        raise ValueError('existing code moved inconsistently')
    if out[m2['string_table']:] != s1[strtab:]:
        raise ValueError('text/AKAO moved inconsistently')
    for off in sorted({o for r in e2 for o in r}):
        for ins in FD.walk(out, off, m2['string_table']):
            if ins[4]:
                raise ValueError('script at %d does not decode' % off)
            if ins[1] == 'ret':
                break
    return out, bytes(s3), {'model': model, 'anim': ANIMS[anim_index()],
                            'entity': n_ent}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field

    def parts_of(name):
        p = payloads.get(name)
        raw = (lgp.lzs_decompress(p[4:]) if p
               else archive.decompressed(archive.index[name]))
        return list(lgp.split_sections(raw))
    if FIELD not in archive.index or SOURCE not in archive.index:
        return st
    try:
        parts = parts_of(FIELD)
        src = parts_of(SOURCE)
        parts[0], parts[2], info = plan(parts[0], parts[2], bytes(parts[4]),
                                        bytes(src[2]))
    except Exception as exc:                                   # noqa: BLE001
        st['refused'].append((FIELD, str(exc)[:100]))
        return st
    payloads[FIELD] = encode(lgp.join_sections(parts))
    st['names'].append('%s: entity %d "%s", model %d (%s from %s), %s'
                       % (FIELD, info['entity'], NAME.decode(), info['model'],
                          model_name().decode(), SOURCE, info['anim']))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  EASTER CAT (BUILD 618z14): the port dev\'s orange cat '
                   'on Tsuna\'s crates (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! easter cat %s: not added -- %s' % (name, why))
    return '\n'.join(out)

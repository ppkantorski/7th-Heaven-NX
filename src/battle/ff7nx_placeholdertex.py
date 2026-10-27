#!/usr/bin/env python3
"""ff7nx_placeholdertex.py -- spell art for magic.lgp entries the Switch
port ships as a 1x1 placeholder. BUILD 765.

WHAT WAS WRONG (hardware, 10-20: "we can see straight through the inside of
Hades' cauldron")
==================================================================
Hades' cauldron (hadesdat.p45) has a 12-gon lid: group 2, 10 textured
triangles, UVs on the unit disc, alpha-blended, drawn with hadesdat.t00.
Of all 1,109 textures in the Switch magic.lgp that one ALONE is a 1x1
placeholder: one texel, index 0, palette entry 0 = (0,0,0,0) with the
colour key on. So the lid draws nothing and the inside of the pot shows the
stage behind it.

SYW Unified Spells ships the art: FF7SYWU\\Base\\magic\\limit\\
HADESDAT_T00_00.dds, 256x256, the molten disc on clear corners. FFNx finds it
because it names a non-.tex texture `<base>_<ext>` (hadesdat.t00 ->
hadesdat_t00). This build only looked up `<stem>.tex`, so the file was
counted as "no magic.lgp home" (PLAN-194) and dropped.

WHAT THIS DOES
==============
* ffnx_alias(stem): `hadesdat_t00` -> `hadesdat.t00` (FFNx's naming for a
  texture whose extension is not .tex).
* For a magic.lgp entry whose vanilla TEX is a placeholder (at most 2x2
  texels) and for which a mod ships palette 00 under either name, a fresh
  TEX is built from the DDS: 1 palette x 256 colours at the art's own size
  (capped at 256), clear corners on index 0 with the colour key -- the
  battle module's hardware-proven paletted form (tex.convert_for_battle).
  It carries NO logical-scale mark: the lid's UVs are normalised floats, so
  the bigger texture is simply sampled where the 1x1 was. Nothing else in
  magic.lgp is touched; the content-matching spell pipeline
  (ff7nx_spelltex) cannot judge a 1-texel original and keeps doing all the
  rest.

SEVENTH_NX_PLACEHOLDER_TEX=0 leaves the placeholders stock.

BUILD 807 -- THE TWO 16-BIT ENTRIES
===================================
`ground_1.tex` (toc 1534, 128x256) and `rock_1.tex` (toc 4059,
limit2/tifa1, 128x128) are the only 16-bit direct-colour TEX in magic.lgp
that SYW ships art for. ff7nx_ddstex.convert_group only rebuilds 8-bit
paletted entries, so both stayed vanilla. Both are MESH textures
(normalised UVs, no .s frame table) and fully opaque (no colour key, no
alpha bit, 0.4% / 0% zero texels), so they are built here the same way as
the lid: 1 palette x 256 colours through tex.convert_for_battle, the
battle module's hardware-proven paletted form, at a whole-number multiple
of the vanilla size up to the spell cap, with the vanilla colour-key flag.
No logical-scale mark -- the UVs are normalised. The set is chosen by
folder exactly as FFNx chooses it (ff7nx_spelltex.conflict_folders).
SEVENTH_NX_SPELL_DIRECT16=0 leaves them stock.

BUILD 783 -- THE LID IS SOLID
=============================
Hardware 10-22: "the surface of the inside of the cauldron ... is
transparent when it shouldnt be". The lid's own render state
(hadesdat.p45, hundred 1, the 10-triangle group 1) is alpha-blended with
blend_mode 0 -- PSX AVERAGE, half the lid and half whatever is behind it.
That is stock (FFNx draws BLEND_AVG at half alpha too); with the 1x1
placeholder nothing was visible to notice. With the molten art restored, the
pot's interior shows through it. When the lid art is rebuilt here, its
hundred gets blend_mode 4 (none, the pot body's own) -- only after checking
the stock bytes exactly. The art is solid where the 12-gon samples it (302
of 49,616 texels are the clear index, coloured like the rim).
SEVENTH_NX_HADES_LID_SOLID=0 keeps the stock average blend.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import hashlib
import os
import re
import struct

ENV = 'SEVENTH_NX_PLACEHOLDER_TEX'
MAX_TEXELS = 4                  # a 1x1 or 2x2 entry is a placeholder
CAP = 256
VERSION = b'placeholder-tex-1'
_ALIAS = re.compile(r'^(.+)_([a-z]\d\d)$')


def enabled():
    return os.environ.get(ENV, '1').strip().lower() not in (
        '0', 'off', 'no', 'false')


def ffnx_alias(stem):
    """FFNx's name for a non-.tex texture: 'hadesdat_t00' -> 'hadesdat.t00'."""
    m = _ALIAS.match(stem.lower())
    return '%s.%s' % m.groups() if m else None


def is_placeholder(payload):
    import tex
    t = tex.parse(payload)
    return t is not None and t['width'] * t['height'] <= MAX_TEXELS


def _truecolor_tex(dds):
    """A minimal 32-bit TEX around the decoded DDS (BGRA on disk)."""
    import dds_decode
    import tex
    rgba, w, h = dds_decode.decode_dds(dds)
    hdr = bytearray(tex.HEADER_LEN)
    struct.pack_into('<I', hdr, tex.O_VERSION, 1)
    struct.pack_into('<I', hdr, tex.O_WIDTH, w)
    struct.pack_into('<I', hdr, tex.O_HEIGHT, h)
    struct.pack_into('<I', hdr, tex.O_BYTES_PER_PIXEL, 4)
    px = bytearray(rgba)
    px[0::4], px[2::4] = rgba[2::4], rgba[0::4]
    return bytes(hdr) + bytes(px)


SOLID_ENV = 'SEVENTH_NX_HADES_LID_SOLID'
# texture entry -> (model entry, hundred index); BUILD 783
SOLID = {'hadesdat.t00': ('hadesdat.p45', 1)}
P_HEADER = 128
HUNDRED = 100
H_FEATURES, H_OPTIONS, H_BLEND = 0x08, 0x0C, 0x44
V_ALPHABLEND = 0x400
BLEND_AVERAGE, BLEND_NONE = 0, 4


def solid_enabled():
    return enabled() and os.environ.get(SOLID_ENV, '1').strip().lower() \
        not in ('0', 'off', 'no', 'false')


def _hundreds_at(p):
    """(offset of hundred 0, count) in a battle .p model."""
    h = struct.unpack_from('<16I', p, 0)
    if h[0] != 1:
        raise ValueError('not a .p model')
    nv, nn, nu1, ntc, nvc, ne, npl, nu2, nu3, nh = (
        h[3], h[4], h[5], h[6], h[7], h[8], h[9], h[10], h[11], h[12])
    o = (P_HEADER + 12 * nv + 12 * nn + 12 * nu1 + 8 * ntc + 4 * nvc
         + 4 * npl + 4 * ne + 24 * npl + 24 * nu2 + 3 * nu3)
    if o + HUNDRED * nh > len(p):
        raise ValueError('hundreds run past the file')
    return o, nh


def solid_lid(p, hundred):
    """The model with that hundred's AVERAGE alpha blend made opaque.
    Raises unless the hundred is exactly the stock alpha-blended average."""
    o, nh = _hundreds_at(p)
    if hundred >= nh:
        raise ValueError('no hundred %d' % hundred)
    at = o + HUNDRED * hundred
    feat, opt = struct.unpack_from('<II', p, at + H_FEATURES)
    blend, = struct.unpack_from('<I', p, at + H_BLEND)
    if blend == BLEND_NONE:
        return p
    if not (feat & opt & V_ALPHABLEND) or blend != BLEND_AVERAGE:
        raise ValueError('hundred %d is not the alpha-blended average '
                         '(features %#x, options %#x, blend %d)'
                         % (hundred, feat, opt, blend))
    out = bytearray(p)
    struct.pack_into('<I', out, at + H_BLEND, BLEND_NONE)
    return bytes(out)


DIRECT16_ENV = 'SEVENTH_NX_SPELL_DIRECT16'
DIRECT16_VERSION = b'direct16-tex-2'   # BUILD 811: the spell scale rule


def direct16_enabled():
    return os.environ.get(DIRECT16_ENV, '1').strip().lower() not in (
        '0', 'off', 'no', 'false')


def is_direct16(payload):
    """A 16-bit direct-colour TEX (no palette)."""
    import tex
    t = tex.parse(payload)
    return bool(t and not t['palette_flag'] and t['bytes_per_pixel'] == 2)


def build_direct16(vanilla, dds, cap):
    """(tex bytes, note) or (None, reason) for a 16-bit mesh entry."""
    import tex
    v = tex.parse(vanilla)
    if not is_direct16(vanilla):
        return None, 'not a 16-bit TEX'
    data, note = tex.convert_for_battle(
        _truecolor_tex(dds), vanilla, max(int(cap or 0), v['width'],
                                          v['height']))
    if data is None:
        return None, note
    t = tex.parse(data)
    if (not t or t['bytes_per_pixel'] != 1 or not t['palette_flag']
            or t['width'] % v['width'] or t['height'] % v['height']
            or t['width'] // v['width'] != t['height'] // v['height']):
        return None, 'conversion did not keep the vanilla aspect'
    out = bytearray(data)
    struct.pack_into('<I', out, tex.O_COLORKEY,
                     struct.unpack_from('<I', vanilla, tex.O_COLORKEY)[0])
    return bytes(out), note


def build(vanilla, dds):
    """(tex bytes, note) or (None, reason)."""
    import tex
    if not is_placeholder(vanilla):
        return None, 'not a placeholder'
    data, note = tex.convert_for_battle(_truecolor_tex(dds), None, CAP)
    if data is None:
        return None, note
    # keep the entry's own colour-key setting (the lid's clear corners)
    ck = struct.unpack_from('<I', vanilla, tex.O_COLORKEY)[0]
    out = bytearray(data)
    struct.pack_into('<I', out, tex.O_COLORKEY, ck)
    return bytes(out), note


def payloads(archive, spell_dds, cache_dir, log=lambda *_: None):
    """{toc index: tex bytes} for placeholder entries a mod supplies art for.

    `spell_dds` is the build's [(rel, full)] list of magic DDS."""
    import ff7nx_spelltex
    if not enabled():
        return {}
    by_name = {}
    for i, e in enumerate(archive.entries):
        by_name.setdefault(e['name'].lower(), []).append(i)
    want = {}
    for rel, full in sorted(spell_dds, key=lambda rf: rf[0].lower()):
        parsed = ff7nx_spelltex._parse_dds_name(
            os.path.basename(rel.replace('\\', '/')).lower())
        if not parsed or parsed[1] != 0:
            continue
        stem = parsed[0]
        for name in (stem + '.tex', ffnx_alias(stem)):
            for i in by_name.get(name or '', ()):
                if is_placeholder(archive.entries[i]['payload']):
                    want.setdefault(i, full)          # first mod in order
    out = {}
    os.makedirs(cache_dir, exist_ok=True)
    for i, full in sorted(want.items()):
        name = archive.entries[i]['name']
        with open(full, 'rb') as f:
            dds = f.read()
        van = archive.entries[i]['payload']
        key = hashlib.sha256(VERSION + van + dds).hexdigest()[:24]
        hit = os.path.join(cache_dir, '%s.%s.tex' % (name, key))
        if os.path.isfile(hit):
            with open(hit, 'rb') as f:
                out[i] = f.read()
            log('  placeholder %s: from cache' % name)
            continue
        try:
            data, note = build(van, dds)
        except Exception as exc:                              # noqa: BLE001
            data, note = None, '%s: %s' % (type(exc).__name__, exc)
        if data is None:
            log('  ! placeholder %s: %s -- stock kept' % (name, note))
            continue
        with open(hit + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(hit + '.tmp', hit)
        out[i] = data
        log('  placeholder %s (1x1 in the Switch archive) <- %s: %s'
            % (name, os.path.basename(full), note))
    _direct16(archive, spell_dds, cache_dir, out, log)
    _solid(archive, by_name, out, log)
    return out


def direct16_limit(vanilla, name):
    """BUILD 811: the largest dimension a 16-bit entry may be rebuilt at --
    its vanilla size times the factor every other spell TEX gets."""
    import ff7nx_spelltex as S
    import tex
    v = tex.parse(vanilla)
    k = S.budgeted_scale(vanilla, S.texture_scale(name, S.uniform_scale()))
    return max(v['width'], v['height']) * max(1, k)


def _direct16(archive, spell_dds, cache_dir, out, log):
    """BUILD 807: the 16-bit mesh entries -- see the module note."""
    import ff7nx_spelltex
    if not direct16_enabled():
        return
    folders = ff7nx_spelltex.conflict_folders(archive)
    cap = ff7nx_spelltex.cap()
    for i, e in enumerate(archive.entries):
        if i in out or not is_direct16(e['payload']):
            continue
        stem = e['name'].lower()
        if not stem.endswith('.tex'):
            continue
        stem = stem[:-4]
        want = folders.get(i, '')
        full = None
        for rel, path in sorted(spell_dds, key=lambda rf: rf[0].lower()):
            norm = rel.replace('\\', '/').lower()
            if (ff7nx_spelltex._parse_dds_name(os.path.basename(norm))
                    == (stem, 0) and ff7nx_spelltex._mod_subfolder(
                        os.path.dirname(norm)) == want):
                full = path
                break
        if full is None:
            continue
        with open(full, 'rb') as f:
            dds = f.read()
        van = e['payload']
        # BUILD 811: the SAME per-texture rule as every other spell TEX --
        # uniform_scale() (3x at the 768px cap), FULL_RES_TEXTURES 4x, held
        # by the palette budget -- not the raw pixel cap. 807c sized these
        # to 768px, which made rock_1 (128px) 4x / 512px; the rule is 3x.
        limit = direct16_limit(van, e['name'])
        key = hashlib.sha256(DIRECT16_VERSION + van + dds
                             + str(limit).encode()).hexdigest()[:24]
        hit = os.path.join(cache_dir, '%s.%s.tex' % (e['name'], key))
        if os.path.isfile(hit):
            with open(hit, 'rb') as f:
                out[i] = f.read()
            continue
        try:
            data, note = build_direct16(van, dds, limit)
        except Exception as exc:                              # noqa: BLE001
            data, note = None, '%s: %s' % (type(exc).__name__, exc)
        if data is None:
            log('  ! 16-bit %s: %s -- stock kept' % (e['name'], note))
            continue
        os.makedirs(cache_dir, exist_ok=True)
        with open(hit + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(hit + '.tmp', hit)
        out[i] = data
        log('  spell textures: 16-bit %s (toc %d) <- %s, rebuilt paletted: %s'
            % (e['name'], i, os.path.basename(full), note))


def _solid(archive, by_name, out, log):
    """BUILD 783: the restored lid is drawn opaque (see the module note)."""
    if not solid_enabled():
        return
    for i in list(out):
        want = SOLID.get(archive.entries[i]['name'].lower())
        if not want:
            continue
        model, hundred = want
        for j in by_name.get(model, ()):
            try:
                new = solid_lid(archive.entries[j]['payload'], hundred)
            except Exception as exc:                          # noqa: BLE001
                log('  ! %s: %s -- lid blend kept' % (model, exc))
                continue
            if new != archive.entries[j]['payload']:
                out[j] = new
                log('  %s: hundred %d average -> opaque (the lid is solid; '
                    '%s=0 keeps it)' % (model, hundred, SOLID_ENV))

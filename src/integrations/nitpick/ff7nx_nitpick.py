"""
ff7nx_nitpick.py -- the Nitpick mod (BUILD 641).

Nitpick (1.82) is a bag of small options. Most of them are files the build
already routes on its own: the WEAPON / Diamond WEAPON world and battle models
(world_us.lgp, battle.lgp), the Black Materia (char.lgp), the alternate world
map (frame-zero world DDS -> world_us.lgp), the attract-mode explosion movie
(data/movies) and the seven SYW field background fixes (the field-art repack
reads them out of the .iro like Cosmo Limit Break's). This module is the rest.

GREYED OUT
==========
LOCKED forces an option to a value and the settings window shows it disabled
with the reason. Title Screen Music: the build plays Echo-S's main_menu.ogg
itself (ff7nx_titlemusic), so Nitpick's copy must not compete with it. The PS1
Style group (Optic Blast, Light up the World, Pixelback, UWback, Battle
Shadows) stays off.

ffnx_aspect_ratio is 0 (4:3): the Switch draws the title screen in the 4:3
area, so the Midgar title takes Nitpick's 4:3 art (title_screen_midgar43).

TEXTURES
========
FFNx loads `menu/<name>_00.dds` and `cr/<name>_00.dds` in place of the game's
menu_us.lgp / cr_us.lgp TEX; the Switch has no such loader. Each active one is
converted into its TEX (one palette, 256 colours, SCALE x the native size,
marked with its logical scale for the _load_texture UV hook -- the same path
Enhanced Stock UI's 2x menu sheets take):
  menu/buster    the title background (NITPICK-TITLE)
  menu/red       Red XIII's menu portrait (NITPICK-RED)
  cr/ff7logo_*   the attract-mode logo, cr/name*_* the staff names,
  cr/opback_a    the attract-mode background (NITPICK-CR); 7th Heaven picks
                 one of ten 4:3 backgrounds at random per launch
                 (RandomVarOnce), the build picks one per build.

MODULE WORDS
============
Title (NITPICK-TITLE 1 or 2). FFNx stretches a modded buster texture to the
whole screen with UV 0..1 (FFNx special_case.cpp, buster_tex). The port draws
it in x86 0x7224D7's quad, (185,180)-(485,501) with UV 0..150/256: its 16
vertex stores (ARM 0xE479D8..0xE480D4) store (0,0)-(640,480), UV 0..1 instead.

Title menu (NITPICK-TITLE 2, Midgar). Nitpick's start.txt moves NEW GAME /
CONTINUE? off the art; its numbers are for the PC title code. The Switch's own
title code (state 7 of the load screen, x86 0x7212FB) draws them from one
halfword, [0x9261C2], used as both x and y (that halfword is the base of the
whole load screen, so it cannot be rewritten). Its eight reads in that block
become constants: x = TITLE_X, first row y = TITLE_Y (rows 28 apart, the
finger beside them, as before).

Credits (NITPICK-CR). Nitpick's 02.txt draws opback_a alone over the whole
screen: x86 0x7AAA5B's quad (0,0)-(256,256) -> (0,0)-(640,480), and
0x7A7D94's tile loop 6 -> 1. ARM: the four stores at 0x108CAA8, 0x108CC5C,
0x108CE28, 0x108CE48 and the loop bound `sub w9, w8, #6` at 0x10A2854.

Nitpick's own HEXT files for these (title start.txt, credits 02.txt) are
dropped from the exe list: their bytes are x86 code the port never runs, and
start.txt's 0x9261C0 data would move the Switch's load screen.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import hashlib
import os
import random
import struct

import a64 as A

MOD_ID = '7ac77aef-8732-4e9a-8321-bec5c016456c'

LOCKED = {
    'NITPICK-TITLEMUSIC': (0, 'Switch: the title music is the build\'s own'),
    'OPTIC-BLAST': (0, 'PS1 style: not used on the Switch'),
    'LIGHT-WORLD': (0, 'PS1 style: not used on the Switch'),
    'PIXELBACK': (0, 'PS1 style: not used on the Switch'),
    'UWBACK': (0, 'PS1 style: not used on the Switch'),
    'PBS': (0, 'PS1 style: not used on the Switch'),
}

# BUILD 641b: Nitpick's mod.xml answers its seven background fixes by
# switching Cosmos Limit Break's LIMITBREAK / UNCROP / ATA off (log 393: the
# whole field-art layer gone). Nitpick is an add-on here, never a reason to
# drop another mod's feature, so its "turn the other mod off" rules (Require
# 0) are ignored. 641c: the fixes themselves stay as chosen -- they replace
# their own pages in those seven fields, on top of Cosmos's art.
CLB_ID = '7c6a0289-ae89-411d-836a-531e78938401'
BG_FIXES = ('NITPICK-BGMD8_2', 'NITPICK-BGMD8_3', 'NITPICK-BGMDS7',
            'NITPICK-BGMDS7PB_1', 'NITPICK-BGMRKT4', 'NITPICK-BGMDS5_5',
            'NITPICK-BGJAIL2')


def ignores_rule(mod, required):
    """True for a Nitpick compatibility rule that would switch another
    mod's option off."""
    try:
        return handles(mod) and int(required) == 0
    except (TypeError, ValueError):
        return False


# HEXT carried as module words here, never baked into the exe
CARRIED_HEXT = ('title_screen_midgar43/', 'title_screen_midgar169/',
                'credits_hd/credits/', 'credits_vanilla/')

TEX_ARCHIVES = {'menu': 'menu_us.lgp', 'cr': 'cr_us.lgp'}
SCALE_ENV = 'SEVENTH_NX_NITPICK_TEXSCALE'
TEX_VERSION = b'nitpick-tex-1'
RANDOM_BG = 'credit_screen_43c/'

TITLE_X, TITLE_Y = 56, 301            # NEW GAME's x, y (640x480 units)
TITLE_ROW = 28                        # the Switch title's own row pitch

F = lambda v: struct.unpack('<I', struct.pack('<f', v))[0]       # noqa: E731

# x86 0x7224D7: (vertex, field) -> ARM store, field x/y/u/v
TITLE_QUAD = {
    0xE479D8: F(0.0), 0xE479F8: F(0.0), 0xE47AD4: F(0.0), 0xE47B0C: F(0.0),
    0xE47B5C: F(0.0), 0xE47B80: F(480.0), 0xE47C50: F(0.0), 0xE47CE4: F(1.0),
    0xE47D38: F(640.0), 0xE47D58: F(0.0), 0xE47E7C: F(1.0), 0xE47EB4: F(0.0),
    0xE47F04: F(640.0), 0xE47F24: F(480.0), 0xE48048: F(1.0), 0xE480D4: F(1.0),
}
# the eight `ldrsh wN, [x0]` of [0x9261C2] in state 7: (register, axis)
TITLE_READS = {
    0xE45774: (8, 'y'), 0xE457C0: (8, 'x'),      # finger
    0xE4583C: (8, 'y'), 0xE45868: (24, 'x'),     # NEW GAME
    0xE458F8: (8, 'y'), 0xE45924: (24, 'x'),     # CONTINUE?
    0xE45998: (21, 'y'), 0xE459BC: (21, 'x'),    # the third (empty) row
}
CREDITS_QUAD = {0x108CAA8: F(480.0), 0x108CC5C: F(640.0),
                0x108CE28: F(640.0), 0x108CE48: F(480.0)}
CREDITS_LOOP = 0x10A2854              # sub w9, w8, #6 -> #1

CURRENT = None


def handles(mod):
    m = getattr(mod, 'manifest', None)
    if m is not None and (getattr(m, 'mod_id', '') or '').lower() == MOD_ID:
        return True
    return (getattr(m, 'name', '') or '').strip().lower() == 'nitpick'


def locked_option(mod, option_id, clb=False):
    """Why a Nitpick option is greyed out, or None."""
    if not handles(mod):
        return None
    hit = next((v for k, v in LOCKED.items() if k.lower() == str(option_id).lower()), None)
    return hit[1] if hit else None


def locked_value(mod, option_id, clb=False):
    if not handles(mod):
        return None
    hit = next((v for k, v in LOCKED.items() if k.lower() == str(option_id).lower()), None)
    return hit[0] if hit else None


def effective(settings, clb=False):
    """Nitpick's settings as the build applies them."""
    out = dict(settings or {})
    for k, (v, _why) in LOCKED.items():
        key = next((x for x in out if str(x).lower() == k.lower()), k)
        out[key] = v
    out.setdefault('ffnx_aspect_ratio', 0)
    return out


def tex_scale():
    raw = os.environ.get(SCALE_ENV, '').strip()
    try:
        v = int(raw) if raw else 2
    except ValueError:
        v = 2
    return v if v in (1, 2, 4) else 2


def _norm(p):
    return p.replace('\\', '/').lower()


def carried_hext(rel):
    """True for a Nitpick HEXT file this module carries (or drops)."""
    r = _norm(rel)
    i = r.find('nitpick/')
    r = r[i + 8:] if i >= 0 else r
    return any(r.startswith(c) for c in CARRIED_HEXT)


def plan_for(mod, settings, read=None, log=lambda *_: None, rng=None):
    """What the active options need from this module."""
    import iro
    st = effective(settings)
    folders = [_norm(f).rstrip('/') + '/'
               for f in iro.active_folders(mod.manifest, st, read=read)]
    bgs = [f for f in folders if f.startswith(RANDOM_BG)]
    if len(bgs) > 1:
        keep = (rng or random.SystemRandom()).choice(sorted(bgs))
        folders = [f for f in folders if not f.startswith(RANDOM_BG) or f == keep]
        log('  Nitpick attract mode: background %s this build (7th Heaven '
            'picks one of %d per launch)' % (keep.rstrip('/'), len(bgs)))
    on = lambda prefix: any(f.startswith(prefix) for f in folders)   # noqa: E731
    return {
        'mod': mod, 'settings': st, 'folders': folders,
        'title': on('title_screen_'),
        'title_menu': on('title_screen_midgar'),
        'credits': on('credits_hd/credits/'),
    }


def texture_jobs(plan, entries):
    """{archive: {tex name: iro entry}}, later active folder winning."""
    out = {}
    for folder in plan['folders']:
        for e in entries:
            low = _norm(e)
            if not low.startswith(folder) or not low.endswith('_00.dds'):
                continue
            parts = low[len(folder):].split('/')
            if len(parts) != 2 or parts[0] not in TEX_ARCHIVES:
                continue
            out.setdefault(TEX_ARCHIVES[parts[0]], {})[parts[1][:-7] + '.tex'] = e
    return out


def build_texture(vanilla, dds, scale):
    """One TEX from one DDS: the native layout at `scale`, 1 x 256 colours."""
    import ff7nx_ddstex as DT
    import ff7nx_esui as E
    import tex
    t = tex.parse(vanilla)
    if t is None or t['bytes_per_pixel'] != 1 or not t['palette_flag']:
        raise ValueError('not an 8-bit paletted TEX')
    img = DT._decode(dds)[0].copy()
    if t['num_palettes'] != 1:
        raise ValueError('%d palettes' % t['num_palettes'])
    if not (img[:, :, 3] >= 128).any():
        # Nothing drawn (Nitpick's cr/ff7logo_* clear the logo off the key
        # art): every texel index 0, a clear colour key, at the native size.
        hdr = bytearray(vanilla[:len(vanilla) - len(t['palette']) - len(t['pixels'])])
        struct.pack_into('<I', hdr, tex.O_COLORKEY, 1)
        return bytes(hdr) + bytes(len(t['palette'])) + bytes(len(t['pixels']))
    data, _note = E.joint_tex(vanilla, {0: img}, scale)
    return data


def build_textures(plan, archive_paths, cache_dir, log=lambda *_: None):
    """{archive: {tex name: path of the built TEX}}."""
    import iro
    import lgp
    mod = plan['mod']
    entries = [e if isinstance(e, str) else e[0] for e in iro.list_entries(mod.path)]
    jobs = texture_jobs(plan, entries)
    scale = tex_scale()
    st = os.stat(mod.path)
    os.makedirs(cache_dir, exist_ok=True)
    out = {}
    for archive, names in sorted(jobs.items()):
        if archive not in archive_paths:
            log('  ! Nitpick: %s is not in this dump; %d texture(s) skipped'
                % (archive, len(names)))
            continue
        arc = lgp.Archive(archive_paths[archive])
        for name, entry in sorted(names.items()):
            v = arc.index.get(name)
            if v is None:
                log('  ! Nitpick: %s has no %s; skipped' % (archive, name))
                continue
            vanilla = v['payload']
            key = hashlib.sha256(TEX_VERSION + vanilla + repr(
                (scale, entry, st.st_size, st.st_mtime_ns)).encode()).hexdigest()[:24]
            path = os.path.join(cache_dir, '%s.%s.tex' % (name[:-4], key))
            if not os.path.isfile(path):
                try:
                    data = build_texture(vanilla, iro.read_one(mod.path, entry), scale)
                except Exception as exc:                          # noqa: BLE001
                    log('  ! Nitpick %s: %s -- the game\'s own kept' % (name, exc))
                    continue
                with open(path + '.tmp', 'wb') as f:
                    f.write(data)
                os.replace(path + '.tmp', path)
            out.setdefault(archive, {})[name] = path
    return out


# ---------------------------------------------------------------- module
def _store_cave(site, value, resume):
    def build(cave, address):
        w = [A.stp64_pre(9, 10, 31, -16), A.movz(9, value & 0xFFFF)]
        if value >> 16:
            w.append(A.movk_hi(9, value >> 16))
        w += [A.str_(9, 0, 0), A.ldp64_post(9, 10, 31, 16)]
        w.append(A.b(address(len(w)), resume))
        return w
    return build


def module_words(text, plan, pool):
    """{va: (old, new)} for the active options; caves from `pool`."""
    import ff7nx_cave
    rd = lambda va: struct.unpack_from('<I', text, va)[0]        # noqa: E731
    out = {}
    stores = {}
    if plan.get('title'):
        stores.update(TITLE_QUAD)
    if plan.get('credits'):
        stores.update(CREDITS_QUAD)
        old = rd(CREDITS_LOOP)
        if old not in (A.sub_imm(9, 8, 6), A.sub_imm(9, 8, 1)):
            raise ValueError('credits loop +0x%X holds %08X' % (CREDITS_LOOP, old))
        out[CREDITS_LOOP] = (A.sub_imm(9, 8, 6), A.sub_imm(9, 8, 1))
    if plan.get('title_menu'):
        for va, (reg, axis) in TITLE_READS.items():
            old = rd(va)
            want = 0x79C00000 | reg           # ldrsh wN, [x0]
            new = A.movz(reg, TITLE_X if axis == 'x' else TITLE_Y + 2 * TITLE_ROW)
            if old not in (want, new):
                raise ValueError('title read +0x%X holds %08X' % (va, old))
            out[va] = (want, new)
    for va, value in sorted(stores.items()):
        old = rd(va)
        if (old & 0xFFFFFFE0) not in (0xB9000000, 0xBD000000):
            raise ValueError('+0x%X holds %08X, not a str to [x0]' % (va, old))
        entry, placed = ff7nx_cave.emit_laid_out(pool, _store_cave(va, value, va + 4))
        for a, w in placed.items():
            out[a] = (rd(a), w)
        out[va] = (old, A.b(va, entry))
    return out


def apply_to_nso(src, dest, plan, log=lambda *_: None):
    import ff7nx_audio_cave as AC
    import ff7nx_cave
    import nxmap
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    m = nxmap.Main(src)
    pool = ff7nx_cave.HolePool(text, starts=set(m.arm_starts))
    words = module_words(bytes(text), plan, pool)
    for va, (_old, new) in words.items():
        struct.pack_into('<I', text, va, new)
    raw[0] = bytes(text)
    packed = AC.pack(blob, raw, 0)
    check, _ = AC.segments(packed)
    assert check[0][2] == segs[0][2]
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(packed)
    return len(words)


# BUILD 644c: Nitpick's HD Ultima WEAPON world model (Weapons_HD_World,
# ultima_sk) lists its head piece twice and its neck never --
#     head bone:  3 EEC1 EEC1 EEC2
# EEC (eed.p, z -14..1 from the bone: the neck) ships with it but nothing
# draws it, so the head floats. EEC1 is the head (z -28..-7), EEC2 a small
# piece on it. The skeleton gets the neck back; nothing else in it changes.
HRC_FIXES = {
    'dyb.hrc': ((b':SKELETON ultima_sk', b'3 EEC1 EEC1 EEC2'),
                b'3 EEC EEC1 EEC2'),
}


def fix_world_files(mod_files, cache_dir, log=lambda *_: None):
    """{low: (src, mod)} with known Nitpick skeleton typos corrected."""
    import hashlib
    out = dict(mod_files)
    for low, ((marker, bad), good) in HRC_FIXES.items():
        pair = out.get(low)
        if pair is None or 'eec.rsd' not in out:
            continue
        try:
            with open(pair[0], 'rb') as f:
                data = f.read()
        except OSError:
            continue
        if marker not in data or data.count(bad) != 1:
            continue
        fixed = data.replace(bad, good)
        os.makedirs(cache_dir, exist_ok=True)
        dest = os.path.join(cache_dir, '%s.%s' % (
            low, hashlib.sha1(fixed).hexdigest()[:16]))
        if not os.path.isfile(dest):
            with open(dest + '.tmp', 'wb') as f:
                f.write(fixed)
            os.replace(dest + '.tmp', dest)
        out[low] = (dest, pair[1])
        log('  Nitpick: %s -- head bone draws its neck (%s -> %s)'
            % (low, bad.decode(), good.decode()))
    return out

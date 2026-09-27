"""Route SYW Unified Minigames DDS art into the Switch's native TEX archives.

The IRO stays compressed until an archive actually needs one of its DDS files.
Only a complete native palette set is converted: a TEX has one index bitmap
shared by every palette, so replacing some palettes independently is unsafe.
"""
from __future__ import annotations

import hashlib
import os
import re
import struct

import iro
import tex


CAP_ENV = 'SEVENTH_NX_MINIGAME_TEX_CAP'
DEFAULT_CAP = 256
CONVERSION_VERSION = b'minigame-ddstex-1'
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache', '_minigame_dds')

# PC FFNx names folders by game, while the Switch keeps several language
# archives. A DDS is offered only to an archive that contains its TEX name.
TARGETS = {
    'chocobo': ('chocobo.lgp', 'fchocobo.lgp', 'gchocobo.lgp',
                'schocobo.lgp'),
    'coaster': ('coaster.lgp',),
    'condor': ('condor.lgp', 'condorj.lgp', 'fcondor.lgp',
               'gcondor.lgp', 'scondor.lgp'),
    'high': ('high-us.lgp', 'high-fr.lgp', 'high-ge.lgp', 'high-sp.lgp'),
    'snowboard': ('snowboard-us.lgp', 'snowboard-fr.lgp',
                  'snowboard-ge.lgp', 'snowboard-sp.lgp'),
    'sub': ('sub.lgp', 'fsub.lgp', 'gsub.lgp', 'ssub.lgp'),
}
# Only the archives for the selected game language need replacement. Building
# all four copies adds large duplicate LGPs to the SD tree and consumes time
# even though FFNx selects only one language at runtime.
LANG_TARGETS = {
    1: {'chocobo': ('chocobo.lgp',), 'coaster': ('coaster.lgp',),
        'condor': ('condor.lgp', 'condorj.lgp'),
        'high': ('high-us.lgp',), 'snowboard': ('snowboard-us.lgp',),
        'sub': ('sub.lgp',)},
    2: {'chocobo': ('fchocobo.lgp',), 'coaster': ('coaster.lgp',),
        'condor': ('fcondor.lgp',), 'high': ('high-fr.lgp',),
        'snowboard': ('snowboard-fr.lgp',), 'sub': ('fsub.lgp',)},
    3: {'chocobo': ('gchocobo.lgp',), 'coaster': ('coaster.lgp',),
        'condor': ('gcondor.lgp',), 'high': ('high-ge.lgp',),
        'snowboard': ('snowboard-ge.lgp',), 'sub': ('gsub.lgp',)},
    4: {'chocobo': ('schocobo.lgp',), 'coaster': ('coaster.lgp',),
        'condor': ('scondor.lgp',), 'high': ('high-sp.lgp',),
        'snowboard': ('snowboard-sp.lgp',), 'sub': ('ssub.lgp',)},
}
_DDS = re.compile(r'^(.+)_([0-9]+)\.dds$', re.I)


def cap():
    raw = os.environ.get(CAP_ENV, '').strip()
    if not raw:
        return DEFAULT_CAP
    value = int(raw)
    if value < 0 or value > 4096:
        raise ValueError('%s must be 0..4096' % CAP_ENV)
    return value


def is_pack(mod):
    return bool(getattr(mod, 'manifest', None) and
                mod.manifest.mod_id.lower() ==
                '50000000-5555-ff75-5775-720000000004')


def _normal_path(path):
    return path.replace('\\', '/').lower()


def _asset(path):
    """(category, native name, palette) for this pack's real art."""
    parts = _normal_path(path).split('/')
    if len(parts) < 4 or parts[0] != 'ff7sywu':
        return None
    if parts[1] == 'base' and len(parts) == 4:
        category = parts[2]
    elif parts[1] == 'lang' and len(parts) == 5:
        category = parts[3]
    else:
        return None
    if category not in TARGETS:
        return None
    match = _DDS.fullmatch(parts[-1])
    if not match:
        return None
    return category, match.group(1) + '.tex', int(match.group(2))


def collect(mod, settings):
    """{archive: {(tex name, palette): IRO path}} in 7H folder priority.

    ``active_folders`` is already ordered for last-write-wins emplacement.
    Iterating it directly preserves the author's Base/selected Lang collision
    priority instead of assuming that a language folder always wins.
    """
    if not is_pack(mod):
        return {}
    language = int(settings.get('SYWU', 1))
    if language not in LANG_TARGETS:
        raise ValueError('unsupported SYW minigame language %s' % language)
    folders = iro.active_folders(mod.manifest, settings)
    entries = mod.entries()
    out = {}
    for folder in folders:
        prefix = _normal_path(folder).rstrip('/') + '/'
        for rel in entries:
            if not _normal_path(rel).startswith(prefix):
                continue
            parsed = _asset(rel)
            if parsed is None:
                continue
            category, name, palette = parsed
            for archive in LANG_TARGETS[language][category]:
                out.setdefault(archive, {})[(name, palette)] = rel
    return out


def _read_archive_entry(path, name):
    """Read one native TEX without unpacking its whole LGP."""
    with open(path, 'rb') as f:
        f.seek(12)
        count = struct.unpack('<I', f.read(4))[0]
        offset = None
        for _ in range(count):
            row = f.read(27)
            if row[:20].split(b'\0')[0].decode('ascii', 'replace').lower() == name:
                offset = struct.unpack('<I', row[20:24])[0]
        if offset is None:
            return None
        f.seek(offset + 20)
        size = struct.unpack('<I', f.read(4))[0]
        return f.read(size)


# BUILD 542. Per-texture size limits for the snowboard's PSX-style path,
# measured against hardware screenshots:
#
#   eyes.tex   VRAM region 56x48 (static table 0x9624A0, entry 0) inside a
#              64x64 TEX. Every snowboard texture whose region equals its TEX
#              (GOAL sign, trees, board) renders right when enlarged; this is
#              the only one whose region does not, and enlarged it drew Cloud's
#              eyes tiny, high and far apart while vanilla's 64px drew them
#              right. Kept at the native 64px -- SYW's art, area-reduced.
#   estamp*, time*   the full-screen result/record stamps. Drawn at ~3.6x
#              their native size, so SYW's own 4x (1024px) is the closest to
#              1:1; at the 768 cap they were magnified again and their 1-bit
#              colour-keyed edge stair-stepped.
SNOWBOARD_LIMITS = {
    'eyes.tex': 64, 'tifaeye.tex': 128,
    'estamp0.tex': 1024, 'estamp1.tex': 1024,
    'time1.tex': 1024, 'time2.tex': 1024,
}


# BUILD 544: the face textures keep vanilla's exact TEX structure -- native
# size AND its 16-colour, 4-bit-CLUT palette -- so nothing but the pixels
# differs from the vanilla file the port draws correctly.
SNOWBOARD_FACE = frozenset(('eyes.tex', 'tifaeye.tex'))
# BUILD 548: eyes.tex is pre-stretched from its 56x48 VRAM region to the
# full 64x64 TEX -- the port samples it through the region size (x86
# 0x732B49), see ff7nx_ddstex.stretch_to_region.
# BUILD 547: tifaeye.tex is NOT Cloud's snowboard face (object 279 is some
# other face); his is eyes.tex on object 14 -- fixed in the TMD, see
# ff7nx_snowface. The 545/546 tifaeye art edit is gone; it converts plainly.
# BUILD 543: stamps re-cut as clean keyed stickers (ff7nx_ddstex.clean_sticker)
SNOWBOARD_STICKERS = frozenset(('estamp0.tex', 'estamp1.tex', 'time1.tex'))


# BUILD 549. The motorcycle game's 2D HUD sprites -- READY, Go!, GOAL, the
# SCORE / HI-SCORE panels, both digit fonts, the meter bars and the rider
# portraits. They are drawn at ~1.5x native (720p) to ~2.2x (1080p); at the
# 768 cap (3x) the GPU minified them with no mipmaps and the engine's 1-bit
# colour key stair-stepped the white outlines. 2x sits at ~1:1, and the area
# reduction (as the snowboard stamps use) gives the cleanest 1-bit edge the
# key can hold. Scenery and road textures keep the global cap.
HIGH_HUD_LIMITS = {
    'guaa.tex': 512, 'huaa.tex': 512, 'iuaa.tex': 512, 'juaa.tex': 512,
    'kuaa.tex': 512, 'luaa.tex': 512, 'muaa.tex': 512, 'nuaa.tex': 512,
}


# BUILD 550. Archives whose texture mod may ship only SOME palettes of a
# multi-palette TEX; the missing ones are filled from vanilla
# (ff7nx_ddstex.vanilla_palette_rgba). SYW's submarine HUD is the case: it
# ships each atlas only in the palettes a sprite is drawn with, so under the
# all-or-nothing rule none of it was ever used. Palette 0 must be shipped.
PARTIAL_PALETTE_ARCHIVES = frozenset(('sub.lgp',))


# BUILD 552. The submarine HUD draws each native texel at ~1.5 screen
# pixels at 720p (TRIM: 9 native rows -> 13-14 px). At 3x (768) the GPU's
# nearest sampling reads 2 texels per pixel and drops every other one --
# the R lost its bowl, the M its second inner corner. Same cure as the
# highway HUD (BUILD 549): 2x (512) with the area reduction.
SUB_HUD_LIMIT = 512
SUB_SYW_ENV = 'SEVENTH_NX_SUB_SYW'


def _texture_limit(archive, name, default):
    if archive.lower() == 'sub.lgp':
        return min(SUB_HUD_LIMIT, default)
    if archive.lower().startswith('snowboard'):
        return SNOWBOARD_LIMITS.get(name.lower(), default)
    if archive.lower() == 'high-us.lgp':
        return HIGH_HUD_LIMITS.get(name.lower(), default)
    return default


def _smooth_for(archive, name):
    a = archive.lower()
    return a.startswith('snowboard') or a == 'sub.lgp' or (
        a == 'high-us.lgp' and name.lower() in HIGH_HUD_LIMITS)


def convert_archive(archive, vanilla_archive, mod_files, dds, mod,
                    mod_rank, log=lambda *_: None):
    """Return model-archive replacements, preserving later native mod wins.

    A failed or incomplete group never enters the returned map, so the native
    mod replacement (if any) or the game's own TEX remains intact.
    """
    import ff7nx_ddstex
    import ff7nx_condormap

    result = dict(mod_files)
    # BUILD 555: the submarine HUD keeps the GAME'S textures by default.
    # Hardware A/B (build 554): vanilla's pixel font reads cleanly; SYW's
    # sub art has uneven glyphs of its own (_mg/trim_src_cmp.png).
    # SEVENTH_NX_SUB_SYW=1 brings the SYW conversion back.
    if archive.lower() == 'sub.lgp' and os.environ.get(
            SUB_SYW_ENV, '0').strip().lower() not in ('1', 'on', 'yes',
                                                      'true'):
        log('  sub.lgp: SYW HUD not used (default; %s=1 enables it), game '
            'textures kept'
            % SUB_SYW_ENV)
        return result, {'converted': 0, 'partial': 0, 'missing': 0,
                         'shadowed': 0, 'refused': 0, 'marked': 0}
    grouped = {}
    for (name, palette), rel in dds.items():
        grouped.setdefault(name, {})[palette] = rel
    stats = {'converted': 0, 'partial': 0, 'missing': 0,
             'shadowed': 0, 'refused': 0, 'marked': 0}
    limit = cap()
    os.makedirs(CACHE_DIR, exist_ok=True)
    for name, paths in sorted(grouped.items()):
        native = result.get(name)
        if native is not None and mod_rank.get(native[1].filename, -1) > \
                mod_rank.get(mod.filename, -1):
            stats['shadowed'] += 1
            continue
        try:
            source = (open(native[0], 'rb').read() if native is not None else
                      _read_archive_entry(vanilla_archive, name))
        except OSError:
            source = None
        if source is None:
            stats['missing'] += 1
            continue
        shape = tex.parse(source)
        if shape is None:
            stats['refused'] += 1
            continue
        needed = set(range(shape['num_palettes']))
        fill = sorted(needed - set(paths))
        if fill and (archive.lower() not in PARTIAL_PALETTE_ARCHIVES
                     or 0 not in paths):
            stats['partial'] += 1
            continue
        # Extra DDS palettes do not exist in this archive and are not read.
        chosen = {p: paths[p] for p in sorted(needed) if p in paths}
        iro_stat = os.stat(mod.path)
        # BUILD 541: the snowboard's PSX-keyed archive gets an area
        # reduction (see ff7nx_ddstex._resample_area); everything else is
        # byte-identical to before, cache key included.
        _smooth = _smooth_for(archive, name)
        _snow = archive.lower().startswith('snowboard')
        limit = _texture_limit(archive, name, cap())
        key = hashlib.sha256(CONVERSION_VERSION + (b'area12' if _smooth
                                                   else b'') + source +
                             str(limit).encode() +
                             os.path.abspath(mod.path).encode() +
                             str((iro_stat.st_size, iro_stat.st_mtime_ns,
                                  ff7nx_ddstex.wide_palette(),
                                  ff7nx_ddstex.dds_alpha(),
                                  ff7nx_ddstex.reseam())).encode() +
                             '\n'.join(chosen.values()).encode() +
                             (('fill%r' % (fill,)).encode() if fill
                              else b'') +
                             (b'joint552' if archive.lower() in
                              PARTIAL_PALETTE_ARCHIVES else b'') +
                             (ff7nx_condormap.VERSION
                              if ff7nx_condormap.applies(archive, name)
                              else b'')).hexdigest()
        target = os.path.join(CACHE_DIR, key + '.tex')
        if not os.path.isfile(target):
            try:
                blobs = {p: iro.read_one(mod.path, rel)
                         for p, rel in chosen.items()}
                if any(b is None for b in blobs.values()):
                    raise ValueError('one or more DDS entries are missing')
                if fill:
                    # BUILD 552: the mod's palette-0 shape in vanilla's
                    # colours for the missing palette (was: vanilla art
                    # nearest-scaled, which read as blocky next to SYW).
                    _ref = ff7nx_ddstex._decode(blobs[0])[0]
                    for _p in fill:
                        blobs[_p] = ff7nx_ddstex.recolour_palette(
                            source, _ref, _p)
                    stats['filled'] = stats.get('filled', 0) + 1
                if archive.lower() in PARTIAL_PALETTE_ARCHIVES:
                    # BUILD 552: shipped palettes whose SHAPE disagrees with
                    # palette 0 (hudb 1-3: "ASSIST" over "TRIM") take the
                    # palette-0 shape recoloured, in those areas only.
                    _ref = ff7nx_ddstex._decode(blobs[0])[0]
                    for _p in list(blobs):
                        if _p == 0 or _p in fill:
                            continue
                        blobs[_p] = ff7nx_ddstex.reconcile_to_reference(
                            source, _ref,
                            ff7nx_ddstex._decode(blobs[_p])[0], _p)
                # BUILD 567: SYW's condor map4 sits 6 units low; see
                # ff7nx_condormap. Every other page measures (0, 0).
                blobs = ff7nx_condormap.fix_blobs(archive, name, source,
                                                  blobs, log)
                scale = ff7nx_ddstex.max_scale(source, limit)
                data, _note = ff7nx_ddstex.convert_group(
                    source, blobs, scale=scale, texture_name=name,
                    smooth_downscale=_smooth,
                    keep_palette=(_snow and name.lower() in
                                  SNOWBOARD_FACE),
                    joint_partition=(archive.lower() in
                                     PARTIAL_PALETTE_ARCHIVES),
                    preprocess=(ff7nx_ddstex.clean_sticker
                                if _snow and name.lower()
                                in SNOWBOARD_STICKERS else
                                ff7nx_ddstex.stretch_to_region
                                if _snow and name.lower() == 'eyes.tex'
                                else None))
                made = tex.parse(data)
                if made is None or made['width'] % shape['width'] or \
                        made['height'] % shape['height']:
                    raise ValueError('converted TEX has an invalid canvas')
                sx = made['width'] // shape['width']
                sy = made['height'] // shape['height']
                if (sx, sy) != (1, 1):
                    data = tex.mark_logical_scale(data, sx, sy)
                with open(target + '.tmp', 'wb') as f:
                    f.write(data)
                os.replace(target + '.tmp', target)
            except Exception as exc:  # leave source art intact on any failure
                stats['refused'] += 1
                log('  ! %s/%s: SYW minigame TEX refused: %s' %
                    (archive, name, exc))
                continue
        data = open(target, 'rb').read()
        if tex.parse(data) is None:
            stats['refused'] += 1
            continue
        result[name] = (target, mod)
        stats['converted'] += 1
        stats['marked'] += tex.logical_scale(data) is not None
    if dds:
        log('  %s: SYW minigame TEX: %d converted (%d resized), %d incomplete '
            'palette set(s) kept native, %d absent name(s), %d later native '
            'winner(s), %d refused' %
            (archive, stats['converted'], stats['marked'], stats['partial'],
             stats['missing'], stats['shadowed'], stats['refused']))
        if stats.get('filled'):
            log('  %s: %d of those had palettes SYW does not ship, filled '
                'as SYW palette 0 recoloured to vanilla (BUILD 552)' % (archive, stats['filled']))
    return result, stats

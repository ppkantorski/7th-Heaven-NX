#!/usr/bin/env python3
"""Cosmos Limit Break's HD field-prop textures, into char.lgp. BUILD 580.

Cosmos ships 76 FFNx model textures as `LIMIT BREAK\\char\\<tex>_<pal>.dds`
-- the pre-rendered billboard props the fields draw as 3D models: Round
Square's gondola and ticket hut (`gcjf`), and 75 more. FFNx loads them over
the model's TEX at runtime. Nothing here ever read them: extraction skips
FFNx DDS and no pass converted them, so every one of those props shipped as
its 1997 256px TEX (the "rough" gondola beside Cosmos's HD backgrounds).

This converts each complete palette set into the TEX the Switch loads, with
the same `ff7nx_ddstex.convert_group` the SYW minigame textures use:
indexed, same palette count, the art's own alpha as the colour key, at the
largest whole scale that fits `CAP` (768 by default; 256px props become
768). Field model UVs are normalised floats, so a bigger TEX needs no UV
bridge and carries no scale marker -- the same as Ninostyle's 512px skins.

A TEX some enabled mod already replaces is left alone (the mod's own art
wins), as is any set missing a palette the vanilla TEX has.

    SEVENTH_NX_CHAR_DDS=0          off
    SEVENTH_NX_CHAR_DDS_CAP=512    a smaller ceiling (256..1024)
"""
from __future__ import annotations

import hashlib
import os
import re

import iro
import tex

ENV = 'SEVENTH_NX_CHAR_DDS'
CAP_ENV = 'SEVENTH_NX_CHAR_DDS_CAP'
DEFAULT_CAP = 768
VERSION = b'char-dds-2'   # 618y: per-texture clean-ups (grcf)
ARCHIVE = 'char.lgp'
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'cache', '_char_dds')
_DDS = re.compile(r'([a-z0-9_]+)_(\d{2})\.dds')


# BUILD 618y. Per-texture clean-ups of Cosmos's upscale, applied to the
# decoded RGBA before conversion. gldelev's elevator (model GRCC, texture
# grcf), hardware 10-04: "the elevator model looks a bit rough ... rough
# edges". The light strip just under the box's top rim (rows 22..40 of the
# 1024px sheet) carries black squiggles -- upscaler garbage from tiny marks in
# the 1997 art -- and the rim samples exactly that strip. Texels there much
# darker than their row are replaced by the strip's own colour (a column-
# smoothed row median), so the rim reads as a clean light edge.
def _strip_clean(lo, hi, ratio=0.55, win=41):
    def run(img):
        import numpy as np
        from scipy import ndimage as ND
        a = np.array(img, dtype=np.float32, copy=True)
        h = a.shape[0]
        y0, y1 = int(round(lo * h)), int(round(hi * h))
        band = a[y0:y1, :, :3]
        lum = band.mean(-1)
        # local background: a wide horizontal median, so the darker panel
        # edges of the sheet are not mistaken for garbage
        bg = np.stack([ND.median_filter(band[..., c], size=(3, win))
                       for c in range(3)], -1)
        bad = lum < ratio * bg.mean(-1)
        if bad.any():
            bad = ND.binary_dilation(bad, np.ones((3, 3), bool))
            band[bad] = bg[bad]
            a[y0:y1, :, :3] = band
        return np.clip(np.rint(a), 0, 255).astype(np.uint8)
    return run


PREPROCESS = {'grcf.tex': _strip_clean(22 / 1024.0, 41 / 1024.0)}


def enabled(env=None):
    e = os.environ if env is None else env
    return e.get(ENV, '').strip().lower() not in ('0', 'off', 'no', 'false')


def cap(env=None):
    e = os.environ if env is None else env
    raw = e.get(CAP_ENV, '').strip()
    v = int(raw) if raw else DEFAULT_CAP
    if not 256 <= v <= 1024:
        raise ValueError('%s must be 256..1024' % CAP_ENV)
    return v


def collect(mods, settings_by_mod, char_names):
    """({(tex name, palette): iro path}, mod) -- the last mod that ships any.

    Folder order is `iro.active_folders`' last-write-wins order, so an
    option folder later in 7th Heaven's precedence cannot beat an earlier one.
    """
    if not enabled():
        return {}, None
    out, owner = {}, None
    for mod in mods:
        if getattr(mod, 'manifest', None) is None:
            continue
        try:
            entries = mod.entries()
        except Exception:                                      # noqa: BLE001
            continue
        if not any('\\char\\' in e.lower() or '/char/' in e.lower()
                   for e in entries if e.lower().endswith('.dds')):
            continue
        folders = iro.active_folders(mod.manifest,
                                     settings_by_mod.get(mod.filename, {}))
        found = {}
        for folder in folders:
            prefix = folder.replace('\\', '/').lower().rstrip('/') + '/char/'
            for rel in entries:
                low = rel.replace('\\', '/').lower()
                if not low.startswith(prefix) or '/' in low[len(prefix):]:
                    continue
                m = _DDS.fullmatch(low[len(prefix):])
                if not m:
                    continue
                name = m.group(1) + '.tex'
                if name in char_names:
                    found[(name, int(m.group(2)))] = rel
        if found:
            out.update(found)
            owner = mod
    return out, owner


def convert(mod_files, vanilla, dds, mod, log=lambda *_: None):
    """Return (mod_files, stats). `vanilla` maps lowercase name -> path."""
    import ff7nx_ddstex
    stats = {'converted': 0, 'kept_mod': 0, 'partial': 0, 'refused': 0,
             'names': []}
    if not dds or mod is None:
        return mod_files, stats
    result = dict(mod_files)
    grouped = {}
    for (name, pal), rel in dds.items():
        grouped.setdefault(name, {})[pal] = rel
    limit = cap()
    os.makedirs(CACHE_DIR, exist_ok=True)
    st = os.stat(mod.path)
    for name, paths in sorted(grouped.items()):
        if name in result:
            stats['kept_mod'] += 1
            continue
        vpath = vanilla.get(name)
        try:
            source = open(vpath, 'rb').read() if vpath else None
        except OSError:
            source = None
        shape = tex.parse(source) if source else None
        if shape is None:
            stats['refused'] += 1
            continue
        if set(range(shape['num_palettes'])) - set(paths):
            stats['partial'] += 1
            continue
        chosen = {p: paths[p] for p in range(shape['num_palettes'])}
        key = hashlib.sha256(
            VERSION + source + str(limit).encode()
            + os.path.abspath(mod.path).encode()
            + str((st.st_size, st.st_mtime_ns)).encode()
            + '\n'.join(chosen[p] for p in sorted(chosen)).encode()
        ).hexdigest()
        target = os.path.join(CACHE_DIR, key + '.tex')
        if not os.path.isfile(target):
            try:
                blobs = {p: iro.read_one(mod.path, rel)
                         for p, rel in chosen.items()}
                if any(b is None for b in blobs.values()):
                    raise ValueError('DDS entry missing')
                scale = ff7nx_ddstex.max_scale(source, limit)
                data, _note = ff7nx_ddstex.convert_group(
                    source, blobs, scale=scale, texture_name=name,
                    preprocess=PREPROCESS.get(name.lower()))
                made = tex.parse(data)
                if (made is None or made['width'] % shape['width']
                        or made['height'] % shape['height']
                        or made['num_palettes'] != shape['num_palettes']):
                    raise ValueError('converted TEX has an invalid shape')
                with open(target + '.tmp', 'wb') as f:
                    f.write(data)
                os.replace(target + '.tmp', target)
            except Exception as exc:                           # noqa: BLE001
                stats['refused'] += 1
                log('  ! char.lgp/%s: Cosmos HD prop refused: %s'
                    % (name, exc))
                continue
        result[name] = (target, mod)
        stats['converted'] += 1
        stats['names'].append(name[:-4])
    log('  char.lgp: Cosmos HD field props (BUILD 580): %d TEX converted '
        'at up to %dpx, %d already replaced by another mod (kept), %d '
        'incomplete palette set(s), %d refused%s'
        % (stats['converted'], limit, stats['kept_mod'], stats['partial'],
           stats['refused'],
           (' -- ' + ', '.join(stats['names'][:20])
            + (' ...' if len(stats['names']) > 20 else ''))
           if stats['names'] else ''))
    return result, stats

#!/usr/bin/env python3
"""
ff7nx_shaders.py -- ship the custom PIXEL shader sets from the build.

WHAT THESE ARE
==============
The port renders the field into a low-resolution offscreen (320x240 stock;
see `ff7nx_fieldbuf.py`) and reconstructs it on the way to the screen with a
2xSaI / HQ4x pair. Those two kernels are the "background scaler", and they
are what makes the pre-rendered maps look soft. `custom_shaders/` holds
drop-in replacements for them.

Until now they were copied to the SD card by hand, which is exactly how the
widescreen vertex shaders drifted out of step with the module (HANDOFF-49
§3). This module makes them a build setting, so the same "copy sdout to the
card" step carries them, `prune_stale` removes them when they are switched
off, and the build log says which set went on.

THE SETS
========
Scaler -- three files each, all replacing the same kernel:

    (stock)   the port's own 2xSaI / HQ4x. Nothing is written.
    hd        Catmull-Rom reconstruction + sharpen + anti-ring. Tuned by
              reconstructing downsampled flevel crops, so it is the one
              aimed at THIS game's art rather than at pixel art generally.
    xbr       level-1 edge-directed: crisp flats, smooth 45-degree edges.
    crisp     nearest neighbour. Raw pixels, no reconstruction at all.
    soft      plain bilinear. The mildest change, and the closest thing to
              an identity pass -- useful as a control.

Full-screen anti-aliasing -- one file:

    (stock)   the port's own FXAA.
    hd        the retuned FXAA from `custom_shaders/hd_fxaa`.
    off       `custom_shaders/fxaa_off`: FXAA disabled. Sharper overall and
              cheaper, jaggier 3D model edges.

Movie -- one file:

    (stock)   the port's own video shader.
    hd        `custom_shaders/hd_video`.

INTERACTION WITH THE FIELD BUFFER
=================================
Worth knowing before picking one. At `SEVENTH_NX_WS_FIELDBUF=1` the field
buffer is 428x240 and the background still lands 1:1, so the scaler is doing
all the magnification and the choice of kernel matters a lot. At scale 2 or
3 the hardware sampler has already magnified the art 2x or 3x before the
kernel sees it, so every kernel converges towards "smooth" and the choice
matters much less. If you run scale 3, `crisp` is the one worth trying --
the reconstruction has already happened.
"""
from __future__ import annotations

import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

SCALER_ENV = 'SEVENTH_NX_SCALER'
FXAA_ENV = 'SEVENTH_NX_FXAA'
VIDEO_ENV = 'SEVENTH_NX_VIDEO_SHADER'

CUSTOM_DIR = 'custom_shaders'

SCALER_FILES = ('2xsal_p.glsl', '2xsal_depth_p.glsl', 'hq4x_p.glsl')
FXAA_FILES = ('fxaanv5_p.glsl',)
VIDEO_FILES = ('video_p.glsl',)

# value -> (folder under custom_shaders/, files, human label)
SCALER_SETS = {
    '': (None, (), 'stock (the port’s own 2xSaI / HQ4x)'),
    'hd': ('hd', SCALER_FILES, 'HD — Catmull-Rom + sharpen'),
    'xbr': ('xbr', SCALER_FILES, 'xBR — edge-directed'),
    'crisp': ('crisp', SCALER_FILES, 'Crisp — nearest neighbour'),
    'soft': ('soft', SCALER_FILES, 'Soft — plain bilinear'),
}

FXAA_SETS = {
    '': (None, (), 'stock FXAA'),
    'hd': ('hd_fxaa', FXAA_FILES, 'HD FXAA'),
    'off': ('fxaa_off', FXAA_FILES, 'FXAA off'),
}

VIDEO_SETS = {
    '': (None, (), 'stock movie shader'),
    'hd': ('hd_video', VIDEO_FILES, 'HD movie shader'),
}

# Every file this module is ever allowed to write. `prune_stale` only
# removes files the build itself produced, so this list is what makes
# switching a set OFF actually delete the old one.
ALL_FILES = tuple(sorted(set(SCALER_FILES + FXAA_FILES + VIDEO_FILES)))


def _value(env, sets):
    """
    The set the environment selects, or '' for stock.

    Note what is NOT in the synonym list: `off`. It is a real value for the
    FXAA set (custom_shaders/fxaa_off, which actively disables it) and
    treating it as "stock" here would silently ship the port's own FXAA
    instead. Anything not in `sets` falls back to stock, which is the safe
    direction -- an unknown name ships nothing rather than half a set.
    """
    raw = (os.environ.get(env) or '').strip().lower()
    if raw in ('', 'stock', 'none', 'default'):
        return ''
    return raw if raw in sets else ''


def scaler():
    return _value(SCALER_ENV, SCALER_SETS)


def fxaa():
    return _value(FXAA_ENV, FXAA_SETS)


def video():
    return _value(VIDEO_ENV, VIDEO_SETS)


def enabled():
    return bool(scaler() or fxaa() or video())


def shader_dir(sdout):
    import build
    return os.path.join(sdout, 'atmosphere', 'contents', build.TITLE_ID,
                        'romfs', 'ff7', 'shaders')


def _install(kind, value, sets, dest_dir, log):
    folder, files, label = sets[value]
    if folder is None:
        return []
    src_dir = os.path.join(_HERE, CUSTOM_DIR, folder)
    missing = [f for f in files
               if not os.path.exists(os.path.join(src_dir, f))]
    if missing:
        # Half a set is worse than none: the three scaler files are one
        # kernel split across colour, depth and the 4x path, and mixing two
        # kernels produces an artefact nobody can attribute.
        log('! %s: %s missing from %s/%s -- refusing to ship half a set, '
            'so the port’s own shaders stay in place'
            % (kind, ', '.join(missing), CUSTOM_DIR, folder))
        return []
    os.makedirs(dest_dir, exist_ok=True)
    out = []
    for f in files:
        dest = os.path.join(dest_dir, f)
        shutil.copy2(os.path.join(src_dir, f), dest)
        out.append(dest)
    log('  %-22s %s   (%s)' % (kind + ':', label, ', '.join(files)))
    return out


def apply(sdout, log=lambda *_: None, produced=()):
    """
    Install whichever sets the environment selects. Returns the paths
    written, so `prune_stale` removes them again when a later build turns
    them off.

    Writes nothing at all when everything is stock, so a build with these
    settings untouched is byte-identical to one from before this existed.
    """
    picks = [('background scaler', scaler(), SCALER_SETS),
             ('anti-aliasing', fxaa(), FXAA_SETS),
             ('movie shader', video(), VIDEO_SETS)]
    if not any(v for _, v, _ in picks):
        return []
    dest_dir = shader_dir(sdout)
    log('')
    log('installing custom pixel shaders ...')
    out = []
    for kind, value, sets in picks:
        out += _install(kind, value, sets, dest_dir, log)
    if out:
        log('  into %s'
            % os.path.relpath(dest_dir, sdout).replace(os.sep, '/'))
        if scaler() in ('crisp', 'soft'):
            log('  note: %s is a control, not a quality setting -- it '
                'removes the reconstruction rather than improving it.'
                % scaler())
    return out


def describe():
    """One line per active set, for a build summary."""
    out = []
    for kind, value, sets in (('background scaler', scaler(), SCALER_SETS),
                              ('anti-aliasing', fxaa(), FXAA_SETS),
                              ('movie shader', video(), VIDEO_SETS)):
        out.append('  %-20s %s' % (kind, sets[value][2]))
    return out


if __name__ == '__main__':
    print('scaler = %r  fxaa = %r  video = %r'
          % (scaler(), fxaa(), video()))
    for line in describe():
        print(line)

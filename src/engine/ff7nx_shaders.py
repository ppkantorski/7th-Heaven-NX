#!/usr/bin/env python3
"""
ff7nx_shaders.py -- ship the custom PIXEL shader sets from the build.

WHAT THESE ARE
==============
The port renders the field into a low-resolution offscreen (320x240 stock;
see `ff7nx_fieldbuf.py`) and reconstructs it on the way to the screen with a
2xSaI / HQ4x pair. Those two kernels are the "background scaler", and they
are what makes the pre-rendered maps look soft. `shaders/` holds
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
    crisp     nearest neighbour. Raw pixels, no reconstruction at all, and
              NO GRADING -- blacks keep the 8/255 lift a truecolor page has
              to store them with. Sharp, but a known-incomplete control.
    sharp     `crisp`'s point sample, CLAMPED so it cannot read outside the
              rect, plus `hd`'s black point and saturation. This is the one
              to use at scale 3: the reconstruction has already happened, so
              the only thing worth keeping from `hd` is the grading.
    soft      plain bilinear. The mildest change, and the closest thing to
              an identity pass -- useful as a control.

Full-screen anti-aliasing -- one file:

    (stock)   the port's own FXAA.
    hd        the retuned FXAA from `shaders/hd_fxaa`.
    off       `shaders/fxaa_off`: FXAA disabled. Sharper overall and
              cheaper, jaggier 3D model edges.

Movie -- one file:

    (stock)   the port's own video shader.
    hd        `shaders/hd_video`.

INTERACTION WITH THE FIELD BUFFER
=================================
Worth knowing before picking one. At `SEVENTH_NX_WS_FIELDBUF=1` the field
buffer is 428x240 and the background still lands 1:1, so the scaler is doing
all the magnification and the choice of kernel matters a lot. At scale 2 or
3 the hardware sampler has already magnified the art 2x or 3x before the
kernel sees it, so every kernel converges towards "smooth" and the choice
matters much less. If you run scale 3, `crisp` is the one worth trying --
the reconstruction has already happened.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: run directly, find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
if _nx_os.path.join(_nx_root, 'src') not in _nx_sys.path:
    _nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import os
import re
import shutil
import sys

_HERE = nxpaths.ROOT
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

SCALER_ENV = 'SEVENTH_NX_SCALER'
FXAA_ENV = 'SEVENTH_NX_FXAA'
VIDEO_ENV = 'SEVENTH_NX_VIDEO_SHADER'
# BUILD 805: the background scaler's black point, in 255ths. The HD sets
# (hd, hd_lite, hd_bilinear) ship HD_BLACK_POINT = 12/255 and `sharp` ships
# 8/255; this lets a developer write a lower value into the copy that goes to
# sdout, to calibrate it. A shader's own value is its ceiling: the setting
# can only lower it, so the default (12, the HD value) leaves every file
# byte-identical to its source, `sharp` included.
BLACK_POINT_ENV = 'SEVENTH_NX_HD_BLACK_POINT'
BLACK_POINT_MAX = 12
BLACK_POINT_DEFAULT = BLACK_POINT_MAX
_BLACK_POINT_RE = re.compile(
    r'^(const float HD_BLACK_POINT = )([0-9.]+);.*$', re.M)

CUSTOM_DIR = 'shaders'

SCALER_FILES = ('2xsal_p.glsl', '2xsal_depth_p.glsl', 'hq4x_p.glsl')
# The HD family's depth pass is the port's own 2xsal_depth_p.glsl byte for
# byte, so it is not shipped: a file we do not write IS the native one (the
# game reads its own romfs copy). Shipping a native shader would also mean
# redistributing the game's file. `prune_stale` still removes a copy an
# older build left behind, because ALL_FILES keeps the full list.
SCALER_FILES_NATIVE_DEPTH = ('2xsal_p.glsl', 'hq4x_p.glsl')
FXAA_FILES = ('fxaanv5_p.glsl',)
VIDEO_FILES = ('video_p.glsl',)

# value -> (folder under shaders/, files, human label)
SCALER_SETS = {
    '': (None, (), 'stock (the port’s own 2xSaI / HQ4x)'),
    'hd': ('hd', SCALER_FILES_NATIVE_DEPTH, 'HD — Catmull-Rom + sharpen'),
    'xbr': ('xbr', SCALER_FILES, 'xBR — edge-directed'),
    'crisp': ('crisp', SCALER_FILES, 'Crisp — nearest neighbour'),
    # FINDINGS-251. `crisp` proved the direction right -- the sky/girder edge
    # ramp went 1.39 px -> 0.91 px against FFNx's 0.97 -- but it ships two
    # defects: it samples with an UNCLAMPED floor (at 1:1 the texel and pixel
    # grids align, so fp noise picks texel N or N-1 and at a rect boundary
    # that reads outside it) and it drops `hd_grade_rgb` entirely, so
    # HD_BLACK_POINT never cancels NEAR_BLACK's 8/255 lift. `sharp` is the
    # same single tap, clamped to the stock kernel's own tap span, with the
    # grading kept verbatim.
    'sharp': ('sharp', SCALER_FILES, 'Sharp — point sample + HD grading'),
    'soft': ('soft', SCALER_FILES, 'Soft — plain bilinear'),
    # The HD 2xSaI files with a cheaper hq4x pass. hd_lite is the shipped
    # default since the GPU tests (BUILD 815: no longer labelled a test).
    'hd_lite': ('hq4x_lite', SCALER_FILES_NATIVE_DEPTH, 'HD lite — 10-read hq4x'),
    'hd_bilinear': ('hq4x_bilinear', SCALER_FILES_NATIVE_DEPTH, 'HD bilinear — 1-read hq4x'),
}

FXAA_SETS = {
    '': (None, (), 'stock FXAA'),
    'hd': ('hd_fxaa', FXAA_FILES, 'HD FXAA'),
    'off': ('fxaa_off', FXAA_FILES, 'FXAA off'),
    'hd_fast': ('hd_fxaa_fast', FXAA_FILES, 'HD FXAA fast — 12-step search'),
}

VIDEO_SETS = {
    '': (None, (), 'stock movie shader'),
    'hd': ('hd_video', VIDEO_FILES, 'HD movie shader'),
}

# Every file this module is ever allowed to write. `prune_stale` only
# removes files the build itself produced, so this list is what makes
# switching a set OFF actually delete the old one.
ALL_FILES = tuple(sorted(set(SCALER_FILES + FXAA_FILES + VIDEO_FILES)))


def release_files():
    """
    Every shader file a build can install, relative to the project folder:
    the sets above plus the wide-screen pair (ff7nx_ws). The release build
    ships exactly these, so stock/, the old experiments and anything else
    under shaders/ stay out of the zip.
    """
    out = set()
    for sets in (SCALER_SETS, FXAA_SETS, VIDEO_SETS):
        for folder, files, _ in sets.values():
            if folder:
                out.update('/'.join((CUSTOM_DIR, folder, f)) for f in files)
    import ff7nx_ws
    out.update('/'.join((ff7nx_ws.WS_SHADER_SET.replace(os.sep, '/'), f))
               for f in ff7nx_ws.WS_SHADERS)
    return sorted(out)


def _value(env, sets):
    """
    The set the environment selects, or '' for stock.

    Note what is NOT in the synonym list: `off`. It is a real value for the
    FXAA set (shaders/fxaa_off, which actively disables it) and
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


def black_point():
    """The black point the environment asks for, 0..BLACK_POINT_MAX
    (255ths), or None (unset or not a number) for each shader's own."""
    try:
        n = int((os.environ.get(BLACK_POINT_ENV) or '').strip())
    except ValueError:
        return None
    return max(0, min(n, BLACK_POINT_MAX))


def with_black_point(text, n):
    """
    `text` with its HD_BLACK_POINT lowered to n/255, and whether the shader
    has one. A value at or above the shader's own leaves `text` as it is.
    """
    m = _BLACK_POINT_RE.search(text)
    if m is None:
        return text, False
    if n / 255.0 >= float(m.group(2)) - 1e-9:
        return text, True
    line = '%s%.11f;   // %d/255 (Settings > Developer > Black level)' % (
        m.group(1), n / 255.0, n)
    return text[:m.start()] + line + text[m.end():], True


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
    src_dir = os.path.join(nxpaths.APP, CUSTOM_DIR, folder)
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
    bp = black_point() if sets is SCALER_SETS else None
    bp_hit = False
    for f in files:
        dest = os.path.join(dest_dir, f)
        shutil.copy2(os.path.join(src_dir, f), dest)
        if bp is not None:
            # BUILD 805: the sdout copy gets the chosen black point; the
            # source under shaders/ is never touched.
            with open(dest, encoding='utf-8') as fh:
                text, hit = with_black_point(fh.read(), bp)
            if hit:
                with open(dest, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(text)
                bp_hit = True
        # BUILD 591: dated by the build, not the source (copy2 kept the
        # source's date, so a rebuilt set looked untouched in sdout and a
        # copy-by-date to the card skipped it)
        os.utime(dest, None)
        out.append(dest)
    log('  %-22s %s   (%s)' % (kind + ':', label, ', '.join(files)))
    if bp is not None and bp < BLACK_POINT_MAX:
        log('  %-22s %s' % ('black point:', '%d/255' % bp if bp_hit else
                            'not used by this set (it has no black point)'))
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

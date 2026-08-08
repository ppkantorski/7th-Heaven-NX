#!/usr/bin/env python3
"""
test_movie_scale.py -- the movie is sized for what the console DRAWS.

WHY THIS EXISTS
===============
"Movies look lower resolution than the files" was chased for a whole session
on the assumption that something in the packer was downscaling. Nothing in
the packer was: `movies.py`'s only filter was an even-dimension guard, and
that was true and documented and checked. The downscale is on the CONSOLE.

`romfs/shaders/video_p.glsl` does exactly one `texture()` fetch per plane.
There is no mip chain and no reconstruction filter anywhere in the movie
path, so a movie larger than the box the port draws it into is minified by
four texels per output pixel regardless of how many texels that pixel really
covers. That is a box filter with a 2x2 aperture: it discards most of an
oversized source and aliases what is left.

So the size the file must be is not "as big as possible". It is the size the
port draws, which this test pins down, and which is derived from constants
that live in `exefs/main`.

WHAT IS CHECKED, AND WHY IT IS CHECKED THIS WAY
===============================================
1. THE GEOMETRY IS MEASURED, NOT REMEMBERED.  Every constant in
   `movies.py`'s geometry block is asserted against the instruction word at
   its address in the module. If a game update moves the movie path, this
   fails loudly instead of the packer quietly resampling to a size the port
   no longer uses. This is `verify_dispatch_signature`'s 'assert' mechanism
   applied to a different file, for the same reason: it has caught wrong
   constants five times.

   Skipped, not failed, when no dump is present -- the maths below still
   runs, and CI without a dump is a normal thing.

2. THE SIZING NEVER UPSCALES.  Adding pixels a source does not have costs
   bitrate and buys nothing. Magnification is the shader's job.

3. THE ASPECT RATIO NEVER MOVES.  One scale factor for both axes. The draw
   path derives the quad's height from w/h, so a changed aspect is a changed
   picture.

4. FFMPEG REALLY PRODUCES IT.  A command line that says 1440x1008 is not
   evidence that 1440x1008 came out. The test encodes an actual oversized
   clip and probes the result. (Skipped when ffmpeg is absent.)

5. THE RESAMPLE IS WORTH DOING.  A GL_LINEAR minification is simulated
   exactly -- four taps at the output pixel centre, no mip -- and compared
   against the Lanczos resample. If the gap ever collapses, the feature is
   not earning its complexity and this says so.
"""
import os
import struct
import subprocess
import sys
import tempfile

# Runnable both as `python3 tests/test_movie_scale.py` and under pytest
# from the project root, so the project root goes on the path either way.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import movies as mv                                          # noqa: E402

FAILURES = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILURES.append(msg)


def find_main():
    """exefs/main from the usual places, or None."""
    here = os.path.dirname(_HERE)
    for rel in (('dump', 'exefs', 'main'),
                ('..', 'dump', 'exefs', 'main'),
                ('sdout', 'atmosphere', 'contents', '0100A5B00BDC6000',
                 'exefs', 'main')):
        p = os.path.join(here, *rel)
        if os.path.exists(p):
            return p
    p = os.environ.get('SEVENTH_NX_MAIN', '')
    return p if p and os.path.exists(p) else None


# --------------------------------------------------------------------- 1
def test_geometry_against_the_module():
    print('\n1. the geometry constants are what is in exefs/main')
    path = find_main()
    if not path:
        print('  skip  no exefs/main found '
              '(set SEVENTH_NX_MAIN to check this)')
        return
    try:
        import nxmap
        img = nxmap.Main(path).img
    except Exception as exc:                                 # noqa: BLE001
        print('  skip  could not load the module: %s' % exc)
        return

    for off, want in sorted(mv.GEOMETRY_ASSERTS.items()):
        got = struct.unpack('<I', img[off:off + 4])[0]
        check(got == want,
              '+0x%07X is 0x%08X as expected' % (off, want)
              if got == want else
              '+0x%07X is 0x%08X, expected 0x%08X -- the movie path has '
              'moved and movies.py\'s geometry is no longer measured'
              % (off, got, want))

    # The two float constants the in-game quad is built from live in
    # .rodata rather than in an immediate, so they are read as floats.
    for off, want in ((0x11AE7A8, 640.0), (0x11AE764, 480.0)):
        got = struct.unpack('<f', img[off:off + 4])[0]
        check(got == want,
              '+0x%07X is %.1ff (the quad\'s %s)'
              % (off, want, 'width' if want == 640.0 else 'height cap'))


# --------------------------------------------------------------------- 2
def test_never_upscales():
    print('\n2. sizing never adds pixels the source does not have')
    for mode in ('fit', 'screen'):
        for w, h in ((320, 224), (640, 448), (960, 672), (640, 480)):
            ew, eh, reason = mv.encode_size(w, h, mode)
            check(ew <= w and eh <= h,
                  '%-6s %dx%d stays %dx%d (%s)' % (mode, w, h, ew, eh,
                                                   reason))


def test_display_footprint():
    print('\n2b. the measured display box')
    check((mv.DISPLAY_W, mv.DISPLAY_H) == (960, 672),
          'the panel shows a %dx%d picture (measured from four captures)'
          % (mv.DISPLAY_W, mv.DISPLAY_H))
    check(mv.display_footprint(1280, 896) == (960, 672),
          'a 1280x896 FMV lands on exactly 960x672 screen pixels')
    lost = 1.0 - (960.0 * 672) / (1280 * 896)
    check(abs(lost - 0.4375) < 1e-6,
          'which is %.0f%% of the file\'s pixels discarded' % (100 * lost))
    check(mv.display_footprint(1920, 1080) == (960, 540),
          'a 16:9 file is letterboxed into the same box')


# --------------------------------------------------------------------- 3
def test_aspect_is_preserved():
    print('\n3. the aspect ratio survives the resample')
    for w, h in ((1920, 1344), (2560, 1792), (2880, 2016), (3840, 2160),
                 (1920, 1080), (1600, 1200)):
        ew, eh, reason = mv.encode_size(w, h, 'fit')
        src_ar = w / float(h)
        out_ar = ew / float(eh)
        # one rounding step to even on each axis is the whole error budget
        check(abs(src_ar - out_ar) < 0.005,
              '%dx%d -> %dx%d  aspect %.4f -> %.4f (%s)'
              % (w, h, ew, eh, src_ar, out_ar, reason))
        dw, dh = mv.device_footprint(w, h)
        check(ew <= dw and eh <= dh,
              '%dx%d fits inside the %dx%d the console draws' % (ew, eh,
                                                                 dw, dh))


# --------------------------------------------------------------------- 3b
def test_footprint_matches_the_draw_path():
    print('\n4. the footprint is the draw path\'s own arithmetic')
    # In-game: quad is 640 x min(640*h/w, 480) in FF7's 640x480 space, and
    # gfx_drv_setviewport scales that space by TARGET/GAME on each axis.
    for w, h in ((1280, 896), (1920, 1344), (640, 448), (1280, 720)):
        quad_h = min(mv.GAME_W * h / float(w), float(mv.GAME_H))
        want_w = mv.TARGET_W * mv.GAME_W / float(mv.GAME_W)
        want_h = quad_h * mv.TARGET_H / float(mv.GAME_H)
        # full screen path: full height of the back buffer
        fs_w = mv.SCREEN_H * w / float(h)
        got = mv.device_footprint(w, h)
        expect = (int(round(max(want_w, fs_w))),
                  int(round(max(want_h, float(mv.SCREEN_H)))))
        check(got == expect,
              '%dx%d -> %s device px (in-game %.0fx%.0f, full-screen '
              '%.0fx%d)' % (w, h, got, want_w, want_h, fs_w, mv.SCREEN_H))

    check(mv.TARGET_W == 1440 and mv.TARGET_H == 1080,
          'render target is %dx%d' % (mv.TARGET_W, mv.TARGET_H))
    check(mv.device_footprint(1280, 896) == (1440, 1008),
          'the port\'s own 1280x896 shape is drawn at 1440x1008')


# --------------------------------------------------------------------- 5
def test_colour_plan():
    print('\n5. colour is normalised to what video_p.glsl hardcodes')
    cases = [
        ({'color_space': '', 'color_range': ''}, 'bt601', 'tv', True),
        ({'color_space': 'bt470bg', 'color_range': 'tv'}, 'bt601', 'tv', True),
        ({'color_space': 'smpte170m', 'color_range': ''}, 'bt601', 'tv', True),
        ({'color_space': 'bt709', 'color_range': 'tv'}, 'bt709', 'tv', False),
        ({'color_space': 'bt709', 'color_range': 'pc'}, 'bt709', 'pc', True),
    ]
    for info, m, r, conv in cases:
        got = mv.colour_plan(info, 'bt709')
        check(got == (m, r, conv),
              '%-30s -> %s' % (info, got))
    check(mv.colour_plan({'color_space': ''}, 'off') == (None, None, False),
          'the "off" setting really does nothing')


# --------------------------------------------------------------------- 6
def test_filter_string():
    print('\n6. exactly one scale filter, so exactly one resample')
    vf, (w, h, why) = mv.video_filter(
        {'width': 2560, 'height': 1792, 'color_space': '',
         'color_range': ''}, 'fit', 'bt709')
    check(vf.count('scale=') == 1, 'one scale filter: %s' % vf)
    check('flags=lanczos' in vf, 'lanczos is asked for')
    check('out_color_matrix=bt709' in vf, 'output matrix is BT.709')
    check((w, h, why) == (1440, 1008, 'fit'), 'resolves to 1440x1008')

    vf2, _ = mv.video_filter(
        {'width': 1280, 'height': 896, 'color_space': 'bt709',
         'color_range': 'tv'}, 'fit', 'bt709')
    check('lanczos' not in vf2 and 'color_matrix' not in vf2,
          'a file that is already right gets a bare %s' % vf2)


# --------------------------------------------------------------------- 7
def test_real_encode():
    print('\n7. ffmpeg really produces the size that was asked for')
    if not mv.have_ffmpeg():
        print('  skip  ffmpeg/ffprobe not installed')
        return
    tmp = tempfile.mkdtemp(prefix='moviescale')
    src = os.path.join(tmp, 'src.mp4')
    subprocess.run(
        ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi',
         '-i', 'testsrc2=size=1920x1344:rate=30:duration=1',
         '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
         '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20',
         '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', src],
        check=True, capture_output=True)

    r = mv.convert(src, os.path.join(tmp, 'fit.mp4'), quality='high',
                   target_fps=30, fit='fit', colour='bt709')
    check((r['out']['width'], r['out']['height']) == (1440, 1008),
          'fit -> %dx%d' % (r['out']['width'], r['out']['height']))
    check(r['out']['color_space'] == 'bt709',
          'output is tagged bt709 (got %r)' % r['out']['color_space'])
    check(r['fit_reason'] == 'fit', 'reported as a fit')

    n = mv.convert(src, os.path.join(tmp, 'native.mp4'), quality='high',
                   target_fps=30, fit='native', colour='off')
    check((n['out']['width'], n['out']['height']) == (1920, 1344),
          'native -> %dx%d (the old behaviour is still reachable)'
          % (n['out']['width'], n['out']['height']))

    fit_mb = os.path.getsize(os.path.join(tmp, 'fit.mp4')) / 1e6
    nat_mb = os.path.getsize(os.path.join(tmp, 'native.mp4')) / 1e6
    check(fit_mb < nat_mb,
          'and it is smaller: %.2f MB vs %.2f MB' % (fit_mb, nat_mb))


# --------------------------------------------------------------------- 8
def test_resample_beats_the_gpu():
    print('\n8. the resample is worth doing (GL_LINEAR simulated exactly)')
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        print('  skip  numpy/pillow not installed')
        return

    def gl_linear(a, ow, oh):
        """GL_LINEAR, no mipmaps: four taps at the output pixel centre,
        whatever the minification ratio. This is what video_p.glsl gets."""
        ih, iw = a.shape[:2]
        x = (np.arange(ow) + 0.5) * iw / ow - 0.5
        y = (np.arange(oh) + 0.5) * ih / oh - 0.5
        x0 = np.floor(x).astype(int)
        y0 = np.floor(y).astype(int)
        fx = (x - x0)[None, :]
        fy = (y - y0)[:, None]

        def c(v, n):
            return np.clip(v, 0, n - 1)

        p00 = a[c(y0, ih)][:, c(x0, iw)]
        p10 = a[c(y0, ih)][:, c(x0 + 1, iw)]
        p01 = a[c(y0 + 1, ih)][:, c(x0, iw)]
        p11 = a[c(y0 + 1, ih)][:, c(x0 + 1, iw)]
        return (p00 * (1 - fx) + p10 * fx) * (1 - fy) + \
               (p01 * (1 - fx) + p11 * fx) * fy

    # A deterministic source with real high-frequency content.
    rng = np.random.RandomState(7)
    yy, xx = np.mgrid[0:1792, 0:2560]
    big = np.clip(120 + 100 * np.sin(xx / 3.0) * np.sin(yy / 3.7)
                  + 20 * rng.rand(1792, 2560), 0, 255)

    # What a correct resample gives -- which is also exactly what the packer
    # now ships, so the packer's own error against this is zero by
    # construction. The number that matters is how far the CONSOLE's own
    # result lands from it when it is handed the oversized file instead.
    ideal = np.asarray(
        Image.fromarray(big.astype(np.uint8)).resize(
            (1440, 1008), Image.LANCZOS), np.float64)
    gpu = gl_linear(big, 1440, 1008)

    mse = ((ideal - gpu) ** 2).mean()
    psnr = 10 * np.log10(255.0 ** 2 / mse) if mse else 99.0

    def hf(v):
        return (np.abs(np.diff(v, axis=0)).mean()
                + np.abs(np.diff(v, axis=1)).mean())

    detail = hf(gpu) / hf(ideal)
    check(psnr < 45.0,
          'GL_LINEAR at 1.78x minification is %.1f dB from a correct '
          'resample' % psnr)
    # Reported, not asserted. Which direction the high-frequency energy
    # moves depends on the picture: below ~1.5x minification GL_LINEAR
    # blurs (energy falls), above it the four-tap aperture undersamples and
    # energy RISES as aliasing -- which on a moving image reads as crawl.
    # Either way it is wrong, and the dB above is the claim being made.
    print('  info high-frequency energy %.0f%% of correct (%s)'
          % (100 * detail, 'aliasing' if detail > 1.0 else 'softening'))


def main():
    print(__doc__.strip().split('\n')[0])
    test_geometry_against_the_module()
    test_never_upscales()
    test_display_footprint()
    test_aspect_is_preserved()
    test_footprint_matches_the_draw_path()
    test_colour_plan()
    test_filter_string()
    test_real_encode()
    test_resample_beats_the_gpu()
    print()
    if FAILURES:
        print('FAILED (%d)' % len(FAILURES))
        for f in FAILURES:
            print('   ' + f)
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())

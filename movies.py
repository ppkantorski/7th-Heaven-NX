"""
Movie conversion for the Switch port.

WHAT THE PORT PLAYS
===================
`exefs/main` asks for movies as

    %s/data/movies/%s.mp4

and the file it ships for `southmk` is:

    H.264 High profile, level 3.2, yuv420p, 1280x896, 15 fps, 242 frames
    AAC-LC 44100 Hz stereo, ~128 kbps
    mp4 container

PC FMV mods are not that. Cosmos FMV names its files `.avi`, but they are
actually WebM: VP8 video with Vorbis audio, because FFNx decodes movies with
its own ffmpeg build and does not care about the container. Handing one of
those to the Switch port gets you nothing -- there is no VP8 decoder and no
matroska demuxer in it.

So the mod's video has to be re-encoded into the shape the port already
plays. That is what this module does, using ffmpeg.

FRAME RATE
==========
Whatever the mod ships is what gets built. A 30 fps FMV pack is installed for
the 30 fps, and there is no setting to throw that away.

This was briefly a question because FF7 hands the current movie frame to game
code and the field module uses it to sync what it draws over a playing movie,
so a 30 fps replacement makes that counter run twice as fast. FFNx hit the
same thing and dealt with it in `ceil(movie_fps / 15.0f)`, which it applies to
the opening movie's music cue and its own widescreen keyframes -- and nowhere
else. It never divides the frame counter back down. High-fps movies have been
feeding FF7 doubled frame numbers on PC for years, so the game tolerates it.

QUALITY
=======
H.264 at the quality level the user picks. TRUE LOSSLESS IS NOT OFFERED, and
that is a hardware limit rather than a preference:

    $ x264 --profile high --crf 0
    x264 [error]: high profile doesn't support lossless

Lossless H.264 requires the High 4:4:4 Predictive profile. Encoding this
project's 16-second reference clip that way produces a 34.8 MB file in a
profile the Switch's video decoder does not handle, against 7.5 MB for the
file the port itself ships. It would not play.

WHERE THE PICTURE ACTUALLY LANDS  (measured out of exefs/main, 1.03_5)
=====================================================================
Movies are not blitted; they are a textured quad, and the port draws that
quad into a fixed-size surface. Every number below was read out of the
module -- the addresses are module offsets and `tests/test_movie_scale.py`
asserts the instruction words at each one, so a different build fails the
test instead of silently scaling to the wrong size.

    +0x10FB090   mov w0,#0x500   ; screen width   = 1280, HARDCODED
    +0x10FB0A0   mov w0,#0x2D0   ; screen height  =  720, HARDCODED

`gfx_drv_init` (+0x10D5150) manufactures a 4:3 logical width from that and
supersamples it by 1.5:

    logical width  = 720 * 4/3 = 960      (+0x10D5284 magic /240, +0x10D52A4)
    render target  = 960 * 1.5 = 1440     (+0x10D528C fmov s0,#1.5)
                     720 * 1.5 = 1080

So the scene render target is 1440x1080 and the panel is 1280x720.

There are TWO movie draw paths, and they draw into different surfaces:

  * IN-GAME (`fw_movie_update` +0x10F1590 -> +0x10DE7C0).  The quad is built
    in FF7's own 640x480 coordinate space:

        width  = 640.0                     (+0x10DE81C  mov w11,#0x44200000)
        height = 640.0 * h / w, capped at 480.0
                                           (+0x10DE82C  mov w10,#0x43F00000)

    `gfx_drv_setviewport` (+0x10D6760) scales that space onto the render
    target by 1440/640 = 1080/480 = 2.25, so the movie occupies

        1440 x min(1440 * h / w, 1080)     DEVICE PIXELS

  * FULL SCREEN (the port's own player, +0x6100 -> +0x10E0390).  This one
    binds the BACK BUFFER, not the render target, and fits the quad to the
    panel's aspect (+0x10E04CC reads screen w/h):

        720 * w/h  x  720                  DEVICE PIXELS

The largest a movie is ever drawn is therefore 1440x1080, and for the
1280x896 shape the port itself ships it is 1440x1008.

WHY THAT MATTERS -- THIS IS THE DOWNSCALE
=========================================
`romfs/shaders/video_p.glsl` does exactly ONE `texture()` fetch per plane.
There is no reconstruction filter and no mip chain anywhere in the movie
path, so a movie LARGER than the box above is minified by plain bilinear:
four texels per output pixel regardless of how many texels the pixel really
covers. At around 2x minification -- a 2880x2016 or even a 1920x1344 pack --
that discards most of the source and aliases what is left, which is exactly
the "the file is huge but it looks low resolution" complaint. The project's
own `hd_video` shader deliberately fades ITSELF out when minifying, for the
same reason, so nothing downstream recovers it.

Resampling on the PC with Lanczos and shipping the size the console actually
draws is strictly better than shipping more pixels for the GPU to throw away
with a box filter. `FIT_DEFAULT` does that; `fit='native'` restores the old
"whatever the mod ships" behaviour for A/B.

COLOUR
======
The port's shader hardcodes the BT.709 limited-range matrix. FF7's FMVs are
320x224 standard definition, i.e. BT.601, and an upscale pack made from them
is BT.601 or tagged unspecified (which ffmpeg treats as BT.601). Decoding
BT.601 through a BT.709 matrix costs up to 39/255 on saturated colour while
leaving grey exact, so it reads as weak saturation rather than as a colour
bug. The conversion belongs at encode time, once, not in a shader that also
serves the port's own already-BT.709 files -- see
`custom_shaders/hd_video/README-hd-video.txt`, which reached the same
conclusion and asked for it here.

So the ladder below is the playable range, measured on that clip (SSIM against
the source with frames aligned; the source is VP8 at 1.5 Mbps and the port's
own mp4 is 7.5 MB):

    crf 10   12.0 MB   0.99768      the practical ceiling
    crf 14    7.0 MB   0.99647      about the bitrate the port itself uses
    crf 18    4.2 MB   0.99509
    crf 23    2.3 MB   0.99282

The x264 preset stays at `veryfast` for all of them. Preset trades encoding
TIME for SIZE, not quality: `slow` at crf 12 scored 0.99763 in 7.58s and 8.8
MB, while `veryfast` at crf 10 scored HIGHER (0.99768) in 1.90s. Quality comes
from the crf, so that is what the setting moves.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess

# Containers a PC FMV mod might ship. `.avi` is on this list because that is
# what Cosmos FMV calls its WebM files; the extension is not evidence of
# anything and the contents are probed, never assumed.
MOVIE_EXT = {'.avi', '.mp4', '.webm', '.mkv', '.mov', '.mpg', '.mpeg', '.m4v'}

MOVIE_DIR = 'data/movies'

# --------------------------------------------------------------------------
# The port's movie geometry. Every constant here is MEASURED out of
# exefs/main -- see the module docstring for the address of each one, and
# tests/test_movie_scale.py for the assertions that keep them honest.
# --------------------------------------------------------------------------
SCREEN_W = 1280                 # +0x10FB090  mov w0,#0x500
SCREEN_H = 720                  # +0x10FB0A0  mov w0,#0x2D0
SUPERSAMPLE = 1.5               # +0x10D528C  fmov s0,#1.5
LOGICAL_W = SCREEN_H * 4 // 3   # 960  -- +0x10D5284 magic /240
TARGET_W = int(LOGICAL_W * SUPERSAMPLE)   # 1440  render target width
TARGET_H = int(SCREEN_H * SUPERSAMPLE)    # 1080  render target height
GAME_W, GAME_H = 640, 480       # +0x10DE81C / +0x10DE82C, the quad's space

# MEASURED ON HARDWARE, not derived. Four Atmosphere captures (two FMV, two
# field, 1280x720 each) all put the game's picture in exactly the same box:
#
#     x 160..1119   ->  960 wide     (a 4:3 pillarbox in a 1280 panel)
#     y  24..695    ->  672 tall     (FF7 draws 448 of its 480 rows)
#
# and a round-trip test -- resample the capture down to a candidate size and
# back up, and see what it costs -- found NO plateau below 960x672:
#
#     through  640x448   39-53 dB      so the frame is not a 640 upscale
#     through  800x560   42-56 dB
#     through  960x672   lossless      the frame is information-native here
#
# So the panel really is handed a 960x672 picture, and every FMV in the pack
# is 1280x896. 960*672 / (1280*896) = 0.5625: only 56% of the file's pixels
# can reach the screen, and the other 44% are discarded by video_p.glsl's
# single bilinear tap. THAT is "it does not look like the mp4".
#
# Nothing in the packer can widen this box -- it is 4:3 on a 720p panel.
# Only 16:9 would (1280x720 = 1.43x the pixels). What the packer CAN do is
# make the one resample that does happen a good one.
DISPLAY_W, DISPLAY_H = 960, 672

# Instruction words at the addresses above, as they are in the shipping
# module. The test reads these back; a build whose words differ has moved
# the geometry and must not be scaled to these numbers.
GEOMETRY_ASSERTS = {
    0x10FB090: 0x5280A000,      # mov w0, #0x500
    0x10FB0A0: 0x52805A00,      # mov w0, #0x2D0
    0x10D528C: 0x1E2F1000,      # fmov s0, #1.5
    0x10DE81C: 0x52A8840B,      # mov w11, #0x44200000   (640.0f)
    0x10DE82C: 0x52A87E0A,      # mov w10, #0x43F00000   (480.0f)
}

# Bump whenever the OUTPUT of an unchanged input changes. build.py puts this
# in the movie cache key. The 768px texture fix silently did nothing because
# its key carried the setting but not the code version; this is that lesson
# applied here.
CONVERTER_VERSION = 'MOVIECONV-V2'

# How the picture is sized.
#   fit      resample to the size the console actually draws (Lanczos, on the
#            PC) and never upscale -- the default, and the fix for "the file
#            is huge but it looks soft"
#   native   whatever the mod ships, which is what every build before V2 did
FIT_CHOICES = [
    ('screen', 'Fit to the displayed area — every pixel is a screen pixel'),
    ('fit',    'Fit to the render target'),
    ('native', 'Keep the mod’s resolution'),
]
FIT_DEFAULT = 'screen'
FIT_ENV = 'SEVENTH_NX_MOVIE_FIT'

# Colour matrix handling.
#   bt709    convert SD/untagged sources to BT.709 limited, which is what
#            romfs/shaders/video_p.glsl hardcodes -- the default
#   off      pass whatever the source carried through untouched (pre-V2)
COLOUR_CHOICES = [
    ('bt709', 'Convert to BT.709 — matches the port’s shader'),
    ('off',   'Leave the source’s colour alone'),
]
COLOUR_DEFAULT = 'bt709'
COLOUR_ENV = 'SEVENTH_NX_MOVIE_COLOUR'

# What ffprobe reports for a matrix that is BT.601 or is not tagged at all.
# ffmpeg treats unspecified SD content as BT.601, and so does the decoder in
# the port, so both land in the same bucket.
BT601_MATRICES = frozenset(('', 'bt470bg', 'smpte170m', 'smpte240m',
                            'unknown', 'unspecified', 'reserved', 'fcc'))


# Quality levels, best first. The numbers in the labels are what the 16-second
# reference clip encoded to, so the size implication is visible at the point of
# choosing rather than discovered after a 40-minute build.
QUALITY_LEVELS = [
    ('maximum',  10, 'Maximum \u2014 highest the console can play'),
    ('high',     14, 'High \u2014 matches the game\'s own bitrate'),
    ('balanced', 18, 'Balanced \u2014 about half the size'),
    ('small',    23, 'Smaller files'),
]
QUALITY_CRF = {name: crf for name, crf, _label in QUALITY_LEVELS}
QUALITY_DEFAULT = 'high'
QUALITY_ENV = 'SEVENTH_NX_MOVIE_QUALITY'

# Target encode, taken from the port's own southmk.mp4 rather than invented.
TARGET_VCODEC = 'libx264'
TARGET_PROFILE = 'high'
TARGET_PIX_FMT = 'yuv420p'
TARGET_ACODEC = 'aac'
TARGET_ARATE = 44100
TARGET_ACHANNELS = 2
TARGET_ABITRATE = '128k'
# The port's file is level 3.2. Switch hardware decodes far above that, but a
# conversion that lands above this is a sign something is wrong with the
# source (an enormous resolution, a silly frame rate) and is worth saying out
# loud rather than shipping.
LEVEL_WARN_ABOVE = 41


class MissingFFmpeg(Exception):
    pass


def have_ffmpeg():
    return bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path):
    """
    Return what actually matters about a video file, or None if it is not one.

        {'vcodec', 'acodec', 'width', 'height', 'fps' (Fraction-ish float),
         'fps_exact' ('15/1'), 'nb_frames', 'duration', 'has_audio',
         'profile', 'level', 'pix_fmt'}
    """
    if not shutil.which('ffprobe'):
        raise MissingFFmpeg('ffprobe not found')
    p = _run(['ffprobe', '-v', 'error', '-print_format', 'json',
              '-show_format', '-show_streams', path])
    if p.returncode != 0:
        return None
    try:
        data = json.loads(p.stdout)
    except ValueError:
        return None
    v = next((s for s in data.get('streams', ())
              if s.get('codec_type') == 'video'), None)
    if v is None:
        return None
    a = next((s for s in data.get('streams', ())
              if s.get('codec_type') == 'audio'), None)
    rate = v.get('r_frame_rate') or '0/1'
    try:
        num, den = (int(x) for x in rate.split('/'))
        fps = num / den if den else 0.0
    except ValueError:
        fps = 0.0
    dur = data.get('format', {}).get('duration') or v.get('duration')
    try:
        dur = float(dur)
    except (TypeError, ValueError):
        dur = None
    nb = v.get('nb_frames')
    try:
        nb = int(nb)
    except (TypeError, ValueError):
        nb = None
    def _dur(stream):
        try:
            return float(stream.get('duration'))
        except (TypeError, ValueError):
            return None

    return {
        'vcodec': v.get('codec_name'),
        'acodec': a.get('codec_name') if a else None,
        'width': v.get('width'),
        'height': v.get('height'),
        'fps': fps,
        'fps_exact': rate,
        'nb_frames': nb,
        'duration': dur,
        'has_audio': a is not None,
        'profile': v.get('profile'),
        'level': v.get('level'),
        'pix_fmt': v.get('pix_fmt'),
        'container': data.get('format', {}).get('format_name', ''),
        'vdur': _dur(v),
        'adur': _dur(a) if a else None,
        # Colour tags. Absent is the normal case for a mod's files and is
        # NOT the same as "correct" -- see colour_plan().
        'color_space': (v.get('color_space') or '').lower(),
        'color_range': (v.get('color_range') or '').lower(),
        'color_primaries': (v.get('color_primaries') or '').lower(),
        'color_transfer': (v.get('color_transfer') or '').lower(),
    }


def already_target(info, fit=None, colour=None):
    """
    True if a file is already exactly what the port plays, so copying it is
    better than re-encoding it (re-encoding is always a quality loss).

    `fit` and `colour` are the settings in force. They are part of the
    question: a 2560x1792 H.264/mp4 is the right CONTAINER and the wrong
    PICTURE, and copying it as-is was how a mod's oversized files reached
    the card untouched and got bilinear-minified on the console.
    """
    if info and fit is not None:
        if encode_size(info['width'], info['height'], fit)[2] != 'native':
            return False
    if info and colour is not None and colour_plan(info, colour)[2]:
        return False
    return bool(info
                and info['vcodec'] == 'h264'
                and info['pix_fmt'] == TARGET_PIX_FMT
                and (info['profile'] or '').lower() in ('high', 'main',
                                                        'baseline',
                                                        'constrained baseline')
                and 'mp4' in (info['container'] or '')
                and (not info['has_audio'] or info['acodec'] == 'aac'))


# x264 preset. Fixed, and deliberately not a second dial: preset trades
# encoding TIME for SIZE, not picture quality. Measured on the reference clip,
# `slow` at crf 12 scored SSIM 0.99763 in 7.58s, while `veryfast` at crf 10
# scored higher (0.99768) in 1.90s. Quality is the crf's job.
TARGET_PRESET = 'veryfast'


# --------------------------------------------------------------------------
# The opening movie's frame cue
#
# FF7's field loop reads the current movie frame every tick and, on the
# OPENING FIELD ONLY, compares it against a hardcoded constant:
#
#   0063C296  call 0x418613            ; get_movie_frame()
#   0063C29B  mov  word [0xCC0E10], ax ; current_movie_frame = it
#   0063C2A3  mov  cx, [0xCFF468]      ; field id
#   0063C2AA  cmp  ecx, 0x74           ; the opening field
#   0063C2AD  jne  skip
#   0063C2AF  movsx edx, word [0xCC0E10]
#   0063C2B6  cmp  edx, 0x6E0          ; <-- 1760, immediate at 0x63C2B8
#   0063C2BC  jne  skip
#                                      ; ...hand over to the field
#
# `get_movie_frame` returns the DECODED FRAME INDEX -- on Switch it is a
# native stub, `mov w0,#0xB00B0005; b 0xA510`, and on PC FFNx's replacement
# returns `movie_frame_counter` verbatim. Either way a 30 fps opening reaches
# frame 1760 in half the wall-clock time it takes at 15, and the field takes
# over while the movie is still running: the train scene appears over the top
# of a cutscene that has another minute to go.
#
# 1760 frames at 15 fps is 117.3 seconds, which is right at the end of the
# vanilla opening -- so the constant is a "the movie is nearly done" cue, and
# scaling it with the frame rate restores it to the same MOMENT.
#
# This is the only such constant in the executable: current_movie_frame has
# exactly two references in .text, the write above and this one comparison.
# FFNx patches the same value for the same reason
# (`opening_movie_music_start_frame * ceil(movie_fps / 15.0f)`).
#
# The address is DERIVED, never hardcoded -- see opening_cue_address() -- so
# it holds for both the Steam exe and the Switch 1.03_5 exe.
# The rate every movie is built at when 30 FPS FMV support is on. The game's
# movie frame counter is divided to match (ff7nx_dispatch.build_movie_frame_
# cave), and that divider is unconditional -- so a movie left at 15 fps would
# have its counter halved and desync the other way. Everything goes to 30.
NORMALISED_FPS = 30
NORMALISED_RATIO = 2          # 30 / VANILLA_MOVIE_FPS

OPENING_FIELD_ID = 0x74
OPENING_CUE_VANILLA = 1760
VANILLA_MOVIE_FPS = 15.0
OPENING_CUE_DERIVATION = ('main_loop -> field_main_loop -> '
                          'field_loop_sub_63C17F + 0x139')


def display_footprint(width, height):
    """
    (w, h) of the box the PANEL actually shows, measured (see DISPLAY_W).

    The picture area is a fixed 960x672 and a movie is aspect-fitted into
    it, so this is the largest number of real screen pixels a movie of this
    shape can ever occupy. Encoding to exactly this means every pixel in the
    file is a pixel on the panel and the GPU resamples nothing.
    """
    if not width or not height:
        return DISPLAY_W, DISPLAY_H
    ar = width / float(height)
    box_ar = DISPLAY_W / float(DISPLAY_H)
    if ar >= box_ar:
        return DISPLAY_W, int(round(DISPLAY_W / ar))
    return int(round(DISPLAY_H * ar)), DISPLAY_H


def device_footprint(width, height):
    """
    (w, h) in DEVICE PIXELS that a `width` x `height` movie covers on the
    console, taking the larger of the two draw paths per axis.

    In-game (`fw_movie_update` -> +0x10DE7C0) builds the quad in FF7's
    640x480 space as 640 x min(640*h/w, 480) and `gfx_drv_setviewport`
    scales that onto the 1440x1080 render target by 2.25.

    Full screen (+0x10E0390) fits the quad to the 1280x720 back buffer at
    full height.

    A movie may be played through either path -- `opening` goes through the
    field, `ending2` through the port's own player -- so the file has to
    satisfy the bigger of the two.
    """
    if not width or not height:
        return TARGET_W, TARGET_H
    ar = width / float(height)
    ingame_w = TARGET_W
    ingame_h = min(TARGET_W / ar, float(TARGET_H))
    fullscreen_h = float(SCREEN_H)
    fullscreen_w = SCREEN_H * ar
    return (int(round(max(ingame_w, fullscreen_w))),
            int(round(max(ingame_h, fullscreen_h))))


def _even(n):
    """Nearest even integer >= 2. yuv420p cannot represent an odd axis."""
    n = int(round(n))
    if n % 2:
        n += 1
    return max(2, n)


def encode_size(width, height, fit=FIT_DEFAULT):
    """
    The size to encode at.

    NEVER UPSCALES. Adding pixels the source does not have costs bitrate and
    buys nothing -- the reconstruction that magnifies a small movie belongs
    in `hd_video/video_p.glsl`, on the GPU, where it can see the real output
    footprint. This only ever removes pixels the console is going to throw
    away anyway, and it removes them with Lanczos instead of with the
    shader's single bilinear fetch.

    Returns (w, h, reason) where reason is 'native', 'fit' or 'odd'.
    """
    if not width or not height:
        return width, height, 'native'
    ew, eh = _even(width), _even(height)
    if fit not in ('fit', 'screen'):
        return ew, eh, ('odd' if (ew, eh) != (width, height) else 'native')
    dw, dh = (display_footprint(width, height) if fit == 'screen'
              else device_footprint(width, height))
    if width <= dw and height <= dh:
        return ew, eh, ('odd' if (ew, eh) != (width, height) else 'native')
    # One scale factor for both axes: the aspect ratio is the one thing the
    # draw path derives everything else from, so it must not move.
    k = min(dw / float(width), dh / float(height))
    ow, oh = _even(width * k), _even(height * k)

    # ALIGNMENT GUARD -- this is why the aspect ratio is not merely a
    # cosmetic concern.
    #
    # Movies are not always fullscreen. FF7 plays them UNDER a live field:
    # 3D models walk about on top of a playing movie and the scene has to cut
    # between the two without the picture jumping. The quad the port builds
    # is
    #
    #     640 x min(640 * h / w, 480)          (+0x10DE7C0)
    #
    # anchored at y = 0. Its HEIGHT is derived from h/w, so any change to the
    # aspect ratio moves the movie's bottom edge relative to the field drawn
    # over it. Rounding each axis to even independently can do that by itself
    # on a source whose dimensions are not both divisible by the scale.
    #
    # So: measure the shift the resample would cause, in the game's own
    # units, and refuse the resample rather than introduce it. Half a game
    # unit is about one device pixel, which is the point at which a hard edge
    # in the movie stops landing on the same row as the field's.
    shift = abs(GAME_W * (oh / float(ow)) - GAME_W * (height / float(width)))
    if shift > 0.25:
        return _even(width), _even(height), 'native'
    return ow, oh, 'fit'


def colour_plan(info, colour=COLOUR_DEFAULT):
    """
    (in_matrix, in_range, convert) for swscale, or (None, None, False).

    `romfs/shaders/video_p.glsl` hardcodes BT.709 limited range. A source
    that is BT.601 or untagged decodes through the wrong matrix; a source
    that is full range decodes through the wrong range. Both are fixed here,
    once, rather than in a shader that also serves the port's own files.
    """
    if colour != 'bt709' or not info:
        return None, None, False
    space = info.get('color_space') or ''
    rng = info.get('color_range') or ''
    in_matrix = 'bt709' if space == 'bt709' else 'bt601'
    in_range = 'pc' if rng in ('pc', 'full') else 'tv'
    convert = (in_matrix != 'bt709') or (in_range != 'tv')
    return in_matrix, in_range, convert


def video_filter(info, fit=FIT_DEFAULT, colour=COLOUR_DEFAULT):
    """
    The -vf argument, and the (w, h, reason) it resolves to.

    There is always exactly one `scale`, so there is exactly one resample:
    chaining a size change and a colour change through two scale filters
    would resample twice for no reason.
    """
    w, h, reason = encode_size(info['width'], info['height'], fit)
    in_matrix, in_range, convert = colour_plan(info, colour)
    opts = ['%d' % w, '%d' % h]
    if reason == 'fit':
        # Lanczos, because this is a real minification. bilinear/bicubic at
        # 0.75x and below start losing the detail the whole exercise is
        # about, and the GPU is already the thing doing a bad job of it.
        opts.append('flags=lanczos')
    if convert:
        opts.append('in_color_matrix=%s' % in_matrix)
        opts.append('out_color_matrix=bt709')
        opts.append('in_range=%s' % in_range)
        opts.append('out_range=tv')
    return 'scale=' + ':'.join(opts), (w, h, reason)


def crf_for(quality):
    """crf for a quality name, falling back to the default rather than
    raising: an unrecognised value in settings.json should not stop a build."""
    return QUALITY_CRF.get(quality, QUALITY_CRF[QUALITY_DEFAULT])


def source_key(path, extra=''):
    """
    Cache identity for a source file: its CONTENT, not its timestamp.

    Extracting an .iro again rewrites every file with a new mtime, which under
    an mtime-based key would throw away a whole FMV pack's worth of encoding
    for files that are byte-for-byte what they were. Hashing costs about
    0.03s for a 7.5 MB movie against seconds to re-encode it.
    """
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    h.update(('|' + extra).encode())
    return h.hexdigest()[:20]


def convert(src, dest, vanilla=None, quality=QUALITY_DEFAULT,
            preset=TARGET_PRESET, target_fps=None, fit=FIT_DEFAULT,
            colour=COLOUR_DEFAULT, log=lambda *_: None):
    """
    Write `dest` (.mp4) from `src`, in the shape the Switch port plays.

    `vanilla` is the port's own file for this movie, when there is one. It is
    used as an audio donor when the mod's file is silent but the original was
    not -- an FMV pack may rebuild the picture only, and a replacement that
    arrived without its soundtrack would be a silent cutscene.

    Returns a dict describing what happened, or raises.
    """
    if not have_ffmpeg():
        raise MissingFFmpeg('ffmpeg and ffprobe are required to convert movies')

    info = probe(src)
    if info is None:
        raise ValueError('%s is not a video file ffprobe understands' % src)
    van = probe(vanilla) if vanilla and os.path.exists(vanilla) else None

    # `target_fps` forces the output rate. It is used by 30 FPS FMV support
    # to bring the WHOLE movie set to one rate: a mod's 30 fps files pass
    # through untouched, and a 15 fps file is frame-doubled -- duplicate
    # frames cost almost nothing in H.264 (the reference clip went 7.53 MB at
    # 15 fps to 6.64 MB at 30) and the running time is unchanged.
    fps = str(target_fps) if target_fps else info['fps_exact']
    crf = crf_for(quality)

    borrow_audio = (not info['has_audio']) and van is not None \
        and van['has_audio']

    vf, (want_w, want_h, fit_reason) = video_filter(info, fit, colour)
    colour_convert = colour_plan(info, colour)[2]

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + '.part.mp4'

    cmd = ['ffmpeg', '-y', '-nostdin', '-loglevel', 'error', '-i', src]
    if borrow_audio:
        cmd += ['-i', vanilla]
    cmd += ['-map', '0:v:0']
    if borrow_audio:
        cmd += ['-map', '1:a:0']
    elif info['has_audio']:
        cmd += ['-map', '0:a:0']
    else:
        cmd += ['-an']

    cmd += ['-c:v', TARGET_VCODEC,
            '-profile:v', TARGET_PROFILE,
            '-pix_fmt', TARGET_PIX_FMT,
            '-preset', preset,
            '-crf', str(crf),
            '-r', fps,
            # CFR, so the frame count is exactly duration * fps and the port's
            # frame numbering is predictable. A variable-frame-rate mp4 would
            # make `get_movie_frame` mean something different every run.
            '-vsync', 'cfr',
            # One scale filter, doing all of: even dimensions (yuv420p
            # cannot represent an odd axis), the fit to the console's drawn
            # size, and the colour matrix. See video_filter().
            '-vf', vf]
    if colour_convert:
        # Tag the result as what it now is. The port's shader does not read
        # these, but ffprobe does, and "check the built file" is how this
        # gets verified without a hardware test.
        cmd += ['-colorspace', 'bt709', '-color_primaries', 'bt709',
                '-color_trc', 'bt709', '-color_range', 'tv']
    if info['has_audio'] or borrow_audio:
        cmd += ['-c:a', TARGET_ACODEC, '-b:a', TARGET_ABITRATE,
                '-ar', str(TARGET_ARATE), '-ac', str(TARGET_ACHANNELS),
                # Rebuild the audio timeline from the sample count.
                #
                # Not cosmetic. A mod's audio arrives with whatever timebase
                # its encoder chose -- a 30 fps VP8/Vorbis source carried its
                # audio on a 32/11025 timebase -- and re-encoding straight
                # from that produced an mp4 whose AAC track claimed 77
                # SECONDS of presentation time for 16 seconds of samples.
                # The picture was perfect and the file looked normal in a
                # directory listing; the sound would have come apart from it
                # within a second.
                #
                # `asetpts=N/SR/TB` discards the incoming timestamps and
                # derives each one from the output sample index instead,
                # which is exactly right here because the audio is never
                # sped up or slowed down -- even the retime option changes
                # only which video frames are kept, not the running time.
                #
                # `aresample=async=1` is the usual reach for this and is
                # deliberately NOT used: on the file above it left the
                # broken timeline in place (77.18s), and putting it in front
                # of asetpts reintroduced the fault, because asetpts then
                # works in the timebase aresample chose.
                '-af', 'asetpts=N/SR/TB',
                # Do not let a shorter audio track extend the video, or a
                # longer one pad it: the picture decides the length.
                '-shortest']
    cmd += ['-map_metadata', '-1', '-movflags', '+faststart', tmp]

    p = _run(cmd)
    if p.returncode != 0 or not os.path.exists(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError('ffmpeg failed on %s:\n%s'
                           % (os.path.basename(src), p.stderr.strip()[-800:]))

    out = probe(tmp)
    if out is None or out['vcodec'] != 'h264':
        os.remove(tmp)
        raise RuntimeError('conversion of %s did not produce H.264'
                           % os.path.basename(src))
    # The picture is authoritative; the sound has to agree with it. A track
    # whose timestamps say something wildly different from the video is the
    # failure described above, and it must not reach an SD card silently.
    if out['adur'] and out['vdur'] and abs(out['adur'] - out['vdur']) > 1.0:
        os.remove(tmp)
        raise RuntimeError(
            'converted %s has %0.2fs of video but an audio track timestamped '
            'at %0.2fs -- the source audio timeline could not be normalised'
            % (os.path.basename(src), out['vdur'], out['adur']))
    # A log line saying a size was requested is not evidence the encoder
    # produced it. Check the artifact.
    if (out['width'], out['height']) != (want_w, want_h):
        os.remove(tmp)
        raise RuntimeError(
            'converted %s came out %dx%d but %dx%d was requested -- the '
            'scale filter did not do what the command said'
            % (os.path.basename(src), out['width'], out['height'],
               want_w, want_h))
    if out['level'] and out['level'] > LEVEL_WARN_ABOVE:
        log('    ! %s encoded at H.264 level %.1f (the game\'s own files are '
            '3.2) -- check it plays' % (os.path.basename(dest),
                                        out['level'] / 10.0))
    os.replace(tmp, dest)

    return {
        'src': info, 'out': out, 'vanilla': van,
        'fps': fps, 'quality': quality, 'crf': crf,
        'doubled': bool(target_fps) and info['fps'] < target_fps - 0.01,
        'borrowed_audio': borrow_audio,
        'fit': fit, 'fit_reason': fit_reason,
        'colour': colour, 'colour_converted': colour_convert,
        'drawn': device_footprint(info['width'], info['height']),
    }


def describe_drawn(info):
    """One line for the build log: what the console will actually draw."""
    if not info or not info['width']:
        return 'unknown'
    dw, dh = device_footprint(info['width'], info['height'])
    ratio = info['width'] / float(dw)
    if ratio > 1.02:
        verdict = 'minified %.2fx by the GPU unless it is resampled' % ratio
    elif ratio < 0.98:
        verdict = 'magnified %.2fx (hd_video shader reconstructs it)' \
            % (1.0 / ratio)
    else:
        verdict = '1:1'
    return '%dx%d drawn, %s' % (dw, dh, verdict)


def describe_source(info):
    """One line for the build log."""
    if not info:
        return 'unreadable'
    a = ('%s %dch' % (info['acodec'], 0)) if False else (info['acodec'] or 'no audio')
    return '%s/%s %dx%d @%.6g fps, %s' % (
        re.sub(r',.*', '', info['container'] or '?'), info['vcodec'],
        info['width'] or 0, info['height'] or 0, info['fps'], a)

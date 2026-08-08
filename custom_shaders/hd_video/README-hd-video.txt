FF7 Switch — "hd_video" movie shader
====================================

Install: copy hd_video/video_p.glsl over
  /atmosphere/contents/0100A5B00BDC6000/romfs/shaders/video_p.glsl
Uninstall: restore the stock file. Game data untouched.

Independent of the hd background shaders — install either, both, or neither.


WHICH SHADER DOES WHAT (the full set is 17 files, one per fixed render pass)
---------------------------------------------------------------------------
  video_p.glsl          MOVIES. YCbCr -> RGB. <- this file
  2xsal_p / hq4x_p      field background colour      (hd/ replaces these)
  2xsal_depth_p         field background DEPTH       (leave nearest-neighbour)
  fxaanv5_p             full-frame FXAA              (hd_fxaa/ tunes this)
  colortex_p            character models, battle, most textured geometry
  text_p                UI text and font glyphs
  color_p               untextured coloured geometry — no sampling, nothing to tune
  depthtex_p            depth blit
  mergeforeground_p     composites field foreground over models using two depths
  *_vv.glsl (6)         vertex shaders — transform only

Only video_p.glsl touches movies. Nothing you do to the hd background shaders
affects FMV playback, which is why movies stayed soft while backgrounds got
sharper.


WHY MOVIES LOOK LOW RES
-----------------------
The stock shader does exactly one plain texture() fetch per plane. There is no
reconstruction filter anywhere in the movie path — a movie smaller than the
area it is drawn into gets stock bilinear magnification, which is the softest
possible result. This is the same problem the hd background shader was written
to solve, and it was never applied here.

The port decodes with movie::DecoderMode_NativeTexture, giving:

    Sampler0 = Y     luma, full resolution
    Sampler1 = CbCr  chroma, interleaved, HALF resolution (4:2:0)

This file runs Catmull-Rom reconstruction on LUMA ONLY. Chroma is half
resolution and heavily quantised; reconstructing it buys no visible detail and
causes colour fringing on hard edges. Luma carries essentially all the apparent
sharpness.

VERIFIED NUMERICALLY:

  weights sum to 1 for all fractional positions       max err 4.4e-16
  9-tap bilinear folding == true 16-tap Catmull-Rom   max err 8.9e-16
  BT.709 branch == stock coefficients                 exact, all 9

(The first attempt at that middle check reported 6.1e-01. That was a bug in the
test harness, not the shader — it indexed the array directly instead of
converting texel-centre units to index space. Worth recording, because the
number looked like a real failure.)


IT TURNS ITSELF OFF WHEN THE MOVIE IS ALREADY BIG ENOUGH
--------------------------------------------------------
The stock movies are 1280x896. On a 720p panel those are being DOWNSCALED, and
sharpening a downscale adds aliasing and amplifies H.264 mosquito noise — it
would make stock movies worse.

Since a replacement pack can be any size, this cannot be a fixed setting.
fwidth() gives the UV footprint of one output pixel; times the texture size
that is texels-per-pixel:

    texels/pixel   effect
        <= 1.0      1.00     magnifying — full reconstruction
         1.25       0.75
         1.50       0.50
        >= 2.0      0.00     minifying  — stock behaviour, bit for bit

So it is safe to install whatever your movies are, and it does nothing at all
to stock ones. If ALL your movies are higher resolution than their screen area,
this file will correctly do nothing — and that also tells you the softness is
coming from somewhere else (see DIAGNOSING below).


TUNING
------
  const float HD_VIDEO_SHARPEN = 0.5;

    Deliberately lower than the background shader's 1.0. Backgrounds are
    stills quantised to R5G6B5; movies are lossy H.264. Above about 0.6 this
    starts sharpening block edges and mosquito noise instead of picture
    detail. 0.0 gives pure Catmull-Rom, still a large gain over bilinear.

  const float HD_VIDEO_ANTIRING = 1.0;

    Leave at 1.0. Ringing on a moving image reads as edge crawl, which is more
    objectionable than on a still.


THE COLOUR MATRIX — A SEPARATE BUG, POSSIBLY
--------------------------------------------
The stock coefficients (1.1644 / 1.7927 / -0.2133 / -0.5329 / 2.1124) are
BT.709 limited range, hardcoded. Correct for SQEX's own movies.

FF7's original FMVs are 320x224 — standard definition, i.e. BT.601. An upscaled
FMV pack derived from them is very likely still BT.601, or tagged unspecified,
which ffmpeg treats as BT.601. Decoding BT.601 through a BT.709 matrix:

    colour     true RGB          shown as        max channel error
    red        255   0   0       255  25   0        24.7 /255
    green        0 255   0         0 216   0        39.4 /255
    magenta    255   0 255       255  40 255        39.6 /255
    cyan         0 255 255         0 230 255        24.5 /255
    skin       204 153 102       209 155  99         5.2 /255
    grey       128 128 128       128 128 128         0.1 /255

Greys are exact and skin barely moves, so this does not look like a colour
bug — it looks like mildly weak saturation, easy to blame on the upscale.

  #define HD_VIDEO_MATRIX 0     0 = BT.709 (stock), 1 = BT.601

DO NOT LEAVE THIS AT 1 CASUALLY. One shader serves every movie, so switching it
fixes a BT.601 pack and breaks every stock movie you did not replace. Use it to
A/B whether colour is actually wrong, then fix it properly at build time:

    ffprobe -v error -select_streams v:0 \
            -show_entries stream=color_space,color_range,width,height \
            -of default=nk=1:nw=1  <built .mp4>

If that reports bt470bg / smpte170m / unknown, the pack is BT.601 and the
encode should convert rather than the shader:

    -vf scale=in_color_matrix=bt601:out_color_matrix=bt709 \
    -colorspace bt709 -color_primaries bt709 -color_trc bt709

movies.py currently passes no colour flags at all, so whatever the source
carried is what ends up in the mp4.


DIAGNOSING "STILL LOW RES"
--------------------------
Resolution is not lost in the packer. movies.py's only scale filter is
`scale=trunc(iw/2)*2:trunc(ih/2)*2`, which forces even dimensions for yuv420p
and nothing else. Check in this order:

  1. The build log already prints source dimensions for every movie
     (describe_source: "container/codec WxH @fps"). Read them.
  2. ffprobe the BUILT mp4 in sdout, per the command above.
  3. Compare against 1280x896, which is what SQEX ships.

A pack that is 640x448 (2x the original 320x224) rather than 1280x896 (4x) is
being magnified on a 720p panel and will look soft no matter what — that is the
case this shader helps most. A pack already at or above 1280x896 will not
benefit, and the softness is then in the encode quality (crf) rather than the
resolution.

For reference, the game's internal coordinate space is 640x480 (set at x86
0x406A7A, `mov [game_obj+0x954], 0x280` / `[+0x958], 0x1E0`). SQEX shipping
1280x896 movies means the movie path is not clamped to that coordinate space.


IF SOMETHING GOES WRONG
-----------------------
A shader that fails to compile usually shows as a black or missing layer rather
than a crash. Restore the stock video_p.glsl and it is back. Nothing here
touches game data, saves, or the module.

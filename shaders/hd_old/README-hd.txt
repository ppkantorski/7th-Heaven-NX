FF7 Switch — "hd" background shader (smooth AND crisp)
======================================================

Install: copy hd/*.glsl over
  /atmosphere/contents/0100A5B00BDC6000/romfs/shaders/
Uninstall: restore the stock files. Game data untouched.

Same three files as your other modes, so it drops straight into the set you
already have (xbr / xbr2 / crisp / soft). Pick ONE.


WHY XBR AND CRISP CANNOT GIVE YOU WHAT YOU WANT
-----------------------------------------------
They are PIXEL-ART algorithms. Both look for hard steps between flat colour
regions and rebuild those steps as clean diagonals.

Your backgrounds are no longer pixel art. With Cosmos Limit Break packed in,
each field page is 512x512 of AI-upscaled continuous-tone art -- there are no
flat regions and no hard steps. So the edge detector either

  * finds nothing, and falls back to a blend  -> looks soft, or
  * fires on gradient noise                   -> the waxy, plastic, melted
                                                 look on skin, sky and rust.

And "crisp" is literally nearest-neighbour: it snaps to the source texel and
duplicates it, which is exactly the blockiness you are seeing. It was the right
answer when the source was 256px pixel art. It is the wrong answer now.

Continuous-tone art does not want an edge-directed scaler. It wants a good
RECONSTRUCTION filter.


WHAT THIS DOES INSTEAD
----------------------
Catmull-Rom bicubic, 9 hardware-bilinear taps, plus a measured amount of extra
acutance and a halo clamp.

Catmull-Rom is INTERPOLATING -- it passes exactly through every source texel,
so no original detail is smeared. Its negative lobes restore the high
frequencies that bilinear throws away. That is the smooth-and-crisp combination
neither of your existing modes could reach: bilinear is smooth but dull, xBR is
crisp but synthetic, this is smooth because it is a real reconstruction and
crisp because it is not band-limited.

VERIFIED NUMERICALLY, not by eye:

  weights sum to 1 for all fractional positions          exact
  9-tap bilinear folding == true 16-tap Catmull-Rom      max err 1.4e-15
  passes exactly through every source texel              err 0.0
  edge slope vs bilinear                                 1.12x steeper
  overshoot on a hard edge                               7.4%  -> clamped

That last line is why the clamp exists rather than being decoration: raw
Catmull-Rom rings 7.4% past the source range at a high-contrast edge, which
reads as a bright halo along railings and doorframes. The result is clamped
into the range of the four nearest texels, which removes the halo without
softening the edge itself.


TUNING
------
Top of 2xsal_p.glsl and hq4x_p.glsl (edit both to keep them consistent):

  const float HD_SHARPEN = 1.0;

MEASURED, not chosen by eye. Seven real 256x256 crops from a built flevel were
downsampled 2x and reconstructed, then compared against the original -- so
"better" means CLOSER TO THE TRUE ART, not merely sharper. With the anti-ring
clamp applied:

    HD_SHARPEN     RMSE    detail   pixels hitting the clamp
      0.35        6.255     73.0%        24.4%    <- the old default
      0.70        6.030     75.1%        28.1%
      1.00        5.876     76.5%        31.3%    <- now
      1.50        5.688     78.2%        36.6%
      3.00        5.489     81.2%        49.1%    RMSE minimum
      6.00        5.793     83.4%        64.8%    past it, worse again

The important result: the sharpening is NOT inventing detail. Error falls
monotonically all the way to about 3.0, which means it is recovering high
frequencies the reconstruction alone leaves behind. The old 0.35 was leaving
real accuracy on the table.

The reason to stop at 1.0 rather than 3.0 is the last column. A clamped pixel
is one the sharpen pushed outside its 2x2 source range, so it had to be pulled
back to the edge of it. At 3.0 half of all pixels are pinned to a
neighbourhood extreme -- that is posterisation, exactly the "pixelated" look
this filter exists to avoid. RMSE cannot see it; it reads a hard edge as
accurate. 1.0 takes most of the accuracy for a clamp rate barely above where
you already were.

Try 1.5 if you want more. Past 3.0 it degrades on the measurements, not just
to taste.

Two things that did NOT help, so they are not in here:

  * a bigger kernel. Lanczos-3 scored RMSE 7.326 / 80.4% detail at 25-36 taps,
    against 5.876 / 76.5% for sharpened Catmull-Rom at 13. Sharpening a good
    short kernel beats a longer one, at half the cost.
  * sharpening luminance only. Slightly WORSE (6.067 vs 6.030 at 0.70), so it
    stays RGB.

  const float HD_ANTIRING = 1.0;

    1.0 = clamp halos fully. This matters more now than at 0.35 -- it is what
    keeps a strong sharpen from turning into ringing. Leave it at 1.0.

THE "FADED" LOOK -- BACKGROUND GRADING
--------------------------------------
If the backgrounds look slightly washed out next to the characters, that is
most likely something the packer fix did, and it is fixable here without
touching anything else.

The packer used to quantise by TRUNCATION (>> 3), which always rounds down. On
true 24-bit input that biased every background pixel -3.49/255, and up to
-7/255 in the shadows. It is fixed -- the quantiser rounds now, measured bias
0.00 -- but the accidental side effect of the bug was CRUSHED BLACKS, and
crushed blacks read as contrast. Correct shadows look faded beside them.

So the data is right and the look changed. Rather than un-fix the packer and
bring the banding back, the punch is restored in the shader:

  const float HD_BLACK_POINT = 0.0;

    Pulls the bottom of the range back down.
      0.0    neutral, photometrically correct (default)
      0.014  undoes EXACTLY the +3.5/255 lift the fix introduced -- this is
             the setting that reproduces the old contrast
      0.02-0.03  beyond it, for more punch than the game ever had

    Verified: 0.014 maps 3.5/255 to 0 and leaves white untouched at 255/255,
    so it deepens shadows without dulling highlights.

  const float HD_SATURATION = 1.0;

    1.0 = untouched. Try 1.05-1.15 if the art looks washed rather than merely
    dark. AI upscales often desaturate slightly, and that would be the mod's
    art rather than anything here -- which is why this is separate from the
    black point. Change one at a time.

BOTH ARE BACKGROUND ONLY. Character models, battle and the UI are drawn by
different shaders (colortex_p.glsl and friends), so nothing you already
consider correct can be disturbed by these. That is also why grading here is
the right place for it rather than a global colour tweak.

The depth shader is deliberately NOT graded -- see below.


DEPTH IS DELIBERATELY LEFT ALONE
--------------------------------
2xsal_depth_p.glsl stays nearest-neighbour, and that is not laziness.

That buffer decides which pixels of the field art draw in FRONT of the
character models. Any interpolation invents depth values that exist nowhere in
the source, and Catmull-Rom's overshoot would invent ones outside the source
range entirely. Along a railing or a doorway that is a halo of wrong occlusion
-- Cloud clipping through a handrail, or vanishing behind something thin.

The one-pixel mismatch between exact depth and smoothly filtered colour is
invisible. Wrong occlusion is not.


STACKING WITH fxaa_off
----------------------
Worth trying, and the trade is real in both directions now.

Full-frame FXAA runs over the finished image, so it softens your freshly
sharpened background for no benefit -- FXAA exists to hide jaggies on hard
geometric edges, and a Catmull-Rom background has none. Turning it off makes
the background measurably sharper and costs GPU time back.

But it also stops anti-aliasing the 3D MODEL silhouettes, which do have hard
edges and will get jaggier. With Ninostyle Chibi models at 720p that is
noticeable.

Try hd alone first. Add fxaa_off only if you decide you prefer sharp
backgrounds over smooth character outlines.


IF SOMETHING GOES WRONG
-----------------------
A shader that fails to compile usually shows as a black or missing background
layer rather than a crash. Restore the stock three files and it is back --
nothing here touches game data, saves, or the module.

FF7 Switch — "hd2" background shader
====================================
Same three files as every other mode. Copy over:
  /atmosphere/contents/0100A5B00BDC6000/romfs/ff7/shaders/

  2xsal_p.glsl       2xsal_depth_p.glsl       hq4x_p.glsl

WHAT CHANGED FROM hd, AND WHY
-----------------------------
hd is Catmull-Rom + an unsharp at full strength + a HARD clamp into the range
of the four nearest texels. Both the unsharp and the clamp vary with WHERE in
a texel a pixel falls. At a non-integer scale that averages into noise. This
build runs the field buffer at exactly 3.0000 pixels per texel, so the same
sub-texel phases are hit in every texel and both effects paint a fixed
3-pixel lattice over the entire screen -- the "grainy screen-door" look.

hd2 keeps Catmull-Rom, drops the sharpen to 0.35, and replaces the hard clamp
with a tanh soft knee that only bends on extreme overshoot. Nothing is ever
pinned, so there is no lattice.

MEASURED on six real truecolor pages upscaled 3x (lattice = how much of the
output depends on sub-texel phase; lower is better. acutance = mean |d/dx|):

    bilinear (soft/)                   lattice 0.0286   acutance 2.72
    CR + sharpen 1.00, hard clamp (hd) lattice 0.0369   acutance 3.12
    CR + sharpen 0.35, soft knee (hd2) lattice 0.0288   acutance 3.00

96% of hd's acutance, with the lattice at the bilinear floor.

TUNING
------
Both constants are at the top of 2xsal_p.glsl and hq4x_p.glsl:

  HD_SHARPEN    0.35   raise toward 0.70 for more bite; each step adds lattice
  HD_OVERSHOOT  1.00   how far past the 2x2 range the knee allows before it
                       bends. Lower = more halo control, slightly softer.
  HD_ANTIRING   1.00   0.0 disables the knee entirely (pure Catmull-Rom --
                       lattice 0.0285, acutance 3.02, but no halo control)

DO NOT USE xbr / xbr2 / crisp / hq4x-stock WITH COSMOS LIMIT BREAK. They are
pixel-art algorithms looking for flat regions and hard steps. This art has
neither, so they either do nothing or fire on gradient noise.

ALSO WORTH TRYING, SEPARATELY
-----------------------------
fxaa_off/ — FXAA on a 3x-magnified image can add its own structure. Test it
on its own after hd2, so you know which one moved what.

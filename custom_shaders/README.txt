FF7 Switch — background de-blur / upscale shader patches (v3)
=============================================================
Replaces the port's background scaler (2xSaL / HQ4X), which blurs the
pre-rendered maps. Pick ONE of the 4 scaler variants (same 3 files each):

  xbr2/   BEST QUALITY — level-2 edge-directed upscale: crisp flat
          areas, smooth 45-degree edges AND smoothed shallow (2:1)
          staircases + anti-aliased curves. Try this first.
  xbr/    level-1: crisp flat areas, smooth 45-degree edges only.
          Fallback if xbr2 shows edge artifacts on some scene.
  crisp/  pure nearest-neighbour, raw sharp pixels.
  soft/   plain bilinear, mildest change.

Optional, stacks with any scaler:
  fxaa_off/  disables full-frame FXAA (sharper overall, lower GPU load,
             jaggier 3D model edges).

Install: copy chosen .glsl files to
  /atmosphere/contents/0100A5B00BDC6000/romfs/shaders/
Uninstall: delete them. Game data untouched. Works with the 1.0.3 update.

Regenerate anytime: python3 patch_shaders.py <shaders> -o out --mode xbr2 [--fxaa-off]

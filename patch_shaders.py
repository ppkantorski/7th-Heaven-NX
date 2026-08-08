"""
De-blur the FF7 Switch port's background/upscale filters.

The port ships its scalers as GLSL source in romfs/shaders/. The 2xSaL
filter (and HQ4X where used) blends 4+ neighbouring texels, which is what
makes pre-rendered backgrounds mushy next to sharp 3D models. This script
rewrites the three filter pixel shaders to output an unfiltered sample:

  --mode crisp   true nearest-neighbour (snapped to texel centers):
                 maximum sharpness, visible pixels (authentic retro look)
  --mode soft    plain bilinear at the center coordinate: removes the
                 2xSaL smearing but keeps gentle smoothing
  --mode xbr     edge-directed upscale (xBR-family): flat areas stay
                 pixel-crisp, diagonal edges are reconstructed smoothly
                 with anti-aliased coverage -- the emulator-style "clean
                 upscale" look; the highest-quality option

Everything else (FXAA, compositing, text, video shaders) is untouched.
Vertex shaders are untouched; the center coordinate is reconstructed in
the pixel shader (for 2xSaL, center = midpoint of the two diagonal taps).
Uniform blocks and sampler declarations are preserved verbatim so the
engine's binding logic sees the same interface.

Usage:
    python3 patch_shaders.py <vanilla_shaders_dir> -o <out_dir> [--mode crisp]

Install: copy the produced files to
    /atmosphere/contents/0100A5B00BDC6000/romfs/shaders/
(LayeredFS overlays the merged base+update romfs, so this works with the
1.0.3 update installed, same as the workingdir LGP files.)
"""
import argparse
import os
import re
import shutil
import sys

MARKER = '// patched by 7th_heaven_nx patch_shaders.py'


def _snap(expr):
    return ('(floor((%s) * vec2(textureSize(Sampler0, 0))) + 0.5) '
            '/ vec2(textureSize(Sampler0, 0))' % expr)


def _xbr_body(center_expr):
    """
    Edge-directed upscale, xBR-family logic in a single pass:
    - every sample lands on a texel center, so flat areas render as
      crisp pixels (identical to nearest);
    - for the quadrant of the texel the fragment falls in, look at the
      horizontal (H), vertical (V) and diagonal (A) neighbours. When H
      and V agree with each other but disagree with the center texel and
      the diagonal continues the shape, the fragment sits on a diagonal
      edge: blend toward (H+V)/2 with a coverage ramp so 45-degree
      staircases become smooth, anti-aliased slopes.
    Thresholds: 0.12 = max H/V mismatch to count as one edge color,
    0.25 = min contrast against the center to treat it as an edge.
    """
    return f'''	vec2 ts = vec2(textureSize(Sampler0, 0));
	vec2 uv = {center_expr};
	vec2 ip = uv * ts;
	vec2 tc = floor(ip) + 0.5;
	vec2 f  = ip - tc;
	vec2 px = 1.0 / ts;
	vec2 dxy = vec2(f.x < 0.0 ? -1.0 : 1.0, f.y < 0.0 ? -1.0 : 1.0);
	vec2 base = tc / ts;
	vec4 E = texture(Sampler0, base);
	vec4 H = texture(Sampler0, base + vec2(dxy.x, 0.0) * px);
	vec4 V = texture(Sampler0, base + vec2(0.0, dxy.y) * px);
	vec4 A = texture(Sampler0, base + dxy * px);
	vec3 lw = vec3(0.299, 0.587, 0.114);
	float dHV = dot(abs(H.rgb - V.rgb), lw);
	float dEH = dot(abs(E.rgb - H.rgb), lw);
	float dEV = dot(abs(E.rgb - V.rgb), lw);
	float dEA = dot(abs(E.rgb - A.rgb), lw);
	float edge = step(dHV, 0.12) * step(0.25, min(dEH, dEV))
	           * step(dHV, dEA + 0.05);
	float cov = clamp((abs(f.x) + abs(f.y) - 0.5) * 2.0, 0.0, 1.0);
	vec4 result = mix(E, 0.5 * (H + V), edge * cov);'''


def _xbr2_body(center_expr):
    """
    Level-2 edge-directed upscale: adds shallow-slope (2:1 / 1:2)
    reconstruction on top of the 45-degree rule. Shallow edges are
    detected by requiring the neighbour row/column color to continue for
    two texels in both directions (strict, to avoid false positives) and
    are blended along the true edge line with an anti-aliasing ramp.
    """
    return f'''	vec2 ts = vec2(textureSize(Sampler0, 0));
	vec2 uv = {center_expr};
	vec2 ip = uv * ts;
	vec2 tc = floor(ip) + 0.5;
	vec2 f  = ip - tc;
	vec2 px = 1.0 / ts;
	vec2 dxy = vec2(f.x < 0.0 ? -1.0 : 1.0, f.y < 0.0 ? -1.0 : 1.0);
	vec2 base = tc / ts;
	vec4 E  = texture(Sampler0, base);
	vec4 Hn = texture(Sampler0, base + vec2(dxy.x, 0.0) * px);
	vec4 Vn = texture(Sampler0, base + vec2(0.0, dxy.y) * px);
	vec4 A  = texture(Sampler0, base + dxy * px);
	vec4 HA = texture(Sampler0, base + vec2(2.0 * dxy.x, dxy.y) * px);
	vec4 VA = texture(Sampler0, base + vec2(dxy.x, 2.0 * dxy.y) * px);
	vec4 H2 = texture(Sampler0, base + vec2(2.0 * dxy.x, 0.0) * px);
	vec4 V2 = texture(Sampler0, base + vec2(0.0, 2.0 * dxy.y) * px);
	vec3 lw = vec3(0.299, 0.587, 0.114);
	float dHV = dot(abs(Hn.rgb - Vn.rgb), lw);
	float dEH = dot(abs(E.rgb - Hn.rgb), lw);
	float dEV = dot(abs(E.rgb - Vn.rgb), lw);
	float dEA = dot(abs(E.rgb - A.rgb), lw);
	float ax = abs(f.x);
	float ay = abs(f.y);
	float e45 = step(dHV, 0.12) * step(0.25, min(dEH, dEV))
	          * step(dHV, dEA + 0.05);
	float c45 = clamp((ax + ay - 0.5) * 2.0, 0.0, 1.0);
	float eh = step(dot(abs(Vn.rgb - A.rgb), lw), 0.10)
	         * step(dot(abs(A.rgb - HA.rgb), lw), 0.10)
	         * step(dEH, 0.10)
	         * step(dot(abs(Hn.rgb - H2.rgb), lw), 0.10)
	         * step(0.25, dEV);
	float ch = clamp((ay + 0.5 * ax - 0.5) * 3.0 + 0.35, 0.0, 1.0);
	float ev = step(dot(abs(Hn.rgb - A.rgb), lw), 0.10)
	         * step(dot(abs(A.rgb - VA.rgb), lw), 0.10)
	         * step(dEV, 0.10)
	         * step(dot(abs(Vn.rgb - V2.rgb), lw), 0.10)
	         * step(0.25, dEH);
	float cv = clamp((ax + 0.5 * ay - 0.5) * 3.0 + 0.35, 0.0, 1.0);
	float t45 = e45 * c45;
	float th = eh * ch;
	float tv = ev * cv;
	vec4 result;
	if (th >= t45 && th >= tv) result = mix(E, Vn, th);
	else if (tv >= t45 && tv >= th) result = mix(E, Hn, tv);
	else result = mix(E, 0.5 * (Hn + Vn), t45);'''


def patch_2xsal_p(src, mode):
    center = '(vTextureCoord0 + vTextureCoord3) * 0.5'
    if mode == 'xbr2':
        body = f'''void main()
{{
	{MARKER}
	float keep = pParam1.w * 0.0;
{_xbr2_body(center)}
	pColor = result + vec4(keep);
}}'''
        return _replace_main(src, body)
    if mode == 'xbr':
        body = f'''void main()
{{
	{MARKER}
	float keep = pParam1.w * 0.0;
{_xbr_body(center)}
	pColor = result + vec4(keep);
}}'''
    else:
        uv = _snap(center) if mode == 'crisp' else center
        body = f'''void main()
{{
	{MARKER}
	float keep = pParam1.w * 0.0;
	vec2 uv = {uv};
	vec4 s = texture(Sampler0, uv);
	pColor = s + vec4(keep);
}}'''
    return _replace_main(src, body)


def patch_2xsal_depth_p(src, mode):
    # depth must not be edge-blended; crisp center sample for all modes
    center = '(vTextureCoord0 + vTextureCoord3) * 0.5'
    uv = center if mode == 'soft' else _snap(center)
    body = f'''void main()
{{
	{MARKER}
	float keep = pParam.w * 0.0;
	vec2 uv = {uv};
	gl_FragDepth = texture(Sampler0, uv).r + keep;
}}'''
    return _replace_main(src, body)


def patch_hq4x_p(src, mode):
    center = 'vTextureCoord0.xy'
    if mode == 'xbr2':
        body = f'''void main()
{{
	{MARKER}
	float keep = pParam1.w * 0.0 + pParam2.x * 0.0;
{_xbr2_body(center)}
	pColor = result + vec4(keep);
	if (pParam1.w > 0.0)
	{{
		gl_FragDepth = pColor.r;
	}}
}}'''
        return _replace_main(src, body)
    if mode == 'xbr':
        body = f'''void main()
{{
	{MARKER}
	float keep = pParam1.w * 0.0 + pParam2.x * 0.0;
{_xbr_body(center)}
	pColor = result + vec4(keep);
	if (pParam1.w > 0.0)
	{{
		gl_FragDepth = pColor.r;
	}}
}}'''
    else:
        uv = _snap(center) if mode == 'crisp' else center
        # preserve the original's conditional depth write behaviour
        body = f'''void main()
{{
	{MARKER}
	float keep = pParam2.x * 0.0;
	vec2 uv = {uv};
	pColor = texture(Sampler0, uv) + vec4(keep);
	if (pParam1.w > 0.0)
	{{
		gl_FragDepth = pColor.r;
	}}
}}'''
    return _replace_main(src, body)


def _replace_main(src, new_main):
    i = src.index('void main()')
    return src[:i] + new_main + '\n'


PATCHES = {
    '2xsal_p.glsl': patch_2xsal_p,
    '2xsal_depth_p.glsl': patch_2xsal_depth_p,
    'hq4x_p.glsl': patch_hq4x_p,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('shaders_dir')
    ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--mode', choices=('crisp', 'soft', 'xbr', 'xbr2'),
                    default='xbr2')
    ap.add_argument('--fxaa-off', action='store_true',
                    help='also write a passthrough fxaanv5_p.glsl that '
                         'disables the full-frame FXAA pass (sharper '
                         'overall, lower GPU load, jaggier model edges)')
    ap.add_argument('--all', action='store_true',
                    help='also copy the untouched shaders to the output')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    done = []
    for fn in sorted(os.listdir(args.shaders_dir)):
        srcp = os.path.join(args.shaders_dir, fn)
        if not fn.endswith('.glsl') or not os.path.isfile(srcp):
            continue
        with open(srcp, 'r', encoding='utf-8', errors='replace') as f:
            src = f.read()
        if MARKER in src:
            print(f'{fn}: already patched input, skipping')
            continue
        fixer = PATCHES.get(fn)
        if fixer:
            if 'void main()' not in src:
                print(f'{fn}: unexpected content (no main), NOT patched')
                continue
            out = fixer(src, args.mode)
            with open(os.path.join(args.out, fn), 'w') as f:
                f.write(out)
            done.append(fn)
            print(f'{fn}: patched ({args.mode})')
        elif args.all:
            shutil.copy(srcp, os.path.join(args.out, fn))
    if args.fxaa_off:
        srcp = os.path.join(args.shaders_dir, 'fxaanv5_p.glsl')
        if os.path.exists(srcp):
            with open(srcp, 'r', encoding='utf-8', errors='replace') as f:
                src = f.read()
            i = src.index('/*')  # keep header/declarations above first block
            out = src[:i] + f'''{MARKER}
void main()
{{
	vec2 keep = rcpFrame * 0.0;
	pColor = vec4(texture(Sampler0, vTextureCoord + keep).rgb, 1.0);
}}
'''
            with open(os.path.join(args.out, 'fxaanv5_p.glsl'), 'w') as f:
                f.write(out)
            done.append('fxaanv5_p.glsl')
            print('fxaanv5_p.glsl: FXAA disabled (passthrough)')

    if not done:
        print('nothing patched — is this the right shaders folder?')
        return 1
    print(f'\n{len(done)} shader(s) written to {args.out}')
    print('copy them to /atmosphere/contents/0100A5B00BDC6000/romfs/shaders/')
    return 0


if __name__ == '__main__':
    sys.exit(main())

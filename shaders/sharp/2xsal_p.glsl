#version 320

precision highp float;

layout(binding=0) uniform BlockFragment {
	vec4 pParam1;
};

layout(location = 0, binding = 0) uniform sampler2D Sampler0;

in vec2 vTextureCoord0;
in vec2 vTextureCoord1;
in vec2 vTextureCoord2;
in vec2 vTextureCoord3;

layout(location = 0) out vec4 pColor;

// ---------------------------------------------------------------------------
// sharp -- 7th_heaven_nx.  POINT SAMPLE + THE GRADING, AND NOTHING ELSE.
//
// WHY THIS EXISTS. Two separate defects, one in `hd` and one in `crisp`.
//
// `hd` IS A BLUR AT SCALE 3. It keeps the stock 2xSaI kernel -- a four-tap
// directional blend -- which exists to RECONSTRUCT a background being
// magnified. With 768px pages and a 3x render target the background lands
// exactly 1:1 (the build's own report: "matched: 768px pages are exactly
// resolved at 3x"), so there is nothing to reconstruct and the four taps are
// pure loss. MEASURED against an FFNx frame of the same field, edge ramp
// width (pixels lying between 15% and 85% of a strong luma step):
//
//                        FFNx        ours (hd)
//     sky / girders    0.97 px       1.98 px      and 1,696 vs 375 edges
//     left machinery   1.58 px       1.94 px
//     Barret           1.04 px       1.68 px
//
// Barret is a 3D MODEL -- same geometry in both frames, 525 vs 527 edges
// found, so it is the same silhouette -- and ours is 60% softer. A model
// cannot be blurred by a background kernel, so that part is FXAA; the extra
// softness on the background on top of it is this kernel. `ff7nx_shaders`
// says as much in its own docstring: "If you run scale 3, `crisp` is the one
// worth trying -- the reconstruction has already happened."
//
// `crisp` FIXES THE BLUR AND INTRODUCES TWO BUGS.
//
//   1. IT SAMPLES OUTSIDE THE RECT. It computes
//      `uv = (floor(centre * texSize) + 0.5) / texSize` with NO CLAMP. At
//      exactly 1:1 the texel grid and the pixel grid are aligned, so the
//      floor argument sits on an integer boundary and floating-point noise
//      decides between texel N and N-1. At the edge of the drawn region that
//      lands OUTSIDE it, on the colour key or on never-written page area,
//      which draws BLACK. That is the "edges extend out to where it should be
//      transparent but end up black" and the black corners, and it is why
//      `crisp` is not the answer even though its direction is right.
//
//   2. IT DROPS THE GRADING ENTIRELY. `hd_grade_rgb` is not called at all, so
//      HD_BLACK_POINT never runs -- and that constant is not decoration, it
//      is sized to exactly cancel `field_bg_native.NEAR_BLACK`. A truecolor
//      page has no index channel, so 0x0000 must mean TRANSPARENT and solid
//      black is stored lifted to 0x0841 = RGB(8,8,8) = 8/255. HD_BLACK_POINT
//      is 0.03137 = 8/255 precisely to put it back on zero. Without it every
//      black in every promoted cell renders as grey, which is the exact
//      failure the note in `hd` says was once rejected as "grey and weird".
//
// SO: take `crisp`'s single tap, clamp it so it cannot leave the neighbourhood
// the stock kernel itself would have read, and keep `hd`'s grading verbatim.
//
// The clamp is to the span of tap 0 and tap 3, which are the two diagonal
// taps of the stock kernel. Those are by construction inside the rect the
// stock filter samples, so a sample clamped into that span cannot reach
// anything the stock filter could not. It costs one min, one max and one
// clamp, and it makes bug 1 unreachable rather than unlikely.
// ---------------------------------------------------------------------------

const float HD_BLACK_POINT = 0.03137;   // = 8/255, cancels NEAR_BLACK 0x0841
const float HD_SATURATION  = 1.05;

vec3 hd_grade_rgb(vec3 rgb, vec2 ts)
{
	// the font atlas is the only 512x512 texture in play -- leave text alone
	if (ts.x == 512.0 && ts.y == 512.0) return rgb;

	if (HD_BLACK_POINT > 0.0)
	{
		rgb = max(rgb - HD_BLACK_POINT, 0.0) / (1.0 - HD_BLACK_POINT);
	}
	if (HD_SATURATION != 1.0)
	{
		float l = dot(rgb, vec3(0.299, 0.587, 0.114));
		rgb = clamp(mix(vec3(l), rgb, HD_SATURATION), 0.0, 1.0);
	}
	return rgb;
}

void main()
{
	// patched by 7th_heaven_nx patch_shaders.py
	float keep = pParam1.w * 0.0;

	vec2 ts = vec2(textureSize(Sampler0, 0));
	vec2 c  = (vTextureCoord0 + vTextureCoord3) * 0.5;
	vec2 lo = min(vTextureCoord0, vTextureCoord3);
	vec2 hi = max(vTextureCoord0, vTextureCoord3);
	vec2 uv = clamp((floor(c * ts) + 0.5) / ts, lo, hi);

	vec4 s = texture(Sampler0, uv);
	pColor = vec4(hd_grade_rgb(s.rgb, ts), s.a) + vec4(keep);
}

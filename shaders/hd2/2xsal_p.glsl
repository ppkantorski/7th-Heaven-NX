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

// ---------------------------------------------------------------- HD core
// Catmull-Rom bicubic reconstruction, 9 hardware-bilinear taps.
//
// WHY NOT xBR/HQ4X: those are PIXEL-ART algorithms. They look for hard steps
// between flat colour regions and rebuild them as clean diagonals. Cosmos
// Limit Break's backgrounds are AI-upscaled continuous-tone art, so there are
// no flat regions and no hard steps -- the detector either finds nothing (and
// falls back to a blend, i.e. soft) or fires on gradient noise (and produces
// the waxy, plastic, over-smoothed look). "Crisp" is nearest-neighbour, which
// is why it looks blocky.
//
// What continuous-tone art wants is a good RECONSTRUCTION filter. Catmull-Rom
// is interpolating (it passes exactly through every source texel, so nothing
// is smeared) and its negative lobes restore the high frequencies bilinear
// throws away. Smooth AND crisp, which is the combination that was missing.

// How much extra acutance on top of Catmull-Rom. 0.0 = pure Catmull-Rom.
//
// MEASURED, not guessed. Seven real 256x256 crops from a built flevel were
// downsampled 2x and reconstructed, and the result compared against the
// original -- so "better" means CLOSER TO THE TRUE ART, not merely sharper.
// With the anti-ring clamp applied, as it is below:
//
//     HD_SHARPEN     RMSE    detail   pixels hitting the clamp
//       0.35        6.255     73.0%        24.4%     <- the old default
//       0.70        6.030     75.1%        28.1%
//       1.00        5.876     76.5%        31.3%     <- now
//       1.50        5.688     78.2%        36.6%
//       3.00        5.489     81.2%        49.1%     RMSE minimum
//       6.00        5.793     83.4%        64.8%     past it, worse again
//
// The sharpening is NOT inventing detail: error falls monotonically until
// about 3.0. It is recovering high frequencies the reconstruction alone
// leaves behind. 0.35 was leaving real accuracy on the table.
//
// The reason to stop at 1.0 rather than 3.0 is the last column. A clamped
// pixel is one the sharpen pushed outside its 2x2 source range, so it got
// pulled back to the edge of it. At 3.0 half of all pixels are pinned to a
// neighbourhood extreme, which is posterisation -- exactly the "pixelated"
// look the filter exists to avoid. RMSE cannot see that; it reads a hard
// edge as accurate. 1.0 takes most of the accuracy for a clamp rate barely
// above the old default.
//
// For reference, this beats a bigger kernel outright: Lanczos-3 scored
// RMSE 7.326 / 80.4% detail at 25-36 taps, against 5.876 / 76.5% here at 13.
// Sharpening a good short kernel is worth more than a longer one.
//
// Sharpening luminance only was also tried and is slightly WORSE
// (6.067 vs 6.030 at 0.70), so this stays RGB.
//
// 1.5 if you want more; past 3.0 it degrades on the measurements, not just
// to taste.
//
// RE-MEASURED AT THE RATIO THIS BUILD ACTUALLY USES, WHICH CHANGES THE ANSWER.
//
// The table above was taken on 2x-downsampled crops. The 16:9 framing patch
// puts the field buffer at EXACTLY 3.0000 pixels per texel ("whole -- no
// resample, no beat"), and at a whole ratio every texel is sampled at the same
// three sub-texel phases. Anything in this shader that varies with phase stops
// being scattered noise and becomes a fixed lattice across the entire screen.
//
// Two things here vary with phase. `(cr - bil)` is zero at texel centres and
// peaks at texel boundaries BY CONSTRUCTION -- that is what makes it a
// sharpener. And a hard `clamp` fires on the pixels furthest from a texel
// centre. Both paint the same 3-pixel grid.
//
// MEASURED on six real truecolor pages from a built flevel, upscaled 3x.
// "lattice" is the standard deviation of the mean output across the nine
// sub-texel phases -- how much of the image depends on WHERE in a texel a
// pixel sits, which is the artefact. "acutance" is mean |d/dx|.
//
//     mode                                lattice   acutance
//     bilinear (soft/)                     0.0286      2.72   <- the floor
//     pure Catmull-Rom, no clamp           0.0285      3.02
//     CR + sharpen 0.35, hard clamp        0.0311      2.99
//     CR + sharpen 0.70, hard clamp        0.0340      3.06
//     CR + sharpen 1.00, hard clamp        0.0369      3.12   <- hd
//     CR + sharpen 0.35, soft knee 1.00    0.0288      3.00   <- hd2
//
// Catmull-Rom on its own costs NOTHING in lattice over bilinear and buys most
// of the acutance. The hard clamp and a heavy sharpen are what build the grid:
// hd sits 29% above the floor. hd2 takes 96% of hd's acutance at 0.7% above
// the floor.
//
// This was reported from hardware as a fine dot pattern over the whole frame.
// The archive was ruled out first: the built pages' autocorrelation decays
// monotonically at lags 1,2,3,4,6,8 with no bump, and is SMOOTHER than vanilla
// at every one. Nothing periodic is in the pixels; it is added here.
const float HD_SHARPEN = 0.35;

// Anti-ringing. Catmull-Rom overshoots at high-contrast edges, which shows up
// as a bright/dark halo. Clamping the result into the range of the four
// nearest texels removes the halo without softening the edge itself.
// 1.0 = full clamp, 0.0 = off.
const float HD_ANTIRING = 1.0;

// How far past the 2x2 source range the soft knee lets a pixel go before it
// bends. 0.0 reproduces the old hard clamp's limits but with a smooth knee;
// 0.25 gives a quarter of the local range of headroom, which keeps the
// acutance HD_SHARPEN is there to provide.
//
// This exists because the hard clamp it replaces was firing on 31.3% of
// pixels at a fixed sub-texel position, which at a whole 3.0 pixels-per-texel
// ratio is a visible 3-pixel lattice rather than the scattered noise it is at
// non-integer ratios. See the note in hd_sample.
const float HD_OVERSHOOT = 1.00;

vec4 hd_catmullrom(sampler2D tex, vec2 uv, vec2 ts)
{
	vec2 pos = uv * ts;
	vec2 c1  = floor(pos - 0.5) + 0.5;
	vec2 f   = pos - c1;

	vec2 w0 = f * (-0.5 + f * (1.0 - 0.5 * f));
	vec2 w1 = 1.0 + f * f * (-2.5 + 1.5 * f);
	vec2 w2 = f * (0.5 + f * (2.0 - 1.5 * f));
	vec2 w3 = f * f * (-0.5 + 0.5 * f);

	// fold the two centre taps into one bilinear fetch: 16 taps -> 9
	vec2 w12 = w1 + w2;
	vec2 o12 = w2 / w12;

	vec2 t0  = (c1 - 1.0) / ts;
	vec2 t3  = (c1 + 2.0) / ts;
	vec2 t12 = (c1 + o12) / ts;

	vec4 r = vec4(0.0);
	r += texture(tex, vec2(t0.x,  t0.y))  * (w0.x  * w0.y);
	r += texture(tex, vec2(t12.x, t0.y))  * (w12.x * w0.y);
	r += texture(tex, vec2(t3.x,  t0.y))  * (w3.x  * w0.y);
	r += texture(tex, vec2(t0.x,  t12.y)) * (w0.x  * w12.y);
	r += texture(tex, vec2(t12.x, t12.y)) * (w12.x * w12.y);
	r += texture(tex, vec2(t3.x,  t12.y)) * (w3.x  * w12.y);
	r += texture(tex, vec2(t0.x,  t3.y))  * (w0.x  * w3.y);
	r += texture(tex, vec2(t12.x, t3.y))  * (w12.x * w3.y);
	r += texture(tex, vec2(t3.x,  t3.y))  * (w3.x  * w3.y);
	return r;
}

// ---- background-only grading -------------------------------------------
// These touch the FIELD BACKGROUND ONLY. Character models, battle and UI are
// drawn by other shaders (colortex_p.glsl and friends) and are not affected,
// so this cannot disturb anything that already looks right.
//
// WHY THIS EXISTS. The packer used to quantise by truncation (>> 3), which
// always rounds DOWN and biased every background pixel -3.49/255, up to
// -7/255 in the shadows. That was a bug and it is fixed -- the quantiser now
// rounds, measured bias 0.00 -- but the accidental side effect was crushed
// blacks, which read as contrast. Correct shadows look "faded" beside them.
//
// So the DATA is right and the LOOK changed. Rather than un-fix the packer
// and bring the banding back, the punch is restored here where it costs
// nothing and is tunable.

// Pulls the bottom of the range back down. 0.014 (= 3.5/255) undoes exactly
// the lift the quantiser fix introduced; 0.02-0.03 goes beyond it for more
// contrast. 0.0 is neutral and photometrically correct.
const float HD_BLACK_POINT = 0.014;

// 1.0 is untouched. 1.05-1.15 if the upscaled art looks washed out -- AI
// upscales frequently desaturate slightly, and that is the mod's art rather
// than anything this shader did.
const float HD_SATURATION = 1.05;

vec4 hd_grade(vec4 c)
{
	if (HD_BLACK_POINT > 0.0)
	{
		c.rgb = max(c.rgb - HD_BLACK_POINT, 0.0) / (1.0 - HD_BLACK_POINT);
	}
	if (HD_SATURATION != 1.0)
	{
		float l = dot(c.rgb, vec3(0.299, 0.587, 0.114));
		c.rgb = clamp(mix(vec3(l), c.rgb, HD_SATURATION), 0.0, 1.0);
	}
	return c;
}

vec4 hd_sample(sampler2D tex, vec2 uv)
{
	vec2 ts = vec2(textureSize(tex, 0));
	vec4 cr = hd_catmullrom(tex, uv, ts);

	// the four texels this pixel sits between -- used both for the extra
	// acutance and for the halo clamp, so they cost one fetch each and
	// nothing more
	vec2 pos = uv * ts;
	vec2 c   = floor(pos - 0.5) + 0.5;
	vec4 s00 = texture(tex, (c + vec2(0.0, 0.0)) / ts);
	vec4 s10 = texture(tex, (c + vec2(1.0, 0.0)) / ts);
	vec4 s01 = texture(tex, (c + vec2(0.0, 1.0)) / ts);
	vec4 s11 = texture(tex, (c + vec2(1.0, 1.0)) / ts);

	// unsharp against the bilinear reconstruction of the same four texels.
	// (cr - bilinear) IS the detail Catmull-Rom recovers, so scaling it is
	// edge sharpening by construction -- it is zero in flat areas and only
	// grows where there is real structure, so it cannot amplify noise.
	vec2 fr   = pos - c;
	vec4 bil  = mix(mix(s00, s10, fr.x), mix(s01, s11, fr.x), fr.y);
	vec4 outc = cr + (cr - bil) * HD_SHARPEN;

	vec4 lo = min(min(s00, s10), min(s01, s11));
	vec4 hi = max(max(s00, s10), max(s01, s11));
	// SOFT-KNEE ANTI-RINGING. A HARD CLAMP IS WHAT MADE THE DOT GRID.
	//
	// `clamp(outc, lo, hi)` is binary: a pixel is either untouched or pinned
	// to exactly a neighbourhood extreme. Whether it clamps depends on where
	// it falls INSIDE its texel, because lo/hi come from the four texels it
	// sits between. The 16:9 framing patch puts the field buffer at exactly
	// 3.0000 pixels per texel ("whole -- no resample, no beat"), so the same
	// sub-texel positions clamp in every texel across the whole screen and
	// the clamped pixels form a lattice with a 3-pixel period.
	//
	// That is the fine dot grid reported from hardware, uniform over the
	// entire frame and independent of the art. MEASURED against the archive
	// to rule the art out: the built pages' autocorrelation decays
	// monotonically at lags 1,2,3,4,6,8 with no bump at any of them, and is
	// SMOOTHER than vanilla at every one -- so nothing periodic is in the
	// pixels. It is added here.
	//
	// The shader's own table says 31.3% of pixels hit the clamp at
	// HD_SHARPEN = 1.0, and rejects 3.0 because 49.1% "is posterisation".
	// A third of the frame pinned to neighbourhood extremes on a fixed
	// lattice is the same failure, one step milder.
	//
	// tanh compresses smoothly and asymptotically instead. Big overshoots
	// still get pulled back -- that is the halo control the clamp was for --
	// but nothing is ever pinned, so no two pixels land on the same value for
	// the same reason and there is no lattice to see. HD_OVERSHOOT sets how
	// far past the 2x2 range the knee allows before it bends.
	vec4 rng = max(hi - lo, vec4(1.0 / 255.0));
	vec4 mid = (hi + lo) * 0.5;
	vec4 lim = rng * 0.5 * (1.0 + HD_OVERSHOOT);
	vec4 soft = mid + lim * tanh((outc - mid) / lim);
	return mix(outc, soft, HD_ANTIRING);
}

void main()
{
	// hd2 mode -- 7th_heaven_nx. pParam1 is unused here but the uniform block
	// must stay referenced or the binding is optimised away and the draw
	// breaks, so it is folded in at zero weight.
	float keep = pParam1.w * 0.0;

	// the vertex shader hands over four half-texel-offset taps; their mean is
	// the true sample centre
	vec2 uv = (vTextureCoord0 + vTextureCoord3) * 0.5;
	pColor = hd_grade(hd_sample(Sampler0, uv)) + vec4(keep);
}

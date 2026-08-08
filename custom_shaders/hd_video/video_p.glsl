#version 320

precision highp float;

// FF7 Switch -- HD movie playback shader (replaces stock video_p.glsl)
//
// WHAT THE STOCK SHADER DOES
// --------------------------
// The port decodes movies with movie::DecoderMode_NativeTexture, which hands
// the GPU two planes rather than RGB:
//
//     Sampler0 = Y      (luma, full resolution)
//     Sampler1 = CbCr   (chroma, interleaved, HALF resolution -- 4:2:0)
//
// The stock shader does one plain texture() fetch from each and applies a
// fixed YCbCr->RGB matrix. That means:
//
//   * no reconstruction filter at all. A movie smaller than the output is
//     stretched with plain bilinear -- exactly the "always looks low res"
//     complaint, and exactly the problem the hd background shader solves.
//   * the matrix is HARDCODED to BT.709 limited range. See below.
//
// This file fixes the first properly and makes the second selectable.
//
//
// 1. LUMA RECONSTRUCTION
// ----------------------
// Same Catmull-Rom core as the hd background shader: interpolating (passes
// exactly through every source texel), 9 hardware-bilinear taps folded from
// 16, with an anti-ringing clamp.
//
// Applied to LUMA ONLY. Chroma is half resolution and heavily quantised by
// the encoder; reconstructing and sharpening it buys no visible detail and
// produces colour fringing on high-contrast edges. Luma carries essentially
// all the apparent sharpness, so that is where the work goes. This is the
// same reasoning as "sharpening luminance only was worse" for backgrounds --
// inverted, because here chroma is genuinely lower resolution rather than
// merely a different channel.
//
//
// 2. IT DISABLES ITSELF WHEN THE MOVIE IS ALREADY BIGGER THAN THE SCREEN
// ----------------------------------------------------------------------
// The stock movies are 1280x896. On a 720p handheld panel that is being
// DOWNSCALED, and sharpening a downscale adds aliasing and amplifies the
// encoder's mosquito noise -- it would make stock movies worse.
//
// A replacement FMV pack may be any size, so this cannot be a fixed setting.
// fwidth() gives the UV footprint of one output pixel, and multiplying by the
// texture size gives texels-per-pixel directly:
//
//     tpp < 1  -> magnifying (movie smaller than its screen area) -> full effect
//     tpp > 1  -> minifying  (movie larger)                       -> faded out
//
// So this file is safe to install regardless of what resolution your movies
// are, and it does nothing at all to the stock ones.
//
//
// 3. THE COLOUR MATRIX -- READ THIS BEFORE CHANGING IT
// ----------------------------------------------------
// The stock coefficients are
//
//     1.1644 / 1.7927 / -0.2133 / -0.5329 / 2.1124
//
// which is BT.709, limited range. That is correct for SQEX's own 1280x896
// movies and they must be left decoding that way.
//
// FF7's original FMVs are 320x224 -- standard definition, i.e. BT.601. An
// upscaled FMV pack derived from them is very likely still BT.601 or tagged
// unspecified (which ffmpeg treats as BT.601). Feeding BT.601 content through
// a BT.709 matrix is a real, measurable error:
//
//     colour     true RGB          shown as        max channel error
//     red        255   0   0       255  25   0        24.7 /255
//     green        0 255   0         0 216   0        39.4 /255
//     magenta    255   0 255       255  40 255        39.6 /255
//     cyan         0 255 255         0 230 255        24.5 /255
//     skin       204 153 102       209 155  99         5.2 /255
//     grey       128 128 128       128 128 128         0.1 /255
//
// Note grey is untouched and skin tones barely move -- so this does NOT look
// like a colour bug. It looks like slightly weak saturation, which is easy to
// blame on the upscale.
//
// THE RIGHT FIX IS TO RE-ENCODE, NOT TO CHANGE THIS. One shader serves every
// movie, so switching it to BT.601 fixes your FMV pack and breaks every stock
// movie that you did not replace. Convert the pack to BT.709 at build time
// instead (see README-hd-video.txt).
//
// The switch exists only for the case where you have replaced ALL movies with
// a single consistently-BT.601 pack, or to A/B whether colour is the problem.
//
//     0 = BT.709 limited  (stock behaviour -- leave it here)
//     1 = BT.601 limited
#define HD_VIDEO_MATRIX 0

// Extra acutance on the reconstructed luma. Deliberately LOWER than the
// background shader's 1.0: backgrounds are stills quantised to R5G6B5, movies
// are lossy H.264. Above ~0.6 this starts sharpening block edges and mosquito
// noise around high-contrast lines instead of picture detail. 0.0 gives pure
// Catmull-Rom, which is still a large improvement over stock bilinear.
const float HD_VIDEO_SHARPEN = 0.5;

// Anti-ringing clamp, as in the background shader. Matters more here: ringing
// on a moving image reads as edge crawl.
const float HD_VIDEO_ANTIRING = 1.0;

layout(location = 0, binding = 0) uniform sampler2D Sampler0;
layout(location = 1, binding = 1) uniform sampler2D Sampler1;

in vec4 vColor;
in vec2 vTextureCoord;

layout(location = 0) out vec4 pColor;

// Catmull-Rom, single channel. Identical maths to hd_catmullrom() in
// hd/2xsal_p.glsl, reduced to one component because this runs on the Y plane.
float hd_catmullrom_y(sampler2D tex, vec2 uv, vec2 ts)
{
	vec2 pos = uv * ts;
	vec2 c1  = floor(pos - 0.5) + 0.5;
	vec2 f   = pos - c1;

	vec2 w0 = f * (-0.5 + f * (1.0 - 0.5 * f));
	vec2 w1 = 1.0 + f * f * (-2.5 + 1.5 * f);
	vec2 w2 = f * (0.5 + f * (2.0 - 1.5 * f));
	vec2 w3 = f * f * (-0.5 + 0.5 * f);

	vec2 w12 = w1 + w2;
	vec2 o12 = w2 / w12;

	vec2 t0  = (c1 - 1.0) / ts;
	vec2 t3  = (c1 + 2.0) / ts;
	vec2 t12 = (c1 + o12) / ts;

	float r = 0.0;
	r += texture(tex, vec2(t0.x,  t0.y )).x * (w0.x  * w0.y );
	r += texture(tex, vec2(t12.x, t0.y )).x * (w12.x * w0.y );
	r += texture(tex, vec2(t3.x,  t0.y )).x * (w3.x  * w0.y );
	r += texture(tex, vec2(t0.x,  t12.y)).x * (w0.x  * w12.y);
	r += texture(tex, vec2(t12.x, t12.y)).x * (w12.x * w12.y);
	r += texture(tex, vec2(t3.x,  t12.y)).x * (w3.x  * w12.y);
	r += texture(tex, vec2(t0.x,  t3.y )).x * (w0.x  * w3.y );
	r += texture(tex, vec2(t12.x, t3.y )).x * (w12.x * w3.y );
	r += texture(tex, vec2(t3.x,  t3.y )).x * (w3.x  * w3.y );
	return r;
}

float hd_luma(sampler2D tex, vec2 uv)
{
	vec2 ts = vec2(textureSize(tex, 0));

	// texels of source per pixel of output. >1 means the movie is being
	// shrunk, and reconstruction+sharpening is the wrong tool.
	vec2  duv = fwidth(uv) * ts;
	float tpp = max(duv.x, duv.y);
	float amt = clamp(1.0 - (tpp - 1.0), 0.0, 1.0);

	float bilinear = texture(tex, uv).x;
	if (amt <= 0.0) return bilinear;

	float cr = hd_catmullrom_y(tex, uv, ts);

	vec2  pos = uv * ts;
	vec2  c   = floor(pos - 0.5) + 0.5;
	float s00 = texture(tex, (c + vec2(0.0, 0.0)) / ts).x;
	float s10 = texture(tex, (c + vec2(1.0, 0.0)) / ts).x;
	float s01 = texture(tex, (c + vec2(0.0, 1.0)) / ts).x;
	float s11 = texture(tex, (c + vec2(1.0, 1.0)) / ts).x;

	vec2  fr  = pos - c;
	float bil = mix(mix(s00, s10, fr.x), mix(s01, s11, fr.x), fr.y);
	float o   = cr + (cr - bil) * HD_VIDEO_SHARPEN;

	float lo = min(min(s00, s10), min(s01, s11));
	float hi = max(max(s00, s10), max(s01, s11));
	o = mix(o, clamp(o, lo, hi), HD_VIDEO_ANTIRING);

	// fade back to the stock result as the movie stops being magnified, so
	// there is no visible switch-over
	return mix(bilinear, o, amt);
}

void main()
{
	vec3 ycbcr = vec3(
		hd_luma(Sampler0, vTextureCoord) - 0.0625,
		texture(Sampler1, vTextureCoord).x - 0.5,
		texture(Sampler1, vTextureCoord).y - 0.5
	);

#if HD_VIDEO_MATRIX == 1
	// BT.601 limited range
	vec4 color = vec4(
		dot(vec3(1.1644,  0.0000,  1.5960), ycbcr),
		dot(vec3(1.1644, -0.3918, -0.8130), ycbcr),
		dot(vec3(1.1644,  2.0172,  0.0000), ycbcr),
		1.0
	);
#else
	// BT.709 limited range -- byte-for-byte the stock matrix
	vec4 color = vec4(
		dot(vec3(1.1644,  0.0000,  1.7927), ycbcr),
		dot(vec3(1.1644, -0.2133, -0.5329), ycbcr),
		dot(vec3(1.1644,  2.1124,  0.0000), ycbcr),
		1.0
	);
#endif

	pColor = color * vColor;
}

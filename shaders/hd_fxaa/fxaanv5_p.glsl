#version 320

precision highp float;

layout(binding=0) uniform BlockFragment {
	vec2 rcpFrame;
};

layout(location = 0, binding = 0) uniform sampler2D Sampler0;

in vec2 vTextureCoord;

layout(location = 0) out vec4 pColor;


/*--------------------------------------------------------------------------*/
#define FxaaToFloat3(a) vec3((a), (a), (a))
/*--------------------------------------------------------------------------*/
vec4 FxaaTexLod0(sampler2D tex, vec2 pos) {
	return textureLod(tex, pos, 0.0);
}
/*--------------------------------------------------------------------------*/
vec4 FxaaTexGrad(sampler2D tex, vec2 pos, vec2 grad) {
	return textureGrad(tex, pos, grad, grad);
}
/*--------------------------------------------------------------------------*/
vec4 FxaaTexOff(sampler2D tex, vec2 pos, ivec2 off) {
	return textureLodOffset(tex, pos, 0.0, off);
}
/*--------------------------------------------------------------------------*/
// TUNED FOR AN HD BACKGROUND -- 7th_heaven_nx.
//
// Only these two constants change. Everything below is stock NVIDIA FXAA 3.x.
//
// FXAA cannot tell a model silhouette from background texture; it filters
// anything that passes this contrast test. On a 320x240 game that was fine,
// because the background was as aliased as the geometry. With a sharpened
// 512px background it is not: FXAA was rewriting 73.4% of the playfield,
// resampling each pixel with up to a half-pixel offset, to fix edges that are
// a few percent of the frame.
//
// MEASURED on a real battle frame (960x450 of playfield, model silhouettes
// against a pre-rendered scene). "edges" = top 5% local contrast,
// "background" = bottom 60%:
//
//     threshold        AA kept on edges     background touched
//     1/8  (stock)          100.0%                55.8%
//     1/6                   100.0%                37.2%
//     1/4  (this)            99.8%                20.3%
//     1/3                    99.2%                11.5%
//
// Silhouettes are high contrast and texture is not, so the threshold splits
// them almost perfectly. 1/4 keeps essentially all the edge anti-aliasing
// while cutting untouched-background resampling by 2.7x.
//
// FXAA_SUBPIX_CAP is deliberately LEFT AT 0.75. It looked like the obvious
// thing to cut, but the mean sub-pixel blur weight measured only 0.076 -- it
// is not what was softening the picture, and it is what suppresses shimmer on
// single-pixel highlights, which a static measurement cannot see.
#define FXAA_EDGE_THRESHOLD      (1.0/4.0)
#define FXAA_EDGE_THRESHOLD_MIN  (1.0/12.0)
#define FXAA_SEARCH_STEPS        32
#define FXAA_SEARCH_ACCELERATION 1
#define FXAA_SEARCH_THRESHOLD    (1.0/4.0)
#define FXAA_SUBPIX              1
#define FXAA_SUBPIX_FASTER       0
#define FXAA_SUBPIX_CAP          (3.0/4.0)
#define FXAA_SUBPIX_TRIM         (1.0/4.0)

/*--------------------------------------------------------------------------*/
#define FXAA_SUBPIX_TRIM_SCALE (1.0/(1.0 - FXAA_SUBPIX_TRIM))

/*============================================================================
HELPERS
============================================================================*/
// Return the luma, the estimation of luminance from rgb inputs.
// This approximates luma using one FMA instruction,
// skipping normalization and tossing out blue.
// FxaaLuma() will range 0.0 to 2.963210702.
float FxaaLuma(vec3 rgb) {
	return rgb.y * (0.587 / 0.299) + rgb.x;
}
/*--------------------------------------------------------------------------*/
vec3 FxaaLerp3(vec3 a, vec3 b, float amountOfA) {
	return (FxaaToFloat3(-amountOfA) * b) + ((a * FxaaToFloat3(amountOfA)) + b);
}
/*--------------------------------------------------------------------------*/
// Support any extra filtering before returning color.
vec3 FxaaFilterReturn(vec3 rgb) {
	return rgb;
}

/*============================================================================
PIXEL SHADER
============================================================================*/
vec3 FxaaPixelShader(
	// Output of FxaaVertexShader interpolated across screen.
	//  xy -> actual texture position {0.0 to 1.0}
	vec2 pos,
	// Input texture.
	sampler2D tex,
	// RCPFRAME SHOULD PIXEL SHADER CONSTANTS!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
	// {1.0/frameWidth, 1.0/frameHeight}
	vec2 rcpFrame) {

	/*----------------------------------------------------------------------------
	EARLY EXIT IF LOCAL CONTRAST BELOW EDGE DETECT LIMIT
	------------------------------------------------------------------------------
	Majority of pixels of a typical image do not require filtering,
	often pixels are grouped into blocks which could benefit from early exit
	right at the beginning of the algorithm.
	Given the following neighborhood,

	N
	W M E
	S

	If the difference in local maximum and minimum luma (contrast "range")
	is lower than a threshold proportional to the maximum local luma ("rangeMax"),
	then the shader early exits (no visible aliasing).
	This threshold is clamped at a minimum value ("FXAA_EDGE_THRESHOLD_MIN")
	to avoid processing in really dark areas.
	----------------------------------------------------------------------------*/
	vec3 rgbN = FxaaTexOff(tex, pos.xy, ivec2(0, -1)).xyz;
	vec3 rgbW = FxaaTexOff(tex, pos.xy, ivec2(-1, 0)).xyz;
	vec3 rgbM = FxaaTexOff(tex, pos.xy, ivec2(0, 0)).xyz;
	vec3 rgbE = FxaaTexOff(tex, pos.xy, ivec2(1, 0)).xyz;
	vec3 rgbS = FxaaTexOff(tex, pos.xy, ivec2(0, 1)).xyz;
	float lumaN = FxaaLuma(rgbN);
	float lumaW = FxaaLuma(rgbW);
	float lumaM = FxaaLuma(rgbM);
	float lumaE = FxaaLuma(rgbE);
	float lumaS = FxaaLuma(rgbS);
	float rangeMin = min(lumaM, min(min(lumaN, lumaW), min(lumaS, lumaE)));
	float rangeMax = max(lumaM, max(max(lumaN, lumaW), max(lumaS, lumaE)));
	float range = rangeMax - rangeMin;
	if (range < max(FXAA_EDGE_THRESHOLD_MIN, rangeMax * FXAA_EDGE_THRESHOLD)) {
		return FxaaFilterReturn(rgbM);
	}
#if FXAA_SUBPIX > 0
#if FXAA_SUBPIX_FASTER
	vec3 rgbL = (rgbN + rgbW + rgbE + rgbS + rgbM) * FxaaToFloat3(1.0 / 5.0);
#else
	vec3 rgbL = rgbN + rgbW + rgbM + rgbE + rgbS;
#endif
#endif

	/*----------------------------------------------------------------------------
	COMPUTE LOWPASS
	------------------------------------------------------------------------------
	FXAA computes a local neighborhood lowpass value as follows,

	(N + W + E + S)/4

	Then uses the ratio of the contrast range of the lowpass
	and the range found in the early exit check,
	as a sub-pixel aliasing detection filter.
	When FXAA detects sub-pixel aliasing (such as single pixel dots),
	it later blends in "blendL" amount
	of a lowpass value (computed in the next section) to the final result.
	----------------------------------------------------------------------------*/
#if FXAA_SUBPIX != 0
	float lumaL = (lumaN + lumaW + lumaE + lumaS) * 0.25;
	float rangeL = abs(lumaL - lumaM);
#endif        
#if FXAA_SUBPIX == 1
	float blendL = max(0.0, (rangeL / range) - FXAA_SUBPIX_TRIM) * FXAA_SUBPIX_TRIM_SCALE;
	blendL = min(FXAA_SUBPIX_CAP, blendL);
#endif
#if FXAA_SUBPIX == 2
	float blendL = rangeL / range;
#endif

	/*----------------------------------------------------------------------------
	CHOOSE VERTICAL OR HORIZONTAL SEARCH
	------------------------------------------------------------------------------
	FXAA uses the following local neighborhood,

	NW N NE
	W  M  E
	SW S SE

	To compute an edge amount for both vertical and horizontal directions.
	Note edge detect filters like Sobel fail on single pixel lines through M.
	FXAA takes the weighted average magnitude of the high-pass values
	for rows and columns as an indication of local edge amount.

	A lowpass value for anti-sub-pixel-aliasing is computed as
	(N+W+E+S+M+NW+NE+SW+SE)/9.
	This full box pattern has higher quality than other options.

	Note following this block, both vertical and horizontal cases
	flow in parallel (reusing the horizontal variables).
	----------------------------------------------------------------------------*/
	vec3 rgbNW = FxaaTexOff(tex, pos.xy, ivec2(-1, -1)).xyz;
	vec3 rgbNE = FxaaTexOff(tex, pos.xy, ivec2(1, -1)).xyz;
	vec3 rgbSW = FxaaTexOff(tex, pos.xy, ivec2(-1, 1)).xyz;
	vec3 rgbSE = FxaaTexOff(tex, pos.xy, ivec2(1, 1)).xyz;
#if (FXAA_SUBPIX_FASTER == 0) && (FXAA_SUBPIX > 0)
	rgbL += (rgbNW + rgbNE + rgbSW + rgbSE);
	rgbL *= FxaaToFloat3(1.0 / 9.0);
#endif
	float lumaNW = FxaaLuma(rgbNW);
	float lumaNE = FxaaLuma(rgbNE);
	float lumaSW = FxaaLuma(rgbSW);
	float lumaSE = FxaaLuma(rgbSE);
	float edgeVert =
		abs((0.25 * lumaNW) + (-0.5 * lumaN) + (0.25 * lumaNE)) +
		abs((0.50 * lumaW) + (-1.0 * lumaM) + (0.50 * lumaE)) +
		abs((0.25 * lumaSW) + (-0.5 * lumaS) + (0.25 * lumaSE));
	float edgeHorz =
		abs((0.25 * lumaNW) + (-0.5 * lumaW) + (0.25 * lumaSW)) +
		abs((0.50 * lumaN) + (-1.0 * lumaM) + (0.50 * lumaS)) +
		abs((0.25 * lumaNE) + (-0.5 * lumaE) + (0.25 * lumaSE));
	bool horzSpan = edgeHorz >= edgeVert;
	float lengthSign = horzSpan ? -rcpFrame.y : -rcpFrame.x;
	if (!horzSpan) lumaN = lumaW;
	if (!horzSpan) lumaS = lumaE;
	float gradientN = abs(lumaN - lumaM);
	float gradientS = abs(lumaS - lumaM);
	lumaN = (lumaN + lumaM) * 0.5;
	lumaS = (lumaS + lumaM) * 0.5;

	/*----------------------------------------------------------------------------
	CHOOSE SIDE OF PIXEL WHERE GRADIENT IS HIGHEST
	------------------------------------------------------------------------------
	This chooses a pixel pair.
	For "horzSpan == true" this will be a vertical pair,

	[N]     N
	[M] or [M]
	S     [S]

	Note following this block, both {N,M} and {S,M} cases
	flow in parallel (reusing the {N,M} variables).

	This pair of image rows or columns is searched below
	in the positive and negative direction
	until edge status changes
	(or the maximum number of search steps is reached).
	----------------------------------------------------------------------------*/
	bool pairN = gradientN >= gradientS;
	if (!pairN) lumaN = lumaS;
	if (!pairN) gradientN = gradientS;
	if (!pairN) lengthSign *= -1.0;
	vec2 posN;
	posN.x = pos.x + (horzSpan ? 0.0 : lengthSign * 0.5);
	posN.y = pos.y + (horzSpan ? lengthSign * 0.5 : 0.0);

	/*----------------------------------------------------------------------------
	CHOOSE SEARCH LIMITING VALUES
	------------------------------------------------------------------------------
	Search limit (+/- gradientN) is a function of local gradient.
	----------------------------------------------------------------------------*/
	gradientN *= FXAA_SEARCH_THRESHOLD;

	/*----------------------------------------------------------------------------
	SEARCH IN BOTH DIRECTIONS UNTIL FIND LUMA PAIR AVERAGE IS OUT OF RANGE
	------------------------------------------------------------------------------
	This loop searches either in vertical or horizontal directions,
	and in both the negative and positive direction in parallel.
	This loop fusion is faster than searching separately.

	The search is accelerated using FXAA_SEARCH_ACCELERATION length box filter
	via anisotropic filtering with specified texture gradients.
	----------------------------------------------------------------------------*/
	vec2 posP = posN;
	vec2 offNP = horzSpan ?
		vec2(rcpFrame.x, 0.0) :
		vec2(0.0f, rcpFrame.y);
	float lumaEndN = lumaN;
	float lumaEndP = lumaN;
	bool doneN = false;
	bool doneP = false;

	posN += offNP * vec2(-1.0, -1.0);
	posP += offNP * vec2(1.0, 1.0);
	for (int i = 0; i < FXAA_SEARCH_STEPS; i++) {
		if (!doneN) lumaEndN =
			FxaaLuma(FxaaTexLod0(tex, posN.xy).xyz);
		if (!doneP) lumaEndP =
			FxaaLuma(FxaaTexLod0(tex, posP.xy).xyz);
		doneN = doneN || (abs(lumaEndN - lumaN) >= gradientN);
		doneP = doneP || (abs(lumaEndP - lumaN) >= gradientN);
		if (doneN && doneP) break;
		if (!doneN) posN -= offNP;
		if (!doneP) posP += offNP;
	}

	/*----------------------------------------------------------------------------
	HANDLE IF CENTER IS ON POSITIVE OR NEGATIVE SIDE
	------------------------------------------------------------------------------
	FXAA uses the pixel's position in the span
	in combination with the values (lumaEnd*) at the ends of the span,
	to determine filtering.

	This step computes which side of the span the pixel is on.
	On negative side if dstN < dstP,

	posN        pos                      posP
	|-----------|------|------------------|
	|           |      |                  |
	|<--dstN--->|<---------dstP---------->|
	|
	span center

	----------------------------------------------------------------------------*/
	float dstN = horzSpan ? pos.x - posN.x : pos.y - posN.y;
	float dstP = horzSpan ? posP.x - pos.x : posP.y - pos.y;
	bool directionN = dstN < dstP;

	lumaEndN = directionN ? lumaEndN : lumaEndP;

	/*----------------------------------------------------------------------------
	CHECK IF PIXEL IS IN SECTION OF SPAN WHICH GETS NO FILTERING
	------------------------------------------------------------------------------
	If both the pair luma at the end of the span (lumaEndN)
	and middle pixel luma (lumaM)
	are on the same side of the middle pair average luma (lumaN),
	then don't filter.

	Cases,

	(1.) "L",

	lumaM
	|
	V    XXXXXXXX <- other line averaged
	XXXXXXX[X]XXXXXXXXXXX <- source pixel line
	|      .      |
	--------------------------
	[ ]xxxxxx[x]xx[X]XXXXXX <- pair average
	--------------------------
	^      ^ ^    ^
	|      | |    |
	.      |<---->|<---------- no filter region
	.      | |    |
	. center |    |
	.        |  lumaEndN
	.        |    .
	.      lumaN  .
	.             .
	|<--- span -->|


	(2.) "^" and "-",

	<- other line averaged
	XXXXX[X]XXX          <- source pixel line
	|     |     |
	--------------------------
	[ ]xxxx[x]xx[ ]        <- pair average
	--------------------------
	|     |     |
	|<--->|<--->|<---------- filter both sides


	(3.) "v" and inverse of "-",

	XXXXXX           XXXXXXXXX <- other line averaged
	XXXXXXXXXXX[X]XXXXXXXXXXXX <- source pixel line
	|     |     |
	--------------------------
	XXXX[X]xxxx[x]xx[X]XXXXXXX <- pair average
	--------------------------
	|     |     |
	|<--->|<--->|<---------- don't filter both!


	Note the "v" case for FXAA requires no filtering.
	This is because the inverse of the "-" case is the "v".
	Filtering "v" case turns open spans like this,

	XXXXXXXXX

	Into this (which is not desired),

	x+.   .+x
	XXXXXXXXX

	----------------------------------------------------------------------------*/
	if (((lumaM - lumaN) < 0.0) == ((lumaEndN - lumaN) < 0.0))
		lengthSign = 0.0;

	/*----------------------------------------------------------------------------
	COMPUTE SUB-PIXEL OFFSET AND FILTER SPAN
	------------------------------------------------------------------------------
	FXAA filters using a bilinear texture fetch offset
	from the middle pixel M towards the center of the pair (NM below).
	Maximum filtering will be half way between pair.
	Reminder, at this point in the code,
	the {N,M} pair is also reused for all cases: {S,M}, {W,M}, and {E,M}.

	+-------+
	|       |    0.5 offset
	|   N   |     |
	|       |     V
	+-------+....---
	|       |
	|   M...|....---
	|       |     ^
	+-------+     |
	.       .    0.0 offset
	.   S   .
	.       .
	.........

	Position on span is used to compute sub-pixel filter offset using simple ramp,

	posN           posP
	|\             |<------- 0.5 pixel offset into pair pixel
	| \            |
	|  \           |
	---.......|...\..........|<------- 0.25 pixel offset into pair pixel
	^        |   ^\         |
	|        |   | \        |
	V        |   |  \       |
	---.......|===|==========|<------- 0.0 pixel offset (ie M pixel)
	^        .   |   ^      .
	|        .  pos  |      .
	|        .   .   |      .
	|        .   . center   .
	|        .   .          .
	|        |<->|<---------.-------- dstN
	|        .   .          .
	|        .   |<-------->|<------- dstP
	|        .             .
	|        |<------------>|<------- spanLength
	|
	subPixelOffset

	----------------------------------------------------------------------------*/
	float spanLength = (dstP + dstN);
	dstN = directionN ? dstN : dstP;
	float subPixelOffset = (0.5 + (dstN * (-1.0 / spanLength))) * lengthSign;
	vec3 rgbF = FxaaTexLod0(tex, vec2(pos.x + (horzSpan ? 0.0 : subPixelOffset), pos.y + (horzSpan ? subPixelOffset : 0.0))).xyz;
#if FXAA_SUBPIX == 0
	return FxaaFilterReturn(rgbF);
#else        
	return FxaaFilterReturn(FxaaLerp3(rgbL, rgbF, blendL));
#endif
}

void main()
{
	pColor = vec4(FxaaPixelShader(vTextureCoord, Sampler0, rcpFrame), 1.0f);
}

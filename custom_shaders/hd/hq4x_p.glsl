#version 320

precision highp float;

layout(binding=0) uniform BlockFragment {
	vec4 pParam1;
	vec4 pParam2;
};

layout(location = 0, binding = 0) uniform sampler2D Sampler0;

in vec2 vTextureCoord0;
in vec4 vTextureCoord1;
in vec4 vTextureCoord2;
in vec4 vTextureCoord3;
in vec4 vTextureCoord4;
in vec4 vTextureCoord5;
in vec4 vTextureCoord6;

layout(location = 0) out vec4 pColor;

// ---------------------------------------------------------------------------
// hd_grade_only -- 7th_heaven_nx
//
// This is the STOCK filter, byte-for-byte, plus the black-point and
// saturation grading from the `hd` set. The Catmull-Rom reconstruction and
// the unsharp mask are GONE.
//
// WHY. The `hd` set did two unrelated things: it RECONSTRUCTED (Catmull-Rom
// + `HD_SHARPEN` unsharp) and it GRADED (black point + saturation). Only the
// grading was ever visibly worth having -- with field textures at 256 the
// reconstruction has almost nothing to reconstruct. The unsharp, meanwhile,
// rings against hard high-contrast edges, and a white glyph inside a black
// outline is the worst input it can get: that is the "dirty black pixels"
// around every character. Confirmed on hardware -- restoring the stock
// filters made the text clean, and hd2 (HD_SHARPEN 0.35) still showed it,
// so lowering the sharpen was never going to be enough.
//
// The `HD_ANTIRING` clamp in the hd set did not help here and could not:
// it clamps to the local 2x2 min/max, and around text that range is the
// full black-to-white span, so it permits the entire overshoot.
//
// The grading is additionally SKIPPED for 512x512 textures. That is the font
// atlas (romfs/ff7/font/TBGoPro_Regular_0.png) and nothing else -- the field
// art in the cache is 1024x1024 (198 files), 1431x826, 6366x3103, 1920x1080
// and 400x225, with no 512x512 among them. So text comes out bit-identical
// to vanilla and the backgrounds still get their blacks back.
// ---------------------------------------------------------------------------

// Pulls the bottom of the range back down. It must equal the lift the
// repack is forced to put on black, or the residue is visible -- and for
// three revisions it did not.
//
// A truecolor field page has no index channel, so 0x0000 has to mean
// TRANSPARENT (x86 0x6470E0) and a black pixel is stored as the dimmest
// non-zero value the format has. R5G6B5's blue LSB is 255/31 = 8.2, so that
// lift is 8/255 = 0.0314. This constant was 0.014 (3.5/255), which undoes
// less than half of it:
//
//     stored 0x0001 = RGB(0,0,8)  ->  graded (0, 0, 4.5)   a BLUE residue
//     stored 0x0841 = RGB(8,8,8)  ->  graded (4.5,4.5,4.5) a GREY residue
//
// 4.5/255 of pure blue on every black outline in a promoted cell is the blue
// linework reported in Men's Hall. At 8/255 both land on exactly (0,0,0) and
// the lift becomes invisible whatever colour it is stored in -- which is why
// `field_bg_native.NEAR_BLACK` can stay the 0x0001 the rest of the project
// documents rather than being changed to hide a shader mismatch.
//
// 0.0 is neutral.
const float HD_BLACK_POINT = 0.04705882353;   // 8/255, the R5G6B5 LSB

// 1.0 is untouched. 1.05 counters the slight desaturation of AI upscales.
const float HD_SATURATION = 1.00;

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
	vec3 dt = pParam1.xyz;
	
	float mx = pParam1.w;		// start smoothing wt.
	float k = pParam2.x;		// wt. decrease factor
	float max_w = pParam2.y;	// max filter weigth
	float min_w = pParam2.z;	// min filter weigth
	float lum_add = pParam2.w;	// effects smoothing
	
	vec3 c  = texture(Sampler0, vTextureCoord0.xy).rgb;
	vec3 i1 = texture(Sampler0, vTextureCoord1.xy).rgb;
	vec3 i2 = texture(Sampler0, vTextureCoord2.xy).rgb;
	vec3 i3 = texture(Sampler0, vTextureCoord3.xy).rgb;
	vec3 i4 = texture(Sampler0, vTextureCoord4.xy).rgb;
	vec3 o1 = texture(Sampler0, vTextureCoord5.xy).rgb;
	vec3 o3 = texture(Sampler0, vTextureCoord6.xy).rgb;
	vec3 o2 = texture(Sampler0, vTextureCoord5.zw).rgb;
	vec3 o4 = texture(Sampler0, vTextureCoord6.zw).rgb;
	vec3 s1 = texture(Sampler0, vTextureCoord1.zw).rgb;
	vec3 s2 = texture(Sampler0, vTextureCoord2.zw).rgb;
	vec3 s3 = texture(Sampler0, vTextureCoord3.zw).rgb;
	vec3 s4 = texture(Sampler0, vTextureCoord4.zw).rgb;
	
	float ko1 = dot(abs(o1 - c), dt);
	float ko2 = dot(abs(o2 - c), dt);
	float ko3 = dot(abs(o3 - c), dt);
	float ko4 = dot(abs(o4 - c), dt);
	
	float k1 = min(dot(abs(i1 - i3), dt), max(ko1, ko3));
	float k2 = min(dot(abs(i2 - i4), dt), max(ko2, ko4));
	
	float w1 = min(k2, k2 * ko3/ko1);
	float w2 = min(k1, k1 * ko4/ko2);
	float w3 = min(k2, k2 * ko1/ko3);
	float w4 = min(k1, k1 * ko2/ko4);
	
	c = (w1*o1 + w2*o2 + w3*o3 + w4*o4 + 0.001*c) / (w1 + w2 + w3 + w4 + 0.001);
	w1 = k * dot(abs(i1-c) + abs(i3-c), dt) / (0.125 * dot(i1+i3, dt) + lum_add);
	w2 = k * dot(abs(i2-c) + abs(i4-c), dt) / (0.125 * dot(i2+i4, dt) + lum_add);
	w3 = k * dot(abs(s1-c) + abs(s3-c), dt) / (0.125 * dot(s1+s3, dt) + lum_add);
	w4 = k * dot(abs(s2-c) + abs(s4-c), dt) / (0.125 * dot(s2+s4, dt) + lum_add);
	
	w1 = clamp(w1 + mx, min_w, max_w); 
	w2 = clamp(w2 + mx, min_w, max_w);
	w3 = clamp(w3 + mx, min_w, max_w); 
	w4 = clamp(w4 + mx, min_w, max_w);
	
	vec3 stockrgb = (w1*(i1+i3) + w2*(i2+i4) + w3*(s1+s3) + w4*(s2+s4) + c) / (2.0 * (w1+w2+w3+w4) + 1.0);
	pColor = vec4(hd_grade_rgb(stockrgb, vec2(textureSize(Sampler0, 0))), texture(Sampler0, vTextureCoord0).a);
	
	if (pParam1.w > 0.0)
	{
		// UNGRADED, deliberately. Stock wrote pColor.r here, and pColor was
		// the raw filter output; now pColor is graded, so reading it back
		// would feed a black-point-crushed value into the DEPTH buffer and
		// silently move geometry. The grade is a display transform and must
		// not reach depth.
		gl_FragDepth = stockrgb.r;
	}
}

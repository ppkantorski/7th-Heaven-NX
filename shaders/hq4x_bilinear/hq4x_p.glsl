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

// BUILD 800 hq4x_bilinear -- 7th_heaven_nx. The current hq4x's HD grading
// on a single bilinear read: no edge-directed smoothing at all. 1 texture
// read per output pixel instead of 14, so the 4x pass a texture build runs
// (about two a frame in Wutai) nearly disappears. Softer, plainer
// magnification of the backgrounds than hq4x.
void main()
{
	vec4 t = texture(Sampler0, vTextureCoord0.xy);
	pColor = vec4(hd_grade_rgb(t.rgb, vec2(textureSize(Sampler0, 0))), t.a);
	
	if (pParam1.w > 0.0)
	{
		gl_FragDepth = t.r;		// ungraded, as in hq4x_p
	}
}

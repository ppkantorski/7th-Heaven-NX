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

// See sharp/2xsal_p.glsl for the whole argument. Same clamped point sample,
// same grading. hq4x has no diagonal tap pair to clamp against, so the bound
// is half a texel either side of the centre -- the texel the pixel actually
// lands in, and nothing outside it.
const float HD_BLACK_POINT = 0.03137;
const float HD_SATURATION  = 1.05;

vec3 hd_grade_rgb(vec3 rgb, vec2 ts)
{
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
	float keep = pParam2.x * 0.0;

	vec2 ts = vec2(textureSize(Sampler0, 0));
	vec2 c  = vTextureCoord0.xy;
	vec2 h  = 0.5 / ts;
	vec2 uv = clamp((floor(c * ts) + 0.5) / ts, c - h, c + h);

	vec4 s = texture(Sampler0, uv);
	pColor = vec4(hd_grade_rgb(s.rgb, ts), s.a) + vec4(keep);

	if (pParam1.w > 0.0)
	{
		// UNGRADED, deliberately -- the same rule hd/hq4x_p states: the grade
		// is a display transform and feeding a black-point-crushed value into
		// the DEPTH buffer silently moves geometry.
		gl_FragDepth = s.r;
	}
}

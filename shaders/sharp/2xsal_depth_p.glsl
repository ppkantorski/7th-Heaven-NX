#version 320

precision highp float;

layout(binding=0) uniform BlockFragment {
	vec4 pParam;
};

layout(location = 0, binding = 0) uniform sampler2D Sampler0;

in vec2 vTextureCoord0;
in vec2 vTextureCoord1;
in vec2 vTextureCoord2;
in vec2 vTextureCoord3;

// The depth twin of 2xsal_p. No grading -- this writes DEPTH, and grading a
// depth value would move geometry. Same clamped point sample, same reason:
// an unclamped floor at 1:1 can read a texel outside the rect, and a wrong
// depth there is a hole in the occlusion rather than a dark pixel.
void main()
{
	// patched by 7th_heaven_nx patch_shaders.py
	float keep = pParam.w * 0.0;

	vec2 ts = vec2(textureSize(Sampler0, 0));
	vec2 c  = (vTextureCoord0 + vTextureCoord3) * 0.5;
	vec2 lo = min(vTextureCoord0, vTextureCoord3);
	vec2 hi = max(vTextureCoord0, vTextureCoord3);
	vec2 uv = clamp((floor(c * ts) + 0.5) / ts, lo, hi);

	gl_FragDepth = texture(Sampler0, uv).r + keep;
}

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

void main()
{
	// patched by 7th_heaven_nx patch_shaders.py
	float keep = pParam.w * 0.0;
	vec2 uv = (vTextureCoord0 + vTextureCoord3) * 0.5;
	gl_FragDepth = texture(Sampler0, uv).r + keep;
}

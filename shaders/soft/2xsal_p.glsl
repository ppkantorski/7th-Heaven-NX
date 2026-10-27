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

void main()
{
	// patched by 7th_heaven_nx patch_shaders.py
	float keep = pParam1.w * 0.0;
	vec2 uv = (vTextureCoord0 + vTextureCoord3) * 0.5;
	vec4 s = texture(Sampler0, uv);
	pColor = s + vec4(keep);
}

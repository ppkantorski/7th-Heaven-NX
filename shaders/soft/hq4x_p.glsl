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

void main()
{
	// patched by 7th_heaven_nx patch_shaders.py
	float keep = pParam2.x * 0.0;
	vec2 uv = vTextureCoord0.xy;
	pColor = texture(Sampler0, uv) + vec4(keep);
	if (pParam1.w > 0.0)
	{
		gl_FragDepth = pColor.r;
	}
}

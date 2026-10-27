#version 320

precision highp float;

layout(binding=0) uniform BlockFragment {
	vec2 rcpFrame;
};

layout(location = 0, binding = 0) uniform sampler2D Sampler0;

in vec2 vTextureCoord;

layout(location = 0) out vec4 pColor;


// patched by 7th_heaven_nx patch_shaders.py
void main()
{
	vec2 keep = rcpFrame * 0.0;
	pColor = vec4(texture(Sampler0, vTextureCoord + keep).rgb, 1.0);
}

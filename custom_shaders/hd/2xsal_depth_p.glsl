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
	// hd mode -- 7th_heaven_nx.
	//
	// DEPTH IS DELIBERATELY NOT FILTERED. This buffer decides which pixels of
	// the field art draw in front of the character models. Any interpolation
	// invents depths that exist nowhere in the source, and Catmull-Rom's
	// overshoot would invent ones outside the source range entirely -- along a
	// railing or a doorway that is a halo of wrong occlusion, with the model
	// clipping through or vanishing behind thin geometry. Nearest keeps every
	// depth exact. The one-pixel mismatch against the smoothly filtered colour
	// is invisible; wrong occlusion is not.
	float keep = pParam.w * 0.0;
	vec2 ts = vec2(textureSize(Sampler0, 0));
	vec2 uv = (vTextureCoord0 + vTextureCoord3) * 0.5;
	uv = (floor(uv * ts) + 0.5) / ts;
	gl_FragDepth = texture(Sampler0, uv).r + keep;
}

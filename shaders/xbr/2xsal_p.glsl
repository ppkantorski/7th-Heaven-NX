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
	vec2 ts = vec2(textureSize(Sampler0, 0));
	vec2 uv = (vTextureCoord0 + vTextureCoord3) * 0.5;
	vec2 ip = uv * ts;
	vec2 tc = floor(ip) + 0.5;
	vec2 f  = ip - tc;
	vec2 px = 1.0 / ts;
	vec2 dxy = vec2(f.x < 0.0 ? -1.0 : 1.0, f.y < 0.0 ? -1.0 : 1.0);
	vec2 base = tc / ts;
	vec4 E = texture(Sampler0, base);
	vec4 H = texture(Sampler0, base + vec2(dxy.x, 0.0) * px);
	vec4 V = texture(Sampler0, base + vec2(0.0, dxy.y) * px);
	vec4 A = texture(Sampler0, base + dxy * px);
	vec3 lw = vec3(0.299, 0.587, 0.114);
	float dHV = dot(abs(H.rgb - V.rgb), lw);
	float dEH = dot(abs(E.rgb - H.rgb), lw);
	float dEV = dot(abs(E.rgb - V.rgb), lw);
	float dEA = dot(abs(E.rgb - A.rgb), lw);
	float edge = step(dHV, 0.12) * step(0.25, min(dEH, dEV))
	           * step(dHV, dEA + 0.05);
	float cov = clamp((abs(f.x) + abs(f.y) - 0.5) * 2.0, 0.0, 1.0);
	vec4 result = mix(E, 0.5 * (H + V), edge * cov);
	pColor = result + vec4(keep);
}

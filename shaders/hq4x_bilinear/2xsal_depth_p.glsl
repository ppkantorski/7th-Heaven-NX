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
	vec3 ide = pParam.xyz;
	float eps = pParam.w;
	
	vec3 a = texture(Sampler0, vTextureCoord1).rgb;
	vec3 d = texture(Sampler0, vTextureCoord3).rgb;
	float av1 = dot(abs(a - d), ide) + eps;
	
   	vec3 b = texture(Sampler0, vTextureCoord0).rgb;
	vec3 c = texture(Sampler0, vTextureCoord3).rgb;
	float av2 = dot(abs(c - b), ide) + eps;
	
	gl_FragDepth = vec3((av1*(c.rgb + b.rgb) + av2*(d.rgb + a.rgb)) / (2.0*(av1+av2))).r;
}

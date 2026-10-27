// 16:9 widescreen framing -- 7th_heaven_nx
//
// Reproduces FFNx's widescreen scale in the shader instead of in the module.
// FFNx, renderer.cpp:2435:
//     widescreenScale = round(game_width / wide_viewport_width * 100)/100
//                     = round(640/854*100)/100 = 0.75
// FFNx applies it to d3dProjection[0] and [8]; for a projection whose _21
// and _41 are zero -- true of FF7's -- that is the same as scaling clip.x.
//
// With gfx_drv_init's four words making the render target 16:9, this puts
// the 4:3 picture back at its ORIGINAL pixel size, centred, with black at
// the sides. The visible game-x range becomes -106.7 .. 746.7, which is
// FFNx's ortho(-107, 747) to within FFNx's own rounding.
//
// Set WS_SCALE to 1.0 to disable without deleting the file.
#version 320

precision highp float;

#define WS_SCALE 0.75

layout(binding=0) uniform BlockVertex {
	ivec4 blendMode;
	layout(column_major) mat4 projectionMatrix;
};

layout(location = 0) in vec4 VertexCoord;
layout(location = 1) in vec2 TextureCoord;
layout(location = 2) in vec4 Color;

out vec4 vColor;
out vec2 vTextureCoord;

void main()
{
	gl_Position = projectionMatrix * vec4(VertexCoord.xyz, 1.0);
	gl_Position.x *= WS_SCALE;
	
	vColor = Color.bgra;
	if (blendMode.x == 4) vColor.a = 1.0;
	else if (blendMode.x == 3) vColor.a = 0.25;
	else if (Color.a > 0.5) vColor.a = 0.5;
	
	vTextureCoord = TextureCoord;
}

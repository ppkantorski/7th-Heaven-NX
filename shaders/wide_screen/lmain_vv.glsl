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

// World-map depth (ff7nx_worldsphere / ff7nx_worldfar) -- BUILD 591.
//
// The module sets blendMode.w to WS_SPHERE_MAGIC only for draws made while
// the driver is in WORLD-MAP mode, so a field, battle or menu draw can never
// take this path (and without the module this is identity).
//
// The planet is dropped on the CPU (the world-fixed d^2 / 2R); here only the
// DEPTH is set. Every world draw -- the models here, and in tlmain_vv the
// terrain, the far field, the shadows and the effects -- gets its depth from
// its true view distance Z by the same formula:
//     ndc z = WS_DEPTH_A * (1 - WS_DEPTH_C / Z)
// a projection with its far plane at infinity: nothing is ever far-clipped,
// and because models and ground use one formula they can never disagree
// about which is in front. (590 squeezed depth past half the far plane on
// the terrain vertices only; the ground between them and the shadows kept
// another scale, which hid parked vehicles and shadows when zoomed out.)
// In clip space it is linear: z = A * (w - C). WS_DEPTH_A < 1 keeps all of it
// in front of the sky backdrop the game draws at depth 0.999.
#define WS_SPHERE_MAGIC 0x575253
#define WS_DEPTH_A 0.99
#define WS_DEPTH_C 32.0

vec4 wsDepth(vec4 clip)
{
	if (clip.w > 2.0)
		clip.z = WS_DEPTH_A * (clip.w - WS_DEPTH_C);
	return clip;
}

void main()
{
	gl_Position = projectionMatrix * vec4(VertexCoord.xyz, 1.0);
	if (blendMode.w == WS_SPHERE_MAGIC)
		gl_Position = wsDepth(gl_Position);
	gl_Position.x *= WS_SCALE;
	
	vColor = Color.bgra;
	if (blendMode.x == 4) vColor.a = 1.0;
	else if (blendMode.x == 3) vColor.a = 0.25;
	else if (Color.a > 0.5) vColor.a = 0.5;
	

	// Day/Night (ff7nx_daynight). The module parks a packed 0xRRGGBB tint in
	// blendMode.y and a 24-bit magic in blendMode.z -- two of the three ivec4
	// lanes this shader has never read, uploaded every draw already.
	//
	// The magic is what makes each half safe on its own. Without the module
	// the lane holds whatever the port left there, the compare fails, and this
	// is identity; with the module but no tint it writes white. So a card can
	// carry this shader and an older `main`, or the reverse, and look exactly
	// as it does today.
	if (blendMode.z == 0x44594E) {
		int dnTint = blendMode.y;
		vColor.rgb *= vec3((dnTint >> 16) & 255, (dnTint >> 8) & 255,
		                   dnTint & 255) * (1.0 / 255.0);
	}

	vTextureCoord = TextureCoord;
}

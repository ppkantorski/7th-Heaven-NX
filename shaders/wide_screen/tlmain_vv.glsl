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

// World-map depth -- BUILD 591, the same formula as lmain_vv's wsDepth: for
// draws in world-map mode (blendMode.w, set by the module) with a real depth
// (rhw != 1; before 721, rhw < 0.5), ndc z = WS_DEPTH_A * (1 - WS_DEPTH_C / Z), Z = 1 / rhw. The
// z the game wrote is not used, so terrain past the game's far plane is not
// clipped. 2D drawing (rhw = 1: the menus, the minimap, the sky backdrop)
// keeps its own depth.
#define WS_SPHERE_MAGIC 0x575253
#define WS_DEPTH_A 0.99
#define WS_DEPTH_C 32.0
// BUILD 721: the near plane covers every 3D vertex, not only rhw < 0.5. A
// vertex closer than 2 units (rhw >= 0.5) kept the z the game wrote, so a
// triangle reaching from it to the ground was never near-clipped: it spread
// over the screen as one flat colour (the grey wedge and grey frames by the
// Northern Crater). rhw == 1 is still 2D. A vertex with no real rhw, or one
// thrown far off screen while near, drops its triangle.
#define WS_FAR_OFF 16384.0
#define WS_FAR_RHW 0.001

void main()
{
	vec4 position = VertexCoord;
	position.w = 1.0 / position.w;
	position.xyz *= position.w;
	gl_Position = projectionMatrix * position;
	if (blendMode.w == WS_SPHERE_MAGIC) {
		float rhw = VertexCoord.w;
		vec2 off = abs(VertexCoord.xy - vec2(320.0, 240.0));
		if (!(rhw > 0.0) || max(off.x, off.y) > WS_FAR_OFF && rhw >= WS_FAR_RHW) {
			// BUILD 721: no world vertex lands this far off screen this close to the
			// eye. Its triangle is cut back to the other two corners by the near
			// plane (z far behind it), so it draws as nothing.
			gl_Position = vec4(0.0, 0.0, -1.0e20, 1.0);
		} else if (rhw != 1.0) {
			gl_Position.z = gl_Position.w * WS_DEPTH_A * (1.0 - WS_DEPTH_C * VertexCoord.w);
		}
	}
	gl_Position.x *= WS_SCALE;
	
	vColor = Color.bgra;
	if (blendMode.x == 4) vColor.a = 1.0;
	else if (blendMode.x == 3) vColor.a = 0.25;
	

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

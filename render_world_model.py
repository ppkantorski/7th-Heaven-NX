"""
Offline rasteriser for a world_us.lgp model, used to VERIFY what a change to
the `.p` files will put on screen before a build is spent on it.

It reproduces the port's 3D pixel path and nothing else:

    location 2 `Color` is 4 normalised bytes at nvertex+0x10, stride 0x20
    (`main+0x10DA44C`), `lmain_vv.glsl` does `vColor = Color.bgra`, and
    `colortex_p.glsl` does `texture(Sampler0, uv) * vColor`.

so a PRE-LIT (vertextype 1) part is exactly `texel * vertexcolordata[v]`,
Gouraud-interpolated, and that is what this draws.

    python3 render_world_model.py <archive.lgp> <skin.tex> <out.png> [part.p ...]

With no parts named it draws the Highwind (cha, cha.1, che, cib) in the pose
the world map shows, and prints each part's vertex type as it goes.
"""
import os
import struct
import sys

import numpy as np
from PIL import Image

import lgp
import tex

HDR = ['version', 'field_4', 'vertextype', 'numverts', 'numnormals',
       'field_14', 'numtexcoords', 'numvertcolors', 'numedges', 'numpolys',
       'field_28', 'field_2C', 'numhundreds', 'numgroups',
       'numboundingboxes', 'has_normindextable']


def parse_p(data):
    """Layout is FF7's own; see FFNx src/ff7/loaders.cpp load_p_file."""
    h = dict(zip(HDR, struct.unpack_from('<16I', data, 0)))
    if h['version'] != 1:
        raise ValueError('not a version 1 .p file')
    off = 128

    def take(n, sz):
        nonlocal off
        blob = data[off:off + n * sz]
        off += n * sz
        return blob

    take(h['field_14'], 12)
    verts = np.frombuffer(take(h['numverts'], 12),
                          '<f4').reshape(-1, 3).astype(np.float64)
    take(h['numnormals'], 12)
    uvs = (np.frombuffer(take(h['numtexcoords'], 8), '<f4')
           .reshape(-1, 2).astype(np.float64)
           if h['numtexcoords'] else np.zeros((h['numverts'], 2)))
    vcol = (np.frombuffer(take(h['numvertcolors'], 4), '<u4').copy()
            if h['numvertcolors'] else
            np.full(h['numverts'], 0x80FFFFFF, '<u4'))
    take(h['numpolys'], 4)
    take(h['numedges'], 4)
    polys = np.frombuffer(take(h['numpolys'], 24), '<u2').reshape(-1, 12)
    return h, verts, uvs, vcol, polys[:, 1:4].astype(np.int32)


def decode_tex(data):
    t = tex.parse(data)
    w, hgt, bypp = t['width'], t['height'], t['bytes_per_pixel']
    px = bytes(t['pixels'])
    if t['palette_flag']:
        # `tex.parse` hands back the palette as raw BGRA quads.
        pal = np.frombuffer(bytes(t['palette']), np.uint8).reshape(-1, 4)
        idx = np.frombuffer(px[:w * hgt], np.uint8).astype(np.int32)
        idx = np.clip(idx, 0, len(pal) - 1)
        rgb = pal[idx][:, 2::-1]                    # BGRA -> RGB
    elif bypp == 3:
        a = np.frombuffer(px[:w * hgt * 3], np.uint8).reshape(hgt, w, 3)
        rgb = a[:, :, ::-1].reshape(-1, 3)          # BGR -> RGB
    else:
        a = np.frombuffer(px[:w * hgt * 4], np.uint8).reshape(hgt, w, 4)
        rgb = a[:, :, 2::-1].reshape(-1, 3)
    return rgb.reshape(hgt, w, 3).astype(np.float64)


def render(verts, uvs, vcol, tris, texture, size=(760, 300), bg=(24, 40, 20)):
    """Orthographic side view, z-buffered, Gouraud texel*colour."""
    W, H = size
    lo, hi = verts.min(0), verts.max(0)
    span = (hi - lo).max()
    scale = min(W, H) * 0.86 / span
    cx, cy = (lo + hi)[0] / 2, (lo + hi)[1] / 2
    sx = (verts[:, 0] - cx) * scale + W / 2
    sy = H / 2 - (verts[:, 1] - cy) * scale             # FF7 +Y is up
    sz = verts[:, 2]

    th, tw = texture.shape[:2]
    tu = np.clip((uvs[:, 0] * tw).astype(np.int32), 0, tw - 1)
    tv = np.clip((uvs[:, 1] * th).astype(np.int32), 0, th - 1)
    texel = texture[tv, tu]                             # per vertex
    mod = np.stack([(vcol >> 16) & 0xFF, (vcol >> 8) & 0xFF,
                    vcol & 0xFF], -1) / 255.0
    vrgb = np.clip(texel * mod, 0, 255)

    img = np.zeros((H, W, 3), np.float64)
    img[:] = bg
    zbuf = np.full((H, W), -1e30)
    for a, b, c in tris:
        xs = np.array([sx[a], sx[b], sx[c]])
        ys = np.array([sy[a], sy[b], sy[c]])
        x0, x1 = int(np.floor(xs.min())), int(np.ceil(xs.max()))
        y0, y1 = int(np.floor(ys.min())), int(np.ceil(ys.max()))
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, W - 1), min(y1, H - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        d = ((ys[1] - ys[2]) * (xs[0] - xs[2]) +
             (xs[2] - xs[1]) * (ys[0] - ys[2]))
        if abs(d) < 1e-12:
            continue
        l0 = ((ys[1] - ys[2]) * (gx - xs[2]) +
              (xs[2] - xs[1]) * (gy - ys[2])) / d
        l1 = ((ys[2] - ys[0]) * (gx - xs[2]) +
              (xs[0] - xs[2]) * (gy - ys[2])) / d
        l2 = 1.0 - l0 - l1
        m = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
        if not m.any():
            continue
        z = l0 * sz[a] + l1 * sz[b] + l2 * sz[c]
        cur = zbuf[y0:y1 + 1, x0:x1 + 1]
        m &= z > cur
        if not m.any():
            continue
        col = (l0[..., None] * vrgb[a] + l1[..., None] * vrgb[b] +
               l2[..., None] * vrgb[c])
        sub = img[y0:y1 + 1, x0:x1 + 1]
        sub[m] = col[m]
        cur[m] = z[m]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


HIGHWIND = ('cha.p', 'cha.1.p', 'che.p', 'cib.p')


def main(argv):
    if len(argv) < 3:
        raise SystemExit(__doc__)
    arch, skin, out = argv[0], argv[1], argv[2]
    parts = argv[3:] or list(HIGHWIND)
    a = lgp.Archive(arch)
    texture = decode_tex(a.index[skin.lower()]['payload'])

    verts = []
    uvs = []
    vcol = []
    tris = []
    base = 0
    for part in parts:
        h, v, u, c, t = parse_p(a.index[part.lower()]['payload'])
        print('%-10s vertextype %d (%s)  %d verts  %d tris  %d vertex colours'
              % (part, h['vertextype'],
                 {0: 'UNLIT', 1: 'pre-lit', 2: '2D'}.get(h['vertextype'], '?'),
                 h['numverts'], len(t), h['numvertcolors']))
        verts.append(v)
        uvs.append(u)
        vcol.append(c)
        tris.append(t + base)
        base += len(v)
    verts = np.vstack(verts)
    uvs = np.vstack(uvs)
    vcol = np.concatenate(vcol)
    tris = np.vstack(tris)

    # Side view in the pose the world map shows: FF7 world model space runs
    # nose-to-tail along +Y, height along Z, beam along X.
    view = np.stack([-verts[:, 1], verts[:, 2], verts[:, 0]], 1)
    render(view, uvs, vcol, tris, texture, size=(900, 430)).save(out)
    print('wrote %s' % out)


if __name__ == '__main__':
    main(sys.argv[1:])

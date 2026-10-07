#!/usr/bin/env python3
"""ff7nx_framesim.py -- render one 16:9 field frame the way the port draws it.

BUILD 618k. A software frame of the BUILT background at a given camera
position and layer-3/4 scroll offset, using the port's shipped rules:

  layers 1/2   screen = (tile.x - cam.x + 213.5, tile.y - cam.y + 120)
  layers 3/4   bg.x = (pos.x/16 + speed.x*cam.x/256 + scroll.x) % W + 160
               bg.y = (pos.y/16 + speed.y*cam.y/256 + scroll.y) % H + 112
               (C truncating %), one +-W/+-H shift (ff7nx_paraudit's
               constants), layer-4 x cull, then
               screen = (tile.x - bg.x + 373.5, tile.y - bg.y + 232)

Pixels nothing draws on are painted MAGENTA so a gap cannot hide in black
art. Draw order: layer 4, layer 3, layer 1, layer 2 (far z first). Overlay
tiles blend by their own mode (0 average, 1 add, 2 subtract, 3 add 1/4);
colour 0 is transparent on layers 2-4. Characters and 3D models are not
drawn. The picture is 427 x 240 field units.
"""
from __future__ import annotations

import struct

import numpy as np

import diag_common as DC
import ff7nx_paraudit as PA
import ff7nx_parallaxfill as PF

UV = 10_000_000
PIC_W, PIC_H = 427, 240
MAGENTA = (255, 0, 255)


def _tmod(v, m):
    """C's truncating remainder."""
    if m == 0:
        return v
    r = abs(v) % m
    return r if v >= 0 else -r


def _palettes(sec3):
    import ff7nx_marginblack as MB     # same reader render_field uses
    cols, _hdr, npg, cpp = MB.palette_colours(sec3)
    v = cols.astype(np.uint32).reshape(npg, cpp)
    rgb = np.stack([((v & 31) << 3), (((v >> 5) & 31) << 3),
                    (((v >> 10) & 31) << 3)], -1).astype(np.float32)
    return rgb, (v == 0)


def _d2(buf):
    v = buf.astype(np.uint32)
    rgb = np.stack([((v >> 11) & 31) << 3, ((v >> 5) & 63) << 2,
                    (v & 31) << 3], -1).astype(np.float32)
    return rgb, (v == 0)


class Field:
    def __init__(self, parts):
        self.parts = parts
        self.sec9 = parts[8]
        self.hdr = PF.trigger_header(parts[7])
        pages, ts, _te, self.px = DC.parse_pages(self.sec9)
        self.pm = {p.slot: p for p in pages if p is not None}
        try:
            self.pal, self.palkey = _palettes(parts[3])
        except Exception:                                      # noqa: BLE001
            import render_field as RF
            self.pal = RF._pal_rgb(parts[3]).astype(np.float32)
            self.palkey = np.zeros(self.pal.shape[:2], bool)
        self.arr = {}
        for s, p in self.pm.items():
            if p.depth == 1:
                self.arr[s] = np.frombuffer(p.data, np.uint8).reshape(256, 256)
            else:
                self.arr[s] = np.frombuffer(p.data, '<u2').reshape(p.px, p.px)
        self.tiles = []
        for layer, offs in DC.walk_layers(self.sec9, self.sec9.find(b'BACK'),
                                          ts):
            for o in offs:
                self.tiles.append((layer, o))
        self._cache = {}

    def _block(self, o, layer, tn):
        s9 = self.sec9
        bl = s9[o + 28]
        fx = s9[o + 34]
        eff = fx if (bl and fx) else s9[o + 32]
        p = self.pm.get(eff)
        if p is None:
            return None
        u, v = struct.unpack_from('<II', s9, o + 42)
        pal = s9[o + 22]
        key = (eff, u, v, pal, tn)
        if key in self._cache:
            return self._cache[key]
        grid = 8 if p.size_flag else 16
        unit = 32 if grid == 8 else 16
        span = max(1, tn // unit)
        cx = int(round(u / UV * grid))
        cy = int(round(v / UV * grid))
        step = (256 if p.depth == 1 else p.px) // grid
        a = self.arr[eff][cy * step:(cy + span) * step,
                          cx * step:(cx + span) * step]
        if a.shape[:2] != (step * span, step * span):
            self._cache[key] = None
            return None
        if p.depth == 1:
            pi = min(pal, self.pal.shape[0] - 1)
            rgb, k = self.pal[pi][a], self.palkey[pi][a]
        else:
            rgb, k = _d2(a)
        self._cache[key] = (rgb, k)
        return rgb, k

    def bg(self, layer, cam, scroll):
        h = self.hdr
        w, hh = h['bg%d_w' % layer], h['bg%d_h' % layer]
        tx = (h['bg%d_pos_x' % layer] / 16.0
              + h['bg%d_speed_x' % layer] * cam[0] / 256.0 + scroll[0])
        ty = (h['bg%d_pos_y' % layer] / 16.0
              + h['bg%d_speed_y' % layer] * cam[1] / 256.0 + scroll[1])
        return (int(_tmod(int(tx), w)) + 320 - PA.OFF_X,
                int(_tmod(int(ty), hh)) + 232 - PA.OFF_Y, w, hh)

    def render(self, cam=(0, 0), scroll3=(0, 0), scroll4=(0, 0), states=None,
               K=2, layers=(1, 2, 3, 4), mark=True, rank=None, bg_color=None):
        """rank: draw order per layer (BGPDH can bring layer 4 in front of
        layer 3, as crater_1's script does)."""
        rank = rank or {4: 0, 3: 1, 1: 2, 2: 3}
        states = states or {}
        W, H = PIC_W * K, PIC_H * K
        can = np.zeros((H, W, 3), np.float32)
        if bg_color is not None:
            can[:] = np.asarray(bg_color, np.float32)
        hit = np.zeros((H, W), bool)
        bgs = {3: self.bg(3, cam, scroll3), 4: self.bg(4, cam, scroll4)}
        order = []
        s9 = self.sec9
        for i, (layer, o) in enumerate(self.tiles):
            if layer not in layers:
                continue
            z = struct.unpack_from('<I', s9, o + 38)[0]
            order.append((rank[layer], -z if layer == 2 else 0, i, layer, o))
        order.sort()
        k = PA.PORT
        for _r, _z, _i, layer, o in order:
            par, st = s9[o + 26], s9[o + 27]
            if par and not (states.get(par, 0) & st):
                continue
            x, y = struct.unpack_from('<hh', s9, o + 2)
            tn = 16 if layer == 1 else (
                max(struct.unpack_from('<HH', s9, o + 18)) or 16)
            if layer in (1, 2):
                sx, sy = x - cam[0] + 213.5, y - cam[1] + 120
            else:
                bx, by, w, hh = bgs[layer]
                if x <= bx - k['L'] or x >= bx + k['R']:
                    x += -w if x >= bx - k['HW'] else w
                if layer == 4 and not (bx - k['L'] < x < bx + k['R']):
                    continue
                if y <= by - k['T'] or y >= by + k['B']:
                    down = (y >= by - k['HH']) if layer == 3 else (
                        y >= by + k['B'])
                    y += -hh if down else hh
                sx, sy = x - bx + 373.5, y - by + 232
            X0, Y0 = int(round(sx * K)), int(round(sy * K))
            n = tn * K
            if X0 >= W or Y0 >= H or X0 + n <= 0 or Y0 + n <= 0:
                continue
            blk = self._block(o, layer, tn)
            if blk is None:
                continue
            rgb, key = blk
            idx = (np.arange(n) * rgb.shape[0] // n)
            rgb = rgb[idx][:, idx]
            key = key[idx][:, idx]
            xa, ya = max(0, X0), max(0, Y0)
            xb, yb = min(W, X0 + n), min(H, Y0 + n)
            rgb = rgb[ya - Y0:yb - Y0, xa - X0:xb - X0]
            key = key[ya - Y0:yb - Y0, xa - X0:xb - X0]
            reg = can[ya:yb, xa:xb]
            m = ~key if layer > 1 else np.ones_like(key)
            if s9[o + 28]:
                mode = s9[o + 30]
                if mode == 1:
                    reg[m] = np.minimum(255, reg[m] + rgb[m])
                elif mode == 2:
                    reg[m] = np.maximum(0, reg[m] - rgb[m])
                elif mode == 3:
                    reg[m] = np.minimum(255, reg[m] + rgb[m] * 0.25)
                else:
                    reg[m] = (reg[m] + rgb[m]) / 2
            else:
                reg[m] = rgb[m]
                hit[ya:yb, xa:xb] |= m
        out = np.clip(can, 0, 255).astype(np.uint8)
        if mark:
            out[~hit] = MAGENTA
        return out

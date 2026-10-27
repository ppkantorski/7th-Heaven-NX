"""Prepare assets/whitein_left_strip.png from an outside painting (ChatGPT).

    python3 ../../ff7nx_wishift.py export <dir>      # from YOUR install
    python3 prep_fill.py <painting.png> <dir>/whitein_reference_full.png \
        whitein_ribbon_mask.png ../whitein_left_strip.png

The painting and the exported reference are inputs on the user's machine
only; neither is shipped (both carry the game's / Cosmos's art). The output
is ONLY the strip (144 x 1536 px, field x -224..-176), and the glass
ribbon's pixels in it are black: ff7nx_wishift keeps the game's own texels
there at build time. (618z8; before that the output was the whole picture.)

1. register the painting to the exported reference (ECC affine, fitted on
   the unpainted part x >= 150 px);
2. keep the reference everywhere except the strip (x < 144 px = field -176);
3. take the painting's own pixels near the glass ribbon's tip out (ChatGPT's
   ribbon edge is a few px off ours) and inpaint them from the painted ocean;
4. add the original's fine grain, taken from the SAME ROWS further right
   (translated, never mirrored) and only from pixels well clear of the
   ribbon and of any strong edge;
5. solve a Poisson blend in the strip: the painting's gradients inside, the
   original layer-1 column x = 144 as the boundary, the ribbon as a free
   (Neumann) boundary. The join is continuous by construction and nothing
   of the original is copied, so nothing can be reflected.

History. 10-05 (§52c) cross-faded the last 40 px into a MIRROR of the
original at x 145..289; the ribbon crosses that band (rows 316..637), so the
strip got a reflected ribbon edge ("a repeat of the bridge", an X shape at
the tip). Mirroring is gone for good; see step 5.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import sys
import numpy as np, cv2
from PIL import Image
import scipy.sparse as sp
import scipy.sparse.linalg as spl

SEAM = 144          # first original column (field x -176)
paint, ref, ribp, out = sys.argv[1:5]
r = np.asarray(Image.open(ref).convert('RGB')).astype(np.float32)
H, W = r.shape[:2]
rib = np.asarray(Image.open(ribp).convert('L')) > 0
a = np.asarray(Image.open(paint).convert('RGB').resize((W, H), Image.LANCZOS)).astype(np.float32)

# 1. registration
ga = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY); gr = cv2.cvtColor(r, cv2.COLOR_RGB2GRAY)
warp = np.eye(2, 3, dtype=np.float32)
cc, warp = cv2.findTransformECC(gr[:, 150:], ga[:, 150:], warp, cv2.MOTION_AFFINE,
                                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 300, 1e-7), None, 5)
T = np.array([[1, 0, -150], [0, 1, 0], [0, 0, 1]], np.float32)
full = np.linalg.inv(T) @ np.vstack([warp, [0, 0, 1]]) @ T
al = cv2.warpAffine(a, full[:2], (W, H), flags=cv2.INTER_CUBIC + cv2.WARP_INVERSE_MAP,
                    borderMode=cv2.BORDER_REPLICATE)
print('ECC', round(float(cc), 4), 'affine', np.round(full[:2], 4).tolist())

# 3. the painting's ribbon edge out, ocean inpainted in
near = cv2.dilate(rib.astype(np.uint8), np.ones((13, 13), np.uint8)) > 0
tgt = al[:, :SEAM + 8].copy()
hole = near[:, :SEAM + 8].astype(np.uint8)
u8 = np.clip(tgt, 0, 255).astype(np.uint8)
est = cv2.inpaint(u8, hole, 6, cv2.INPAINT_TELEA).astype(np.float32)
# only where the painting is clearly brighter than the ocean estimate (its
# own ribbon edge); elsewhere in the ring the painting is kept as painted
hole = hole.astype(bool) & (tgt.max(-1) - est.max(-1) > 20)
hole = cv2.dilate(hole.astype(np.uint8), np.ones((5, 5), np.uint8)) & \
    near[:, :SEAM + 8].astype(np.uint8)
tgt = cv2.inpaint(u8, hole, 6, cv2.INPAINT_TELEA).astype(np.float32)[:, :SEAM]
print('painted-ribbon pixels replaced:', int(hole.sum()))

# 4. grain: high-pass of the original, same rows, translated right
hp = r - cv2.GaussianBlur(r, (0, 0), 1.5)
bad = np.abs(hp).max(-1) > 10
bad = cv2.dilate(bad.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
bad |= cv2.dilate(rib.astype(np.uint8), np.ones((31, 31), np.uint8)) > 0
grain = np.zeros((H, SEAM, 3), np.float32)
done = np.zeros((H, SEAM), bool)
for D in range(SEAM + 8, W - SEAM, 37):
    ok = ~bad[:, D:D + SEAM] & ~done
    grain[ok] = hp[:, D:D + SEAM][ok]
    done |= ok
grain = np.clip(grain, -6, 6)
lum = tgt.max(-1)
grain *= np.clip((lum - 8) / 16.0, 0, 1)[..., None]   # no grain on black
tgt = tgt + grain

# 5. Poisson blend in the strip:  sum_q (v_p - v_q) = sum_q (t_p - t_q)
#    q in the strip and not ribbon: unknown;  q = column SEAM and not ribbon:
#    v_q = original (Dirichlet), t_q = the registered painting there;
#    q off the image or on the ribbon: no equation term (Neumann).
free = ~rib[:, :SEAM]
idx = -np.ones((H, SEAM), np.int64)
idx[free] = np.arange(free.sum())
n = int(free.sum())
ys, xs = np.nonzero(free)
me = idx[ys, xs]
rows, cols, vals = [], [], []
b = np.zeros((n, 3), np.float64)
diag = np.zeros(n)
for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
    ny, nx = ys + dy, xs + dx
    ins = (ny >= 0) & (ny < H) & (nx >= 0)
    p_y, p_x, q_y, q_x, m_ = ys[ins], xs[ins], ny[ins], nx[ins], me[ins]
    tp = tgt[p_y, p_x]
    st = (q_x < SEAM)
    st &= free[q_y, np.minimum(q_x, SEAM - 1)]
    se = (q_x == SEAM) & ~rib[q_y, SEAM]
    # near-black on both sides (the opaque-black cells under the ocean):
    # no tie, or the 8/255 'opaque black' would bleed in as a grey blob
    se &= ~((r[q_y, SEAM].max(-1) <= 24) & (al[q_y, SEAM].max(-1) <= 24))
    np.add.at(diag, m_[st | se], 1)
    np.add.at(b, m_[st], tp[st] - tgt[q_y[st], q_x[st]])
    rows.append(m_[st]); cols.append(idx[q_y[st], q_x[st]]); vals.append(-np.ones(st.sum()))
    np.add.at(b, m_[se], tp[se] - al[q_y[se], SEAM] + r[q_y[se], SEAM])
rows.append(np.arange(n)); cols.append(np.arange(n)); vals.append(diag)
A = sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))
# rows with no Dirichlet anywhere in their component would be singular; a
# tiny tie to the guidance keeps them at the painting
eps = 1e-4
A = A + eps * sp.identity(n)
b = b + eps * tgt[ys, xs]
sol = np.zeros((n, 3))
for c in range(3):
    sol[:, c] = spl.spsolve(A.tocsc(), b[:, c])
strip = tgt.copy()
strip[ys, xs] = sol
strip[~free] = r[:, :SEAM][~free]
strip[strip.max(-1) < 6] = 0                      # black stays the clear colour
res = r.copy()
res[:, :SEAM] = np.clip(strip, 0, 255)
g = lambda x: float(np.abs(np.diff(x, axis=1)).mean())
print('grain: painted', round(g(al[:600, :140]), 2), 'result', round(g(res[:600, :140]), 2),
      'original', round(g(r[:600, 150:290]), 2))
j = np.abs(res[:, SEAM - 1] - res[:, SEAM]).mean(-1)[~rib[:, SEAM]]
print('join step |x143-x144| mean', round(float(j.mean()), 2), 'max', round(float(j.max()), 1),
      '(original x144-x145:', round(float(np.abs(r[:, SEAM] - r[:, SEAM + 1]).mean()), 2), ')')
own = np.clip(strip, 0, 255).astype(np.uint8)
own[~free] = 0                  # the ribbon: the game's texels, not shipped
Image.fromarray(own).save(out)

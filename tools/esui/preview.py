"""tools/esui/preview.py

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import diffemu as D, align as A, struct, sys
from PIL import Image, ImageDraw, ImageFont
FN=0x6ca346
D.NATIVE_ARM={0x6c62a2,0x6c98a6,0x6ca1ef,0x6ca23f,0x6ca2a9,0x6c9808}
PRIM={0x6e7ba1:'box',0x6f5b03:'str',0x6f5c0c:'chr',0x6f9739:'num',0x6f9c44:'num',0x6f7346:'bar',0x6f7bfb:'barf',0x6e6c5b:'ava',0x6eb3b8:'cur',0x6e7d20:'boxend'}
def run(module, exebuf, seed=0, pokes=None):
    D.RETMODE[0]=seed; D.POKES=pokes or {}
    c,w,s=D.run_arm({},exebuf,FN,seed,[0,0,0,0],module=module)
    global LASTW
    LASTW=w
    return c,s
LASTW={}
def fftext(exebuf, va):
    o=A.off(va); out=''
    if o is None: return '?'
    seq=[LASTW.get(va+k, exebuf[o+k]) for k in range(40)]
    for b in seq:
        if b==0xff: break
        out+=chr(b+0x20) if b<0x5f else '·'
    return out
def s32(v): return v-(1<<32) if v&0x80000000 else v
def events(c, exebuf):
    ev=[]
    for t,a in c:
        k=PRIM.get(t)
        a=[s32(x) for x in a]
        if k=='box': ev.append(('box',a[1],a[2],a[3],a[4]))
        elif k=='str': ev.append(('text',a[0],a[1],fftext(exebuf,a[2]&0xffffffff),a[3]))
        elif k=='chr': ev.append(('text',a[0],a[1],chr((a[2]&0xff)+0x20) if (a[2]&0xff)<0x5f else {0xd5:':',0xd4:'/'}.get(a[2]&0xff,'#'),a[3]))
        elif k=='num': ev.append(('num',a[0],a[1],a[2],a[3],a[4]))
        elif k=='barf': ev.append(('bar',a[0],a[1],a[2],a[3]))
        elif k=='cur': ev.append(('cur',a[0],a[1]))
        elif k=='ava': ev.append(('ava',a[0],a[1]))
    return ev
if __name__=='__old__':
    import exe_patch
    H='tools/esui/x/EnhancedStock/hext/'
    new=bytearray(A.exe)
    for p in [H+'00-Main.txt']:
        for va,b in exe_patch.parse_hext(open(p,encoding='utf-8',errors='replace').read()):
            o=A.off(va)
            if o is not None and not (0x401000<=va<0x7b6000): new[o:o+len(b)]=b
    for name,mod,eb in (('before','tools/esui/main.built',A.exe),('after','tools/esui/main.cal_esui',bytes(new))):
        c,s=run(mod,eb,pokes={0xdc112c:10})
        ev=events(c,eb)
        print(name,s,len(c))
        for e in ev: print('  ',e)


POKES = {0xdc112c: 10, 0xdc1294: 1, 0xdc08f8: 0xffff, 0xdc1298: 0}


def render(ev, scale_font, title, W=640, H=480, S=2):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new('RGB', (W * S, H * S + 40), (12, 12, 16))
    d = ImageDraw.Draw(im)
    fpath = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    f = ImageFont.truetype(fpath, int(9 * scale_font * S))
    ft = ImageFont.truetype(fpath, 26)
    d.text((10, H * S + 6), title, font=ft, fill=(230, 230, 230))
    d.rectangle([0, 0, W * S - 1, H * S - 1], outline=(80, 80, 80))
    for e in reversed(ev):
        if e[0] == 'box':
            _, x, y, w, h = e
            d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], radius=6,
                                fill=(18, 30, 92), outline=(200, 200, 220), width=2)
    for e in ev:
        k = e[0]
        if k == 'text':
            _, x, y, t, c = e
            t = t.strip() or ('Date' if x < 800 and False else t)
            if t in ('?', ''):
                t = 'NAME'
            d.text((x * S, y * S), t, font=f, fill=(235, 235, 235))
        elif k == 'num':
            _, x, y, v, n, c = e
            d.text((x * S, y * S), '8' * max(1, min(n, 10)), font=f, fill=(180, 220, 255))
        elif k == 'bar':
            _, x, y, w, h = e
            if w > 0 and h > 0:
                d.rectangle([x * S, y * S, (x + w) * S, (y + h) * S], fill=(60, 140, 220))
        elif k == 'ava':
            _, x, y = e
            d.rectangle([x * S, y * S, (x + 56) * S, (y + 60) * S], outline=(255, 200, 90), width=2)
        elif k == 'cur':
            _, x, y = e
            d.polygon([(x * S, y * S), (x * S + 20, y * S + 10), (x * S, y * S + 20)], fill=(255, 255, 255))
    return im


if __name__ == '__main__':
    import exe_patch
    from PIL import Image
    H = 'tools/esui/x/EnhancedStock/hext/'
    new = bytearray(A.exe)
    for va, b in exe_patch.parse_hext(open(H + '00-Main.txt', encoding='utf-8', errors='replace').read()):
        o = A.off(va)
        if o is not None and not (0x401000 <= va < 0x7b6000):
            new[o:o + len(b)] = b
    ims = []
    for name, mod, eb, fs, title in (
            ('before', 'tools/esui/main.built', A.exe, 1.667, 'NOW (your 620k5c main): stock layout + Echo-S calendar'),
            ('after', 'tools/esui/main.cal_esui', bytes(new), 2.0, 'BUILD 621: ESUI main menu + calendar in ESUI\'s layout')):
        c, s = run(mod, eb, pokes=POKES)
        ev = events(c, eb)
        print(name, s, len(c), sum(1 for e in ev if e[0] == 'text'))
        ims.append(render(ev, fs, title))
    W = Image.new('RGB', (ims[0].width * 2 + 20, ims[0].height), (0, 0, 0))
    W.paste(ims[0], (0, 0)); W.paste(ims[1], (ims[0].width + 20, 0))
    W.save('/mnt/user-data/outputs/PREVIEW-621-esui-main-menu.png')

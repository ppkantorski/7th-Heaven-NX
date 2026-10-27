"""tools/esui/preview2.py

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
import numpy as np, struct, exe_patch, lgp, tex, glob, json
from PIL import Image, ImageDraw, ImageFont
import render_x86 as R, align as A, preview as P, diffemu as D
H='tools/esui/x/'
def apply(files):
    new=bytearray(A.exe)
    for p in files:
        for va,b in exe_patch.parse_hext(open(p,encoding='utf-8',errors='replace').read()):
            o=A.off(va)
            if o is not None: new[o:o+len(b)]=b
    return bytes(new)
CAL='tools/esui/calendar.txt'
NUMPAL={}
stock_exe=apply([CAL])
esui_exe0=apply([H+'EnhancedStock/hext/00-Main.txt',H+'EnhancedStock/hext/00-Spacing.txt',CAL,H+'ECHOS/MAIN/hext/05 - ESUI Theme.txt',H+'Fullmenu/hext/all.txt'])
_e=bytearray(esui_exe0)
for _va in (0x6f9860,0x6f9a9b): _e[A.off(_va)]=0x0b   # 03-Battle's two words in the shared number draw
esui_exe=bytes(_e)
def enc(s): return bytes(ord(c)-0x20 for c in s)+b'\xff'
names={}
party=[0,2,1]; nm={0:'Cloud',1:'Barret',2:'Tifa'}
pk=dict(P.POKES); pk[0xdc08f8]=0xffff
pk[0xdc0230]=party[0]|(party[1]<<8)|(party[2]<<16)|(0xff<<24)
pk[0xdc08b8]=12*3600*30   # play timer (for stock box)
pk[0xdc08dc+0xc]=30|(5<<8)|(12<<16)|(15<<24)  # min 30, month 5 (Jun), day 12, hour 15
for cid,n in nm.items():
    base=0xdbfd8c+cid*0x84
    names[base+0x10]=enc(n)
    pk[base]=cid|(25<<8)
    pk[base+0x2c]=1234|(1500<<16)
    pk[base+0x30]=156|(200<<16)
    pk[base+0x38]=1500|(200<<16)
names[0xdc08d8]=b''
names[0x913fb1]=enc('Jun')
names[0x915000]=enc('Date')
arc=lgp.Archive('tools/esui/menu_us.lgp')
idx={n.split('/')[-1].split('.')[0]+'.tex':n for n in glob.glob('tools/esui/texcache/*.tex')}
def sheet(name, esui):
    d=open(idx[name],'rb').read() if esui and name in idx else arc.index[name]['payload']
    t=tex.parse(d); w,h=t['width'],t['height']; cp=t['colors_per_palette']
    pal=np.frombuffer(t['palette'],np.uint8).reshape(-1,cp,4)
    ix=np.minimum(np.frombuffer(t['pixels'],np.uint8).reshape(h,w),cp-1)
    ck=struct.unpack_from('<I',d,8)[0]==1
    return pal,ix,ck
def rgba(sh,p):
    pal,ix,ck=sh; p=min(p,len(pal)-1)
    a=pal[p][ix][:,:,[2,1,0,3]].copy()
    if ck: a[ix==0,3]=0
    return a
def render(exebuf,widths,esui,title,S=2):
    D.RETMODE[0]=0
    ev,st=R.run(exebuf,widths,pk,names=names)
    W,Hh=680,480
    im=Image.new('RGBA',(W*S,Hh*S+44),(6,6,10,255)); d=ImageDraw.Draw(im)
    boxes=[e for e in ev if e[0]=='box']
    for k,a in reversed(boxes):
        x,y,w,h=a[1:5]
        top=np.array([28,44,128]) if esui else np.array([40,56,150]); bot=np.array([8,12,48]) if esui else np.array([10,16,70])
        for yy in range(h*S):
            c=tuple((top+(bot-top)*yy/max(1,h*S-1)).astype(int))+(255,)
            d.line([(x*S,y*S+yy),((x+w)*S,y*S+yy)],fill=c)
        d.rectangle([x*S,y*S,(x+w)*S,(y+h)*S],outline=(190,190,210,255),width=1 if esui else 3)
    fa=sheet('usfont_a_l.tex' if esui else 'usfont_a_h.tex',esui); fb=sheet('usfont_b_l.tex' if esui else 'usfont_b_h.tex',esui)
    cache={}
    wb=sheet('btl_win_b_l.tex' if esui else 'btl_win_b_h.tex',esui)
    wd=sheet('btl_win_d_l.tex' if esui else 'btl_win_d_h.tex',esui)
    av=[e for e in ev if e[0]=='ava']
    for i,(k,a) in enumerate(av):
        n={0:'cloud',1:'barre',2:'tifa'}[party[i]]+'.tex'
        sh=sheet(n,esui); img=Image.fromarray(rgba(sh,0)).resize((84*S,96*S),Image.LANCZOS) if False else Image.fromarray(rgba(sh,0)).crop((0,0,128,128)).resize((84*S,84*S),Image.LANCZOS)
        im.alpha_composite(img,(max(0,a[0]*S),max(0,a[1]*S)))
    for e in ev:
        if e[0]=='bar':
            x,y,w,h=e[1][:4]
            if w>0 and h>0: d.rectangle([x*S,y*S,(x+w)*S,(y+h)*S],fill=(70,150,230,255))
    for e in ev:
        if e[0]!='quad': continue
        _,which,vs,g=e
        colour=g[2]&0xff if len(g)>2 else 7
        p=min(15,(colour&7)*2)
        if which in ('winb','wind'):
            sh=wb if which=='winb' else wd; p=min(15,colour&0xff)
        else:
            sh=fa if which=='a' else fb
        key=(which,p)
        if key not in cache: cache[key]=rgba(sh,p)
        src=cache[key]; th,tw=src.shape[:2]
        (x0,y0,u0,v0),(_,y1,_,v1),(x1,_,u1,_),_=vs
        cu0,cu1=int(round(u0*tw)),int(round(u1*tw)); cv0,cv1=int(round(v0*th)),int(round(v1*th))
        if cu1<=cu0 or cv1<=cv0: continue
        g_=Image.fromarray(src[cv0:cv1,cu0:cu1]).resize((max(1,int((x1-x0)*S)),max(1,int((y1-y0)*S))),Image.LANCZOS)
        im.alpha_composite(g_,(int(x0*S),int(y0*S)))
    d.line([(640*S,0),(640*S,Hh*S)],fill=(255,80,80,255),width=2)
    ft=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',24)
    d.text((10,Hh*S+8),title,font=ft,fill=(230,230,230,255))
    print(title, st, len(ev))
    return im
if __name__=="__main__":
  a=render(stock_exe,open('tools/esui/w_stock.bin','rb').read(),False,'NOW (stock menu + Echo-S calendar)')
  b=render(esui_exe,open('tools/esui/w_esui.bin','rb').read(),True,'BUILD 621: ESUI main menu, your options (fonts, widths, sheets)')
  Wim=Image.new('RGBA',(a.width*2+16,a.height),(0,0,0,255)); Wim.paste(a,(0,0)); Wim.paste(b,(a.width+16,0))
  Wim.convert('RGB').save('/mnt/user-data/outputs/PREVIEW-621-esui-main-menu.png')
  b.convert('RGB').save('/mnt/user-data/outputs/PREVIEW-621-esui-main-menu-after.png')

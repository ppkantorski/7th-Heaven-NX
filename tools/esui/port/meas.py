"""tools/esui/port/meas.py

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
import sys
from PIL import Image; import numpy as np
im=np.array(Image.open(sys.argv[1]).convert('RGB')).astype(int)
def ink(x0,x1,y0,y1,mode='both'):
    c=im[y0:y1,x0:x1]
    bright=(c.min(2)>165) | (c[:,:,1]>150)
    dark=(c.max(2)<45)
    m=bright|dark if mode=='both' else bright
    rows=np.where(m.any(1))[0]; cols=np.where(m.any(0))[0]
    return (y0+rows.min(), y0+rows.max(), x0+cols.min(), x0+cols.max()) if len(rows) else None
for a in sys.argv[2:]:
    nm,box=a.split('=');mode='both'
    if nm.endswith('!'): nm=nm[:-1]; mode='bright'
    x0,x1,y0,y1=map(int,box.split(','))
    print(nm, ink(x0,x1,y0,y1,mode))

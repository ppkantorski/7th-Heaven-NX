"""tools/esui/gen.py

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
import sys, io, contextlib, align as A
def edits_for(fn, prefixes='00-,01-', accept=('OK','GROUP','REGSWAP')):
    A.EDITS.clear(); A.GROUPS.clear(); A.STRUCTS.clear()
    sys.argv=['align', hex(fn), prefixes]
    import os; os.environ['BLOCKS']='1'
    f=io.StringIO()
    with contextlib.redirect_stdout(f):
        A.main(); A.resolve_groups()
    out={}
    for st,xva,a,w0,w1,o,n in A.EDITS:
        if w0 is None: continue
        if st.split('+')[0] in accept or st.startswith('DEFSHARED') and os.environ.get('DEFOK'):
            out[a]=(w0,w1,xva,o,n,st)
    return out, f.getvalue()


import struct, capstone, keystone
_ca = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
_ks = keystone.Ks(keystone.KS_ARCH_ARM64, keystone.KS_MODE_LITTLE_ENDIAN)


def manual_for(fn):
    from manual import MANUAL
    out = {}
    for a, old, new, note in MANUAL.get(fn, []):
        w0 = struct.unpack_from('<I', A.M.text, a)[0]
        i = next(_ca.disasm(A.M.text[a:a + 4], a))
        txt = i.mnemonic + ' ' + i.op_str
        if txt != old:
            raise SystemExit('manual %x: found %r expected %r' % (a, txt, old))
        w1 = struct.unpack('<I', bytes(_ks.asm(new, a)[0]))[0]
        out[a] = (w0, w1, None, old, new, 'MANUAL ' + note)
    return out


def all_for(fn, prefixes='00-,01-'):
    e, log = edits_for(fn, prefixes)
    m = manual_for(fn)
    for a in m:
        if a in e:
            print('note: manual overrides auto at %x' % a)
    e.update(m)
    from manual import RAW
    for a, old, new, note in RAW.get(fn, []):
        cur = struct.unpack_from('<I', A.M.img, a)[0]
        if cur != old:
            raise SystemExit('raw %x: %#x != %#x' % (a, cur, old))
        e[a] = (old, new, None, '.word %#x' % old, '.word %#x' % new, 'RAW ' + note)
    return e, log


def changed_x86(fn, prefixes='00-,01-'):
    """addresses (in the NEW x86) of instructions ESUI changed."""
    import difflib
    new = A.build_new(prefixes.split(','))
    fs, fe = A.M.containing(fn)
    xo = A.x86_tokens(fs, fe, A.exe); xn = A.x86_tokens(fs, fe, new)
    f = lambda i: i.mnemonic + ' ' + i.op_str
    sm = difflib.SequenceMatcher(None, [f(i) for i, _ in xo], [f(i) for i, _ in xn], autojunk=False)
    out = []
    for tg, i1, i2, j1, j2 in sm.get_opcodes():
        if tg != 'equal':
            out += [xn[k][0].address for k in range(j1, j2)]
    return out

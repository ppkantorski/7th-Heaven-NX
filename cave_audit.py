"""Walk every cave this tree installs and prove no two modules share a word."""
import os, sys as _s; _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)) if False else "/sessions/sleepy-tender-sagan/mnt/outputs/7th_heaven_nx")
import sys, struct, collections
import nso_tool

t = nso_tool.parse_nso(sys.argv[1])['segments']['.text']['data']
W = lambda va: struct.unpack_from('<I', t, va)[0]

def walk(hook, name):
    """Every word of the chained cave hooked at `hook`, including the hook."""
    w = W(hook)
    if (w & 0xFC000000) != 0x14000000:
        return None                      # not hooked
    imm = w & 0x03FFFFFF
    if imm & (1 << 25): imm -= 1 << 26
    va = hook + imm * 4
    seen = [hook]
    for _ in range(80):
        x = W(va)
        seen.append(va)
        if (x & 0xFC000000) == 0x14000000:
            j = x & 0x03FFFFFF
            if j & (1 << 25): j -= 1 << 26
            tgt = va + j * 4
            if tgt == hook + 4:
                return seen
            va = tgt
            continue
        va += 4
    raise RuntimeError(f'{name}: chain from +{hook:#x} did not terminate')

HOOKS = [
    ('letterbox  viewport y',      0x9298EC),
    ('letterbox  [0xCFF208]',      0x9299D0),
    ('letterbox  uncrop setvp',    0x10D67C8),
    ('letterbox  uncrop loadstate',0x10D9458),
    ('letterbox  uncrop beginscene',0x10D9E34),
    ('letterbox  fade x',          0x9F3A24),
    ('letterbox  fade y',          0x9F3A44),
    ('letterbox  fade w',          0x9F3A64),
    ('letterbox  fade h',          0x9F3A84),
    ('moviealign movie quad',      0x10DE8F0),
    ('movieclip  scissor band',    0x1133FE8),
]
own = collections.defaultdict(list)
for name, hook in HOOKS:
    ws = walk(hook, name)
    if ws is None:
        print(f'  {name:30} NOT HOOKED')
        continue
    print(f'  {name:30} {len(ws):2} word(s)  hook +{hook:#09x}  '
          f'cave {" ".join(hex(v) for v in ws[1:])}')
    for v in ws:
        own[v].append(name)

print()
dup = {v: n for v, n in own.items() if len(n) > 1}
if dup:
    print('  *** COLLISIONS ***')
    for v, n in sorted(dup.items()):
        print(f'    +{v:#09x} claimed by {n}')
else:
    print(f'  no shared words: {len(own)} distinct cave words across '
          f'{len([1 for _,h in HOOKS if walk(h,"") is not None])} caves')

# single-word patches must not land inside anyone's cave
SINGLES = [('letterbox bars', 0x10F3DDC), ('letterbox frame h', 0x9298BC),
           ('letterbox origin l2', 0xA05AA4), ('letterbox origin l1', 0xA06EA8),
           ('letterbox origin l3', 0xA07878), ('letterbox origin l4', 0xA08728),
           ('letterbox sprite', 0x929964),
           ('modelcull left', 0x9EC43C), ('modelcull right', 0x9EC49C),
           ('movieclip bypass', 0x11377C4)]
bad = [(n, v) for n, v in SINGLES if v in own]
print('  single-word patches inside a cave: ' + (str(bad) if bad else 'none'))

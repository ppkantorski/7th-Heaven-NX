"""Standalone evaluation of the FFNx derivation chain for the battle effect
dispatchers. Every value is cross-checked against the address encoded in its
own FFNx symbol name; disagreement is fatal."""
import re, struct, sys

import os
EXE_PATH = os.environ.get('FF7_EXE', 'ff7_en')
exe = open(EXE_PATH,'rb').read()
def _sections(data):
    pe = struct.unpack('<I', data[0x3c:0x40])[0]
    nsec = struct.unpack('<H', data[pe+6:pe+8])[0]
    optsz = struct.unpack('<H', data[pe+20:pe+22])[0]
    off = pe+24+optsz; out=[]
    for i in range(nsec):
        s=data[off+40*i:off+40*(i+1)]
        vsize,va,rsize,raw=struct.unpack('<IIII',s[8:24])
        out.append((s[:8].rstrip(b'\0').decode(), va+0x400000, raw, rsize))
    return out
SEC=_sections(exe)
def rd(va,n=4):
    for name,base,raw,size in SEC:
        if base<=va<base+size:
            return exe[raw+(va-base):raw+(va-base)+n]
    raise ValueError('VA 0x%X not mapped'%va)
def gav(base,off):                       # get_absolute_value
    return struct.unpack('<I', rd(base+off))[0]
def grc(base,off):                       # get_relative_call
    b=rd(base+off,6)
    if b[0] in (0xE8,0xE9):
        return base+off+5+struct.unpack('<i',b[1:5])[0]
    if b[0]==0xFF and b[1]==0x15:
        return struct.unpack('<I', rd(struct.unpack('<I',b[2:6])[0]))[0]
    raise ValueError('no call/jmp at 0x%X (bytes %s)'%(base+off,b.hex(' ')))

S={}
def put(n,v):
    S[n]=v
    m=re.search(r'_((?:sub_)?)([0-9A-F]{6})$', n)
    if m and int(m.group(2),16)!=v:
        sys.exit('CHAIN MISMATCH %s: derived 0x%X, name says 0x%s'%(n,v,m.group(2)))
    return v

# ---- roots -------------------------------------------------------------
# engine_loop_obj initialiser at 0x40A3C1..0x40A3FF; main_loop at 0x40A3CE.
b = rd(0x40A3CE,10)
assert b[0]==0xC7, 'engine_loop_obj initialiser moved'
main_loop = struct.unpack('<I', b[6:10])[0]
assert struct.unpack('<I', rd(main_loop))[0] == 0x81EC8B55, 'main_loop prologue'
put('main_loop', main_loop)

battle_main_loop = gav(main_loop, 0x8C)      # ff7_data.h
battle_main_loop = put('battle_main_loop', gav(main_loop, 0x89A))
battle_loop      = put('battle_loop',      grc(battle_main_loop, 0x1C8))
battle_sub_429AC0= put('battle_sub_429AC0',gav(battle_loop, 0x79))
battle_sub_42D808= put('battle_sub_42D808',grc(battle_sub_429AC0, 0xE7))
battle_sub_42D992= put('battle_sub_42D992',grc(battle_sub_42D808, 0x30))
battle_sub_42A5D0= put('battle_sub_42A5D0',grc(battle_sub_429AC0, 0x1A6))
battle_sub_42A5EB= put('battle_sub_42A5EB',grc(battle_sub_42A5D0, 0x14))
ras = put('run_animation_script', grc(battle_sub_42A5EB, 0xB8))

# The byte the pause-throttle sets. FFNx ff7_data.h:
#   ff7_externals.g_is_battle_paused =
#       (byte*)get_absolute_value(ff7_externals.run_animation_script, 0xA);
# Named with FFNx's ff7.h suffix so `put` cross-checks the derivation against
# the address FFNx itself recorded -- this is a data pointer, so the recompiler
# map cannot corroborate it and the name is the only independent witness.
put('g_is_battle_paused_DC0E6C', gav(ras, 0xA))
S['g_is_battle_paused'] = S['g_is_battle_paused_DC0E6C']

# ---- dispatchers -------------------------------------------------------
put('execute_effect10_fn',  grc(battle_sub_42D992, 0x4D))
put('execute_effect60_fn',  grc(battle_sub_42D992, 0x129))
put('execute_effect100_fn', grc(battle_sub_42D992, 0x12E))
put('handle_camera_functions', grc(battle_sub_42D992, 0xE3))
put('execute_camera_functions', grc(S['handle_camera_functions'], 0x55))
scfps = put('set_camera_focal_position_scripts', grc(S['handle_camera_functions'], 0x35))
put('add_fn_to_camera_fn_array', grc(scfps, 0xF40))

a10  = put('add_fn_to_effect10_fn',  grc(ras, 0x825))
a60  = put('add_fn_to_effect60_fn',  grc(ras, 0x394))
a100 = put('add_fn_to_effect100_fn', grc(ras, 0x48C2))

# ---- slot tables (data, so no name suffix to check) --------------------
for tag, a in (('10', a10), ('60', a60), ('100', a100)):
    S['effect%s_array_data'%tag] = gav(a, 0x5D)
    S['effect%s_array_fn'%tag]   = gav(a, 0x48)
    S['effect%s_array_idx'%tag]  = gav(a, 0x32)
    S['effect%s_counter'%tag]    = gav(a, 0x63)
acam = S['add_fn_to_camera_fn_array']
S['camera_fn_array']   = gav(acam, 0x39)
S['camera_fn_data']    = gav(acam, 0x4D)
S['camera_fn_index']   = gav(acam, 0x46)
S['camera_fn_counter'] = gav(acam, 0x54)

# ---- effect10 dispatch list -------------------------------------------
b426C9B = put('battle_sub_426C9B', grc(ras, 0x14C7))
put('battle_sub_426DE3', gav(b426C9B, 0x5))
put('battle_sub_426941', gav(ras, 0x1A5D))
put('battle_sub_426899', gav(ras, 0x821))
put('battle_sub_4267F1', gav(ras, 0xFF6))
put('battle_move_character_sub_426A26', gav(ras, 0x1568))
put('battle_move_character_sub_42739D', gav(ras, 0x248E))
put('battle_move_character_sub_426F58', gav(ras, 0x26AF))
put('battle_move_character_sub_4270DE', gav(ras, 0x2357))

# ---- effect100 dispatch list ------------------------------------------
put('display_battle_action_text_42782A', gav(ras, 0x4906))
b425D29 = put('battle_sub_425D29', gav(ras, 0x2850))
battle_sub_42CBF9 = put('battle_sub_42CBF9', grc(battle_loop, 0x425))
put('battle_sub_5BDA0F', gav(battle_sub_42CBF9, 0x240))



scps = put('set_camera_position_scripts', grc(S['handle_camera_functions'], 0x4B))

# ---- the camera script interpreters' wait state -------------------------
# FFNx ff7_data.h:
#   battle_camera_position    = get_absolute_value(set_camera_position_scripts, 0x331)
#   battle_camera_focal_point = get_absolute_value(set_camera_focal_position_scripts, 0x233)
# Each is 4 x struct bcamera_position (0xE bytes):
#   +0 point(6)  +6 unused  +8 current_position  +0xA frames_to_wait  +0xC,+0xD bytes
# These are data, so there is no name suffix to check them against; the layout
# assertion below is the substitute.
S['battle_camera_position'] = gav(scps, 0x331)
S['battle_camera_focal_point'] = gav(S['set_camera_focal_position_scripts'], 0x233)
if S['battle_camera_focal_point'] - S['battle_camera_position'] != 0x40:
    sys.exit('CHAIN MISMATCH battle_camera_position 0x%X and '
             'battle_camera_focal_point 0x%X are not one 4-entry array apart'
             % (S['battle_camera_position'], S['battle_camera_focal_point']))
BCAMERA_STRIDE = 0xE
BCAMERA_CURRENT_POSITION = 0x8
BCAMERA_FRAMES_TO_WAIT = 0xA
put('battle_camera_position_sub_5C3D0D', gav(scps, 0x5DE))
put('battle_camera_position_sub_5C5B9C', gav(scps, 0x40A))
put('battle_camera_position_sub_5C557D', gav(scps, 0xE28))
put('battle_camera_focal_sub_5C5F5E', gav(S['set_camera_focal_position_scripts'], 0xBDB))
put('battle_camera_focal_sub_5C5714', gav(S['set_camera_focal_position_scripts'], 0x67D))


# ---- effect100 arithmetic branches: Tifa's limit breaks -----------------
_b4E1627 = put('battle_sub_4E1627', grc(ras, 0x3848))
put('run_tifa_limit_effects', grc(_b4E1627, 0xD))
_t12 = put('tifa_limit_1_2_main_4E2DF3', gav(S['run_tifa_limit_effects'], 0x47))
put('tifa_limit_1_2_sub_4E3D51', gav(_t12, 0x4BB))
_t21 = put('tifa_limit_2_1_main_4E401E', gav(S['run_tifa_limit_effects'], 0x67))
put('tifa_limit_2_1_sub_4E48D4', gav(_t21, 0x41A))

if __name__ == '__main__':
    for k in sorted(S):
        print('%-40s 0x%X' % (k, S[k]))

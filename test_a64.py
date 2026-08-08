import struct
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
import a64 as A
md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
def H(v):                      # capstone prints <10 decimal, >=10 hex
    return str(v) if v < 10 else hex(v)
fails = []
def chk(word, want, pc=0x1000):
    got = None
    for i in md.disasm(struct.pack('<I', word), pc):
        got = ('%s %s' % (i.mnemonic, i.op_str)).strip()
    if got != want:
        fails.append('%08X: got %-40r want %r' % (word, got, want))
n = 0
for rt in (0, 8, 16, 17, 19, 22):
    for imm in (0, 4, 8, 0x14, 0x18, 0x3C):
        chk(A.ldr(rt, 0), 'ldr w%d, [x0]' % rt) ; n += 1
        chk(A.ldr(rt, 21, imm), 'ldr w%d, [x21, #%s]' % (rt, H(imm)) if imm else 'ldr w%d, [x21]' % rt); n += 1
        chk(A.str_(rt, 21, imm), 'str w%d, [x21, #%s]' % (rt, H(imm)) if imm else 'str w%d, [x21]' % rt); n += 1
for off in (0, 2, 4, 6, 8, 0xA, 0xC, 0xE, 0x1A):
    chk(A.ldrh(17, 0, off), 'ldrh w17, [x0, #'+H(off)+']' if off else 'ldrh w17, [x0]'); n += 1
    chk(A.strh(17, 0, off), 'strh w17, [x0, #'+H(off)+']' if off else 'strh w17, [x0]'); n += 1
    chk(A.ldrsh(17, 0, off), 'ldrsh w17, [x0, #'+H(off)+']' if off else 'ldrsh w17, [x0]'); n += 1
for off in (0, 1, 0x18, 0x19, 0x1A):
    chk(A.ldrb(17, 0, off), 'ldrb w17, [x0, #'+H(off)+']' if off else 'ldrb w17, [x0]'); n += 1
    chk(A.strb(17, 0, off), 'strb w17, [x0, #'+H(off)+']' if off else 'strb w17, [x0]'); n += 1
chk(A.strb(A.WZR, 17, 0), 'strb wzr, [x17]'); n += 1
chk(A.ldr64(8, A.SP, 0), 'ldr x8, [sp]'); n += 1
chk(A.str64(8, A.SP, 0), 'str x8, [sp]'); n += 1
chk(A.sub_imm64(A.SP, A.SP, 0x10), 'sub sp, sp, #0x10'); n += 1
chk(A.add_imm64(A.SP, A.SP, 0x10), 'add sp, sp, #0x10'); n += 1
chk(A.add_reg64(17, 17, 16), 'add x17, x17, x16'); n += 1
chk(A.add_reg(16, 16, 16), 'add w16, w16, w16'); n += 1
chk(A.add_reg(17, 17, 16), 'add w17, w17, w16'); n += 1
chk(A.mov_reg(0, 17), 'mov w0, w17'); n += 1
chk(A.cmp_reg(8, 17), 'cmp w8, w17'); n += 1
chk(A.cmp_imm(17, 1), 'cmp w17, #1'); n += 1
chk(A.and_mask(16, 16, 4), 'and w16, w16, #0xf'); n += 1
chk(A.and_mask(16, 16, 7), 'and w16, w16, #0x7f'); n += 1
chk(A.and_mask(16, 16, 2), 'and w16, w16, #3'); n += 1
chk(A.and_mask(16, 16, 1), 'and w16, w16, #1'); n += 1
for sh in (1, 2, 3, 5):
    chk(A.lsl(17, 17, sh), 'lsl w17, w17, #%d' % sh); n += 1
    chk(A.asr(17, 17, sh), 'asr w17, w17, #%d' % sh); n += 1
chk(A.asr(16, 17, 31), 'asr w16, w17, #0x1f'); n += 1
chk(A.movz(17, 0x2e70), 'mov w17, #0x2e70'); n += 1
chk(A.movk_hi(17, 0xbf), 'movk w17, #0xbf, lsl #16'); n += 1
chk(A.adrp(17, 0x1152660, 0x3FEC000), 'adrp x17, #0x3fec000', 0x1152660); n += 1
chk(A.b(0x1000, 0x1040), 'b #0x1040'); n += 1
chk(A.b(0x1040, 0x1000), 'b #0x1000', 0x1040); n += 1
chk(A.bl(0x1152660, 0x10FC3A0), 'bl #0x10fc3a0', 0x1152660); n += 1
chk(A.bcond(0x1000, 0x1020, A.NE), 'b.ne #0x1020'); n += 1
chk(A.bcond(0x1000, 0x1020, A.LE), 'b.le #0x1020'); n += 1
chk(A.cbz(16, 0x1000, 0x1080), 'cbz w16, #0x1080'); n += 1
chk(A.cbz64(0, 0x1000, 0x1080), 'cbz x0, #0x1080'); n += 1
chk(A.cbz64(0, 0x1050, 0x1000), 'cbz x0, #0x1000', 0x1050); n += 1
for rd, rn, rm in ((16, 16, 0), (0, 16, 17), (17, 0, 16)):
    chk(A.mul(rd, rn, rm), 'mul w%d, w%d, w%d' % (rd, rn, rm)); n += 1
print('a64 encoder: %d forms checked, %d mismatch' % (n, len(fails)))
for f in fails: print('  ' + f)
raise SystemExit(1 if fails else 0)
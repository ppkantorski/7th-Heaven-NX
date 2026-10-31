#!/bin/sh
# Rebuild the PS3Tweaks blobs (BUILD 868): ps3tweaks.bin from
# ps3tweaks_head.S + ps3tweaks.S + ps3tweaks_c.c, and ps3tweaks2.bin from
# ps3tweaks_head.S + ps3tweaks2.S + ps3tweaks2_c.c, each placed in a dead
# body of its own. One section per blob, .text.pt, so every branch and adr is
# resolved by the assembler and none may carry a relocation. The C file is
# compiled to assembly and appended, so its static functions are local to
# the same section. The entries come from the symbol table. LLVM (default,
# as the other native blobs) or binutils: TOOLS=aarch64-linux-gnu-
set -e
cd "$(dirname "$0")"
T=${TMPDIR:-/tmp}
CFLAGS="-Os -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-stack-protector \
 -fno-asynchronous-unwind-tables -fno-unwind-tables -fno-exceptions \
 -fno-jump-tables -mstrict-align -mgeneral-regs-only -ffixed-x18 -Wall -Werror"
if [ -n "$TOOLS" ]; then
  CC="${TOOLS}gcc"; AS="${TOOLS}gcc"
  OBJDUMP=${TOOLS}objdump; OBJCOPY=${TOOLS}objcopy; NM=${TOOLS}nm
else
  LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
  CC="$LLVM/clang --target=aarch64-none-elf"; AS="$CC"
  OBJDUMP=$LLVM/llvm-objdump; OBJCOPY=$LLVM/llvm-objcopy; NM=$LLVM/llvm-nm
fi
for B in ps3tweaks ps3tweaks2 ps3tweaks3 ps3tweaks4 ps3tweaks5 ps3tweaks6 ps3tweaks7 ps3tweaks8 ps3tweaks9 ps3tweaks10 ps3tweaks11 ps3tweaks12 ps3tweaks13 ps3tweaks14 ps3tweaks15 ps3tweaks16 ps3tweaks17 ps3tweaks18 ps3tweaks19 ps3tweaks20 ps3tweaks21 ps3tweaks22; do
  O=$T/$B.o
  $CC $CFLAGS -S ${B}_c.c -o $T/${B}_c.s
  cat ps3tweaks_head.S $B.S $T/${B}_c.s > $T/${B}_all.S
  $AS -c $T/${B}_all.S -o $O
  if $OBJDUMP -h $O | awk '$2 ~ /^\.(data|bss|rodata)/ && $3 != "00000000"' | grep -q .; then
    echo "$B.o has data sections"; exit 1; fi
  if $OBJDUMP -h $O | awk '$2 ~ /^\.text/ && $2 != ".text.pt" && $3 != "00000000"' | grep -q .; then
    echo "$B.o: code outside .text.pt"; exit 1; fi
  if $OBJDUMP -r $O | grep -q R_AARCH64; then
    echo "$B.o has relocations"; exit 1; fi
  $OBJCOPY -O binary --only-section=.text.pt $O $B.bin
  : > $B.entry
  for fn in $($NM $O | awk '$2 == "T" {print $3}' | sort); do
    off=$($NM $O | awk -v f=$fn '$3 == f && $2 == "T" {print $1}')
    printf '%s %08x\n' $fn $((0x$off)) >> $B.entry
  done
  echo "$B.bin $(wc -c < $B.bin | tr -d ' ') bytes, entries: $(tr '\n' ' ' < $B.entry)"
done
(cat ps3tweaks.h ps3tweaks_head.S ps3tweaks.S ps3tweaks_c.c ps3tweaks2.S ps3tweaks2_c.c ps3tweaks3.S ps3tweaks3_c.c ps3tweaks4.S ps3tweaks4_c.c ps3tweaks5.S ps3tweaks5_c.c ps3tweaks6.S ps3tweaks6_c.c ps3tweaks7.S ps3tweaks7_c.c ps3tweaks8.S ps3tweaks8_c.c ps3tweaks9.S ps3tweaks9_c.c ps3tweaks10.S ps3tweaks10_c.c ps3tweaks11.S ps3tweaks11_c.c ps3tweaks12.S ps3tweaks12_c.c ps3tweaks13.S ps3tweaks13_c.c ps3tweaks14.S ps3tweaks14_c.c ps3tweaks15.S ps3tweaks15_c.c ps3tweaks16.S ps3tweaks16_c.c ps3tweaks17.S ps3tweaks17_c.c ps3tweaks18.S ps3tweaks18_c.c ps3tweaks19.S ps3tweaks19_c.c ps3tweaks20.S ps3tweaks20_c.c ps3tweaks21.S ps3tweaks21_c.c ps3tweaks22.S ps3tweaks22_c.c \
  | shasum -a 256 2>/dev/null \
  || cat ps3tweaks.h ps3tweaks_head.S ps3tweaks.S ps3tweaks_c.c ps3tweaks2.S ps3tweaks2_c.c ps3tweaks3.S ps3tweaks3_c.c ps3tweaks4.S ps3tweaks4_c.c ps3tweaks5.S ps3tweaks5_c.c ps3tweaks6.S ps3tweaks6_c.c ps3tweaks7.S ps3tweaks7_c.c ps3tweaks8.S ps3tweaks8_c.c ps3tweaks9.S ps3tweaks9_c.c ps3tweaks10.S ps3tweaks10_c.c ps3tweaks11.S ps3tweaks11_c.c ps3tweaks12.S ps3tweaks12_c.c ps3tweaks13.S ps3tweaks13_c.c ps3tweaks14.S ps3tweaks14_c.c ps3tweaks15.S ps3tweaks15_c.c ps3tweaks16.S ps3tweaks16_c.c ps3tweaks17.S ps3tweaks17_c.c ps3tweaks18.S ps3tweaks18_c.c ps3tweaks19.S ps3tweaks19_c.c ps3tweaks20.S ps3tweaks20_c.c ps3tweaks21.S ps3tweaks21_c.c ps3tweaks22.S ps3tweaks22_c.c \
  | sha256sum) | cut -d" " -f1 > ps3tweaks.src.sha256

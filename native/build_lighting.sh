#!/bin/sh
# Rebuild lighting.bin from lighting.c (LLVM clang; no linker needed: every
# function is in ONE section, .text.lt, so calls between them are resolved by
# the assembler, and none may carry a relocation). BUILD 679: the entries
# come from the symbol table.
set -e
cd "$(dirname "$0")"
LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
FLAGS="--target=aarch64-none-elf -Os -ffreestanding -fno-builtin -nostdlib -fno-pic \
 -mcmodel=tiny -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -ffixed-x18 -fno-math-errno \
 -fno-vectorize -fno-slp-vectorize -Wall -Werror"
O=${TMPDIR:-/tmp}/lighting.o
$LLVM/clang $FLAGS -c lighting.c -o $O
if $LLVM/llvm-objdump -r $O | grep -q R_AARCH64; then
  echo "lighting.o has relocations"; exit 1; fi
if $LLVM/llvm-objdump -h $O | awk '$2 ~ /^\.(data|bss|rodata)/ && $3 != "00000000"' | grep -q .; then
  echo "lighting.o has data sections"; exit 1; fi
if $LLVM/llvm-objdump -h $O | awk '$2 ~ /^\.text/ && $2 != ".text.lt" && $3 != "00000000"' | grep -q .; then
  echo "code outside .text.lt"; exit 1; fi
$LLVM/llvm-objcopy -O binary --only-section=.text.lt $O lighting.bin
: > lighting.entry
for fn in lt_setup lt_vtx lt_mvfix lt_entry; do
  off=$($LLVM/llvm-nm $O | awk -v f=$fn '$3 == f && $2 == "T" {print $1}')
  [ -n "$off" ] || { echo "no $fn"; exit 1; }
  printf '%s %08x\n' $fn $((0x$off)) >> lighting.entry
done
shasum -a 256 lighting.c | cut -d" " -f1 > lighting.src.sha256
echo "lighting.bin $(wc -c < lighting.bin | tr -d ' ') bytes, entries: $(tr '\n' ' ' < lighting.entry)"

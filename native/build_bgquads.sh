#!/bin/sh
# Rebuild bgquads.bin from bgquads.c (LLVM clang; no linker needed: every
# function is in ONE section, .text.bg, so calls between them are resolved by
# the assembler, and none may carry a relocation). BUILD 792. The entry
# comes from the symbol table. -ffp-contract=off: the x87 sums stay separate
# double adds, as the translation does them.
set -e
cd "$(dirname "$0")"
LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
FLAGS="--target=aarch64-none-elf -O2 -ffreestanding -fno-builtin -nostdlib -fno-pic \
 -mcmodel=tiny -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -ffixed-x18 -fno-math-errno \
 -ffp-contract=off -fno-vectorize -fno-slp-vectorize -Wall -Werror"
O=${TMPDIR:-/tmp}/bgquads.o
$LLVM/clang $FLAGS -c bgquads.c -o $O
if $LLVM/llvm-objdump -r $O | grep -q R_AARCH64; then
  echo "bgquads.o has relocations"; exit 1; fi
if $LLVM/llvm-objdump -h $O | awk '$2 ~ /^\.(data|bss|rodata)/ && $3 != "00000000"' | grep -q .; then
  echo "bgquads.o has data sections"; exit 1; fi
if $LLVM/llvm-objdump -h $O | awk '$2 ~ /^\.text/ && $2 != ".text.bg" && $3 != "00000000"' | grep -q .; then
  echo "code outside .text.bg"; exit 1; fi
$LLVM/llvm-objcopy -O binary --only-section=.text.bg $O bgquads.bin
: > bgquads.entry
for fn in bg_6465fb bg_64085c bg_640c49; do
  off=$($LLVM/llvm-nm $O | awk -v f=$fn '$3 == f && $2 == "T" {print $1}')
  [ -n "$off" ] || { echo "no $fn"; exit 1; }
  printf '%s %08x\n' $fn $((0x$off)) >> bgquads.entry
done
shasum -a 256 bgquads.c | cut -d" " -f1 > bgquads.src.sha256
echo "bgquads.bin $(wc -c < bgquads.bin | tr -d ' ') bytes, entries: $(tr '\n' ' ' < bgquads.entry)"

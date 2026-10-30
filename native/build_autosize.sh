#!/bin/sh
# Rebuild autosize.bin from autosize.c (BUILD 860; LLVM clang; no linker needed: every
# function is in ONE section, .text.as, so calls between them are resolved by
# the assembler, and none may carry a relocation). The entries
# come from the symbol table.
set -e
cd "$(dirname "$0")"
LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
FLAGS="--target=aarch64-none-elf -Os -ffreestanding -fno-builtin -nostdlib -fno-pic \
 -mcmodel=tiny -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -ffixed-x18 -fno-math-errno \
 -fno-vectorize -fno-slp-vectorize -Wall -Werror"
O=${TMPDIR:-/tmp}/autosize.o
$LLVM/clang $FLAGS -c autosize.c -o $O
if $LLVM/llvm-objdump -r $O | grep -q R_AARCH64; then
  echo "autosize.o has relocations"; exit 1; fi
if $LLVM/llvm-objdump -h $O | awk '$2 ~ /^\.(data|bss|rodata)/ && $3 != "00000000"' | grep -q .; then
  echo "autosize.o has data sections"; exit 1; fi
if $LLVM/llvm-objdump -h $O | awk '$2 ~ /^\.text/ && $2 != ".text.as" && $3 != "00000000"' | grep -q .; then
  echo "code outside .text.as"; exit 1; fi
$LLVM/llvm-objcopy -O binary --only-section=.text.as $O autosize.bin
: > autosize.entry
for fn in as_open; do
  off=$($LLVM/llvm-nm $O | awk -v f=$fn '$3 == f && $2 == "T" {print $1}')
  [ -n "$off" ] || { echo "no $fn"; exit 1; }
  printf '%s %08x\n' $fn $((0x$off)) >> autosize.entry
done
shasum -a 256 autosize.c | cut -d" " -f1 > autosize.src.sha256
echo "autosize.bin $(wc -c < autosize.bin | tr -d ' ') bytes, entries: $(tr '\n' ' ' < autosize.entry)"

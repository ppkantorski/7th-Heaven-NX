#!/bin/sh
# Rebuild palconv.bin from palconv.c (LLVM clang; no linker needed: every
# function is in ONE section, .text.pc, so calls between them are resolved by
# the assembler, and none may carry a relocation). BUILD 797. The entry
set -e
cd "$(dirname "$0")"
LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
FLAGS="--target=aarch64-none-elf -O2 -ffreestanding -fno-builtin -nostdlib -fno-pic \
 -mcmodel=tiny -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -ffixed-x18 -fno-math-errno \
 -ffp-contract=off -fno-vectorize -fno-slp-vectorize -Wall -Werror"
O=${TMPDIR:-/tmp}/palconv.o
$LLVM/clang $FLAGS -c palconv.c -o $O
if $LLVM/llvm-objdump -r $O | grep -q R_AARCH64; then
  echo "palconv.o has relocations"; exit 1; fi
if $LLVM/llvm-objdump -h $O | awk '$2 ~ /^\.(data|bss|rodata)/ && $3 != "00000000"' | grep -q .; then
  echo "palconv.o has data sections"; exit 1; fi
if $LLVM/llvm-objdump -h $O | awk '$2 ~ /^\.text/ && $2 != ".text.pc" && $3 != "00000000"' | grep -q .; then
  echo "code outside .text.pc"; exit 1; fi
$LLVM/llvm-objcopy -O binary --only-section=.text.pc $O palconv.bin
: > palconv.entry
for fn in pc_convert; do
  off=$($LLVM/llvm-nm $O | awk -v f=$fn '$3 == f && $2 == "T" {print $1}')
  [ -n "$off" ] || { echo "no $fn"; exit 1; }
  printf '%s %08x\n' $fn $((0x$off)) >> palconv.entry
done
shasum -a 256 palconv.c | cut -d" " -f1 > palconv.src.sha256
echo "palconv.bin $(wc -c < palconv.bin | tr -d ' ') bytes, entries: $(tr '\n' ' ' < palconv.entry)"

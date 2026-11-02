#!/bin/sh
# DIAGNOSTIC (BUILD 876p): lightprobe.bin = lighting.c with -DLT_PROBE
# (tools/probes/lightprobe.py). Same rules as build_lighting.sh.
set -e
cd "$(dirname "$0")"
LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
FLAGS="--target=aarch64-none-elf -Os -ffreestanding -fno-builtin -nostdlib -fno-pic \
 -mcmodel=tiny -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -ffixed-x18 -fno-math-errno \
 -fno-vectorize -fno-slp-vectorize -Wall -Werror -DLT_PROBE"
O=${TMPDIR:-/tmp}/lightprobe.o
$LLVM/clang $FLAGS -c lighting.c -o $O
if $LLVM/llvm-objdump -r $O | grep -q R_AARCH64; then
  echo "lightprobe.o has relocations"; exit 1; fi
if $LLVM/llvm-objdump -h $O | awk '$2 ~ /^\.(data|bss|rodata)/ && $3 != "00000000"' | grep -q .; then
  echo "lightprobe.o has data sections"; exit 1; fi
if $LLVM/llvm-objdump -h $O | awk '$2 ~ /^\.text/ && $2 != ".text.lt" && $3 != "00000000"' | grep -q .; then
  echo "code outside .text.lt"; exit 1; fi
$LLVM/llvm-objcopy -O binary --only-section=.text.lt $O lightprobe.bin
: > lightprobe.entry
for fn in lt_setup lt_vtx lt_mvfix lt_entry lp_frame; do
  off=$($LLVM/llvm-nm $O | awk -v f=$fn '$3 == f && $2 == "T" {print $1}')
  [ -n "$off" ] || { echo "no $fn"; exit 1; }
  printf '%s %08x\n' $fn $((0x$off)) >> lightprobe.entry
done
echo "lightprobe.bin $(wc -c < lightprobe.bin | tr -d ' ') bytes, entries: $(tr '\n' ' ' < lightprobe.entry)"

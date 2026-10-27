#!/bin/sh
# Rebuild wmtime.bin from wmtime.c (Homebrew LLVM clang; no linker needed:
# each entry is its own section, and none of them may carry a relocation).
set -e
cd "$(dirname "$0")"
LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
FLAGS="--target=aarch64-none-elf -Os -ffreestanding -fno-builtin -nostdlib -fno-pic \
 -mcmodel=tiny -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -ffixed-x18 -fno-math-errno \
 -fno-vectorize -fno-slp-vectorize -Wall -Werror"
$LLVM/clang $FLAGS -c wmtime.c -o ${TMPDIR:-/tmp}/wmtime.o
if $LLVM/llvm-objdump -r ${TMPDIR:-/tmp}/wmtime.o | grep -q R_AARCH64; then
  echo "wmtime.o has relocations"; exit 1; fi
if $LLVM/llvm-objdump -h ${TMPDIR:-/tmp}/wmtime.o | awk '$2 ~ /^\.(data|bss|rodata)/ && $3 != "00000000"' | grep -q .; then
  echo "wmtime.o has data sections"; exit 1; fi
: > wmtime.bin; : > wmtime.entry
for fn in wt_pre wt_post; do
  off=$(wc -c < wmtime.bin | tr -d ' ')
  pad=$(( (16 - off % 16) % 16 ))
  [ $pad -gt 0 ] && head -c $pad /dev/zero >> wmtime.bin
  off=$(wc -c < wmtime.bin | tr -d ' ')
  $LLVM/llvm-objcopy -O binary --only-section=.text.$fn ${TMPDIR:-/tmp}/wmtime.o ${TMPDIR:-/tmp}/wmtime_$fn.bin
  cat ${TMPDIR:-/tmp}/wmtime_$fn.bin >> wmtime.bin
  printf '%s %08x\n' $fn $off >> wmtime.entry
done
shasum -a 256 wmtime.c | cut -d" " -f1 > wmtime.src.sha256
echo "wmtime.bin $(wc -c < wmtime.bin | tr -d ' ') bytes, entries: $(tr '\n' ' ' < wmtime.entry)"

#!/bin/sh
# Rebuild shadows.bin from shadows.c (Homebrew LLVM clang; no linker needed:
# each entry is its own section, and none of them may carry a relocation).
set -e
cd "$(dirname "$0")"
LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
FLAGS="--target=aarch64-none-elf -Os -ffreestanding -fno-builtin -nostdlib -fno-pic \
 -mcmodel=tiny -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -ffixed-x18 -fno-math-errno \
 -fno-vectorize -fno-slp-vectorize -Wall -Werror"
$LLVM/clang $FLAGS -c shadows.c -o ${TMPDIR:-/tmp}/shadows.o
if $LLVM/llvm-objdump -r ${TMPDIR:-/tmp}/shadows.o | grep -q R_AARCH64; then
  echo "shadows.o has relocations"; exit 1; fi
if $LLVM/llvm-objdump -h ${TMPDIR:-/tmp}/shadows.o | awk '$2 ~ /^\.(data|bss|rodata)/ && $3 != "00000000"' | grep -q .; then
  echo "shadows.o has data sections"; exit 1; fi
: > shadows.bin; : > shadows.entry
for fn in sh_pre sh_begin sh_end sh_blob_done sh_field sh_world sh_wcam sh_wblob; do
  off=$(wc -c < shadows.bin | tr -d ' ')
  pad=$(( (16 - off % 16) % 16 ))
  [ $pad -gt 0 ] && head -c $pad /dev/zero >> shadows.bin
  off=$(wc -c < shadows.bin | tr -d ' ')
  $LLVM/llvm-objcopy -O binary --only-section=.text.$fn ${TMPDIR:-/tmp}/shadows.o ${TMPDIR:-/tmp}/shadows_$fn.bin
  cat ${TMPDIR:-/tmp}/shadows_$fn.bin >> shadows.bin
  printf '%s %08x\n' $fn $off >> shadows.entry
done
shasum -a 256 shadows.c | cut -d" " -f1 > shadows.src.sha256
echo "shadows.bin $(wc -c < shadows.bin | tr -d ' ') bytes, entries: $(tr '\n' ' ' < shadows.entry)"

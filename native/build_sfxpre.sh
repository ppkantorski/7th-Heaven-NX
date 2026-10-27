#!/bin/sh
# Rebuild sfxpre.bin from sfxpre.c (LLVM clang; no linker needed: each entry
# is its own section, and none of them may carry a relocation).
# LLVM=/usr/bin works on Linux; the default is Homebrew's.
set -e
cd "$(dirname "$0")"
LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
FLAGS="--target=aarch64-none-elf -Os -ffreestanding -fno-builtin -nostdlib -fno-pic \
 -mcmodel=tiny -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -ffixed-x18 -fno-math-errno \
 -fno-vectorize -fno-slp-vectorize -Wall -Werror"
$LLVM/clang $FLAGS -c sfxpre.c -o ${TMPDIR:-/tmp}/sfxpre.o
if $LLVM/llvm-objdump -r ${TMPDIR:-/tmp}/sfxpre.o | grep -q R_AARCH64; then
  echo "sfxpre.o has relocations"; exit 1; fi
if $LLVM/llvm-objdump -h ${TMPDIR:-/tmp}/sfxpre.o | awk '$2 ~ /^\.(data|bss|rodata)/ && $3 != "00000000"' | grep -q .; then
  echo "sfxpre.o has data sections"; exit 1; fi
: > sfxpre.bin; : > sfxpre.entry
for fn in sp_plan sp_prep sp_take sp_find sp_store; do
  off=$(wc -c < sfxpre.bin | tr -d ' ')
  pad=$(( (16 - off % 16) % 16 ))
  [ $pad -gt 0 ] && head -c $pad /dev/zero >> sfxpre.bin
  off=$(wc -c < sfxpre.bin | tr -d ' ')
  $LLVM/llvm-objcopy -O binary --only-section=.text.$fn ${TMPDIR:-/tmp}/sfxpre.o ${TMPDIR:-/tmp}/sfxpre_$fn.bin
  cat ${TMPDIR:-/tmp}/sfxpre_$fn.bin >> sfxpre.bin
  printf '%s %08x\n' $fn $off >> sfxpre.entry
done
(shasum -a 256 sfxpre.c 2>/dev/null || sha256sum sfxpre.c) | cut -d" " -f1 > sfxpre.src.sha256
echo "sfxpre.bin $(wc -c < sfxpre.bin | tr -d ' ') bytes, entries: $(tr '\n' ' ' < sfxpre.entry)"

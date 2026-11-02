#!/bin/sh
set -e
cd "$(dirname "$0")"
LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
FLAGS="--target=aarch64-none-elf -Os -ffreestanding -fno-builtin -nostdlib -fno-pic \
 -mcmodel=tiny -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -ffixed-x18 -Wall -Werror"
O=${TMPDIR:-/tmp}/movieprobe.o
$LLVM/clang $FLAGS -c movieprobe.c -o $O
if $LLVM/llvm-objdump -r $O | grep -q R_AARCH64; then echo relocs; exit 1; fi
if $LLVM/llvm-objdump -h $O | awk '$2 ~ /^\.text/ && $2 != ".text.mp" && $3 != "00000000"' | grep -q .; then echo "code outside"; exit 1; fi
$LLVM/llvm-objcopy -O binary --only-section=.text.mp $O movieprobe.bin
: > movieprobe.entry
for fn in mp_present mp_update mp_frame; do
  off=$($LLVM/llvm-nm $O | awk -v f=$fn '$3 == f && $2 == "T" {print $1}')
  printf '%s %08x\n' $fn $((0x$off)) >> movieprobe.entry
done
echo "movieprobe.bin $(wc -c < movieprobe.bin) bytes: $(tr '\n' ' ' < movieprobe.entry)"

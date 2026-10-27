#!/bin/sh
# Rebuild fxinterp.bin from fxinterp.c (BUILD 747; see ff7nx_fxinterp.py).
# Position independence is proven by linking at two bases and comparing.
set -e
cd "$(dirname "$0")"
CC=${CC:-gcc}
FLAGS="-Os -ffreestanding -fno-builtin -nostdlib -fno-pic -mcmodel=tiny \
 -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -fno-tree-loop-distribute-patterns \
 -ffixed-x18 -mgeneral-regs-only -Wall -Werror"
T=${TMPDIR:-/tmp}
# fxinterp.bin is the 60 FPS build (4 sub-steps per battle tick);
# fxinterp30.bin the Full 30 FPS one (2 sub-steps), from the same source.
build() {
  out=$1; shift
  $CC $FLAGS "$@" -c fxinterp.c -o $T/fxinterp.o
  if objdump -h $T/fxinterp.o | grep -E '\.(data|bss|rodata)' | awk '$3 != "00000000"' | grep -q .; then
    echo "fxinterp.o has data sections"; exit 1; fi
  for base in 0x0 0x123450; do
    printf 'SECTIONS { . = %s; .text : { *(.text.fx_material) *(.text.fx_spt) *(.text*) } /DISCARD/ : { *(.eh_frame*) *(.comment) *(.note*) } }\n' $base > $T/fx.ld
    ld -T $T/fx.ld -o $T/fxinterp_$base.elf $T/fxinterp.o
    objcopy -O binary -j .text $T/fxinterp_$base.elf $T/fxinterp_$base.bin
  done
  cmp $T/fxinterp_0x0.bin $T/fxinterp_0x123450.bin
  cp $T/fxinterp_0x0.bin $out.bin
  nm $T/fxinterp_0x0.elf | awk '$2=="T" && $3 ~ /^fx_/ {print $3, $1}' | sort > $out.entry
  echo "$out.bin $(wc -c < $out.bin) bytes, entries: $(cat $out.entry | tr "\n" " ")"
}
build fxinterp
build fxinterp30 -DFREQ=2
sha256sum fxinterp.c | cut -d" " -f1 > fxinterp.src.sha256

#!/usr/bin/env python3
"""Safely patch a Nintendo Switch NSO0 executable, including compressed NSOs.

This tool is deliberately limited to verified in-place ARM64 byte edits.  It
does not invent patch locations or inject a code cave.  Each patch must state
the exact original bytes expected at its target, so a different game update or
module revision fails before an output file is written.

Patch-spec format:
{
  "name": "descriptive name",
  "input_sha256": "optional whole-file SHA-256",
  "patches": [
    {
      "name": "descriptive edit",
      "va": "0xfd70",                 // NSO virtual address
      "expect": "F3 03 01 2A",         // original bytes, required
      "set": "33 00 80 52"             // replacement bytes
    }
  ]
}

`va` is the NSO virtual address (the .text offset for subsdk0), not a raw
compressed-file offset.  The script preserves compression flags, recompresses
changed segments with raw LZ4 blocks, updates all segment file offsets and
compressed sizes, and recalculates NSO segment hashes.

Requires: pip install lz4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    import lz4.block
except ImportError as exc:
    raise SystemExit("Missing dependency: install it with `python3 -m pip install lz4`") from exc


HEADER_SIZE = 0x100
SEGMENT_NAMES = (".text", ".rodata", ".data")
SEGMENT_TABLE = 0x10
COMPRESSED_SIZE_TABLE = 0x60
HASH_TABLE = 0xA0


class PatchError(Exception):
    """A malformed NSO or a patch precondition failure."""


@dataclass
class Segment:
    name: str
    file_offset: int
    va: int
    size: int
    align_or_bss: int
    compressed_size: int
    compressed: bool
    hash_checked: bool
    original_data: bytes
    stored: bytes
    data: bytes

    @property
    def end(self) -> int:
        return self.va + self.size


@dataclass
class Nso:
    path: Path
    raw: bytes
    header: bytearray
    flags: int
    segments: list[Segment]


def hex_bytes(value: str) -> bytes:
    try:
        result = bytes.fromhex("".join(str(value).split()))
    except ValueError as exc:
        raise PatchError(f"invalid hexadecimal bytes: {value!r}") from exc
    if not result:
        raise PatchError("an empty byte string is not a valid patch")
    return result


def integer(value: str | int) -> int:
    try:
        return int(str(value), 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise PatchError(f"invalid integer: {value!r}") from exc


def read_nso(path: Path) -> Nso:
    raw = path.read_bytes()
    if len(raw) < HEADER_SIZE or raw[:4] != b"NSO0":
        raise PatchError(f"{path}: not an NSO0 file")

    header = bytearray(raw[:HEADER_SIZE])
    flags = struct.unpack_from("<I", header, 0x0C)[0]
    compressed_sizes = struct.unpack_from("<3I", header, COMPRESSED_SIZE_TABLE)
    segments: list[Segment] = []
    for index, name in enumerate(SEGMENT_NAMES):
        table_offset = SEGMENT_TABLE + index * 0x10
        file_offset, va, size, align_or_bss = struct.unpack_from(
            "<4I", header, table_offset
        )
        compressed_size = compressed_sizes[index]
        if not size or not compressed_size:
            raise PatchError(f"{path}: {name} is empty or malformed")
        if file_offset < HEADER_SIZE or file_offset + compressed_size > len(raw):
            raise PatchError(f"{path}: {name} range is outside the file")
        stored = raw[file_offset:file_offset + compressed_size]
        compressed = bool(flags & (1 << index))
        try:
            data = (lz4.block.decompress(stored, uncompressed_size=size)
                    if compressed else stored[:size])
        except Exception as exc:
            raise PatchError(f"{path}: cannot decompress {name}: {exc}") from exc
        if len(data) != size:
            raise PatchError(f"{path}: {name} has {len(data):#x} bytes; expected {size:#x}")
        hash_checked = bool(flags & (1 << (3 + index)))
        expected_hash = header[HASH_TABLE + index * 0x20: HASH_TABLE + (index + 1) * 0x20]
        if hash_checked and hashlib.sha256(data).digest() != expected_hash:
            raise PatchError(f"{path}: {name} hash does not match the NSO header")
        segments.append(Segment(name, file_offset, va, size, align_or_bss,
                                compressed_size, compressed, hash_checked, data, stored, data))
    return Nso(path, raw, header, flags, segments)


def segment_for_va(nso: Nso, va: int, size: int) -> tuple[Segment, int]:
    if size < 1:
        raise PatchError("patch data must not be empty")
    for segment in nso.segments:
        if segment.va <= va and va + size <= segment.end:
            return segment, va - segment.va
    raise PatchError(f"VA {va:#x} ({size} bytes) is outside all NSO segments")


def verify_spec_identity(nso: Nso, spec: dict) -> None:
    required_hash = spec.get("input_sha256")
    actual_hash = hashlib.sha256(nso.raw).hexdigest()
    if required_hash and str(required_hash).lower() != actual_hash:
        raise PatchError(
            f"input_sha256 mismatch: have {actual_hash}, expected {required_hash}"
        )


def apply_spec(nso: Nso, spec: dict) -> list[str]:
    verify_spec_identity(nso, spec)
    patches = spec.get("patches")
    if not isinstance(patches, list) or not patches:
        raise PatchError("spec must contain a non-empty patches list")

    mutable = {segment.name: bytearray(segment.data) for segment in nso.segments}
    log: list[str] = []
    for index, patch in enumerate(patches, 1):
        if not isinstance(patch, dict):
            raise PatchError(f"patch {index} is not an object")
        name = patch.get("name", f"patch {index}")
        if "va" not in patch or "expect" not in patch or "set" not in patch:
            raise PatchError(f"{name}: va, expect, and set are all required")
        va = integer(patch["va"])
        expected = hex_bytes(patch["expect"])
        replacement = hex_bytes(patch["set"])
        if len(expected) != len(replacement):
            raise PatchError(f"{name}: expect and set must be the same length")
        segment, offset = segment_for_va(nso, va, len(expected))
        current = bytes(mutable[segment.name][offset:offset + len(expected)])
        if current != expected:
            raise PatchError(
                f"{name}: verification failed at {va:#x}; "
                f"have {current.hex(' ')}, expected {expected.hex(' ')}"
            )
        mutable[segment.name][offset:offset + len(replacement)] = replacement
        log.append(f"{name}: {segment.name}+{offset:#x} (VA {va:#x}), {len(replacement)} bytes")

    for segment in nso.segments:
        segment.data = bytes(mutable[segment.name])
    return log


def rebuild(nso: Nso) -> bytes:
    """Rebuild the NSO and return it only after all structural checks pass."""
    header = bytearray(nso.header)
    first_file_offset = min(segment.file_offset for segment in nso.segments)
    if first_file_offset < HEADER_SIZE:
        raise PatchError("invalid initial segment offset")
    prefix = nso.raw[:first_file_offset]
    payloads: list[bytes] = []
    for index, segment in enumerate(nso.segments):
        if len(segment.data) != segment.size:
            raise PatchError(f"internal error: {segment.name} size changed")
        # Keep untouched compressed segments byte-for-byte identical.  This
        # minimizes the replacement and avoids changing the vendor's LZ4
        # encoder choices when a patch touches only one segment.
        if segment.data == segment.original_data:
            payload = segment.stored
        elif segment.compressed:
            # NSO uses raw (size-less) LZ4 blocks.  The default fast encoder
            # most closely matches the original retail NSO's size profile.
            payload = lz4.block.compress(segment.data, store_size=False)
        else:
            payload = segment.data
        if not payload:
            raise PatchError(f"internal error: empty {segment.name} payload")
        payloads.append(payload)
        struct.pack_into("<I", header, COMPRESSED_SIZE_TABLE + index * 4, len(payload))
        header[HASH_TABLE + index * 0x20: HASH_TABLE + (index + 1) * 0x20] = \
            hashlib.sha256(segment.data).digest()

    next_offset = len(prefix)
    for index, payload in enumerate(payloads):
        struct.pack_into("<I", header, SEGMENT_TABLE + index * 0x10, next_offset)
        next_offset += len(payload)
    result = bytes(header) + prefix[HEADER_SIZE:] + b"".join(payloads)

    # Reparse so a malformed file is never written by the CLI.
    with tempfile.NamedTemporaryFile(prefix="nso-verify-", suffix=".nso", delete=False) as tmp:
        verify_path = Path(tmp.name)
        tmp.write(result)
    try:
        verified = read_nso(verify_path)
        for want, got in zip(nso.segments, verified.segments):
            if want.data != got.data:
                raise PatchError(f"rebuild verification failed for {want.name}")
    finally:
        verify_path.unlink(missing_ok=True)
    return result


def list_caves(nso: Nso, minimum: int) -> list[tuple[int, int, int, str]]:
    """Report only obvious ARM64 padding; this does not claim it is injectable."""
    text = next(segment for segment in nso.segments if segment.name == ".text")
    patterns = ((b"\0\0\0\0", "zero"), (bytes.fromhex("1F2003D5"), "nop"))
    found: list[tuple[int, int, int, str]] = []
    for pattern, kind in patterns:
        offset = 0
        while offset <= len(text.data) - 4:
            if text.data[offset:offset + 4] != pattern:
                offset += 4
                continue
            start = offset
            while offset <= len(text.data) - 4 and text.data[offset:offset + 4] == pattern:
                offset += 4
            size = offset - start
            if size >= minimum:
                found.append((text.va + start, start, size, kind))
    return sorted(found, key=lambda item: (-item[2], item[0]))


def print_info(nso: Nso) -> None:
    print(f"{nso.path}: sha256={hashlib.sha256(nso.raw).hexdigest()}")
    print(f"flags={nso.flags:#x} build_id={nso.header[0x40:0x60].hex()}")
    for segment in nso.segments:
        print(f"  {segment.name:8} VA {segment.va:#010x}-{segment.end - 1:#010x} "
              f"raw={segment.size:#x} stored={segment.compressed_size:#x} "
              f"compressed={segment.compressed} hash_checked={segment.hash_checked}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("nso", type=Path, help="input NSO0 (for example, exefs/subsdk0)")
    parser.add_argument("spec", type=Path, nargs="?", help="verified JSON patch spec")
    parser.add_argument("-o", "--output", type=Path, help="new NSO output path; never defaults to the input")
    parser.add_argument("--verify", action="store_true", help="validate and report the input NSO without patching")
    parser.add_argument("--list-caves", type=lambda value: int(value, 0), metavar="MIN_BYTES",
                        help="list obvious .text padding runs (informational only)")
    parser.add_argument("--limit", type=int, default=50,
                        help="maximum padding runs printed by --list-caves (default: 50)")
    parser.add_argument("--dry-run", action="store_true", help="validate/apply/rebuild in memory but do not write")
    parser.add_argument("--force", action="store_true", help="allow overwriting an existing output file")
    args = parser.parse_args(argv)

    try:
        nso = read_nso(args.nso)
        print_info(nso)
        if args.verify:
            if args.spec or args.output or args.dry_run:
                raise PatchError("--verify cannot be combined with a spec, output, or --dry-run")
            return 0
        if args.list_caves is not None:
            if args.spec or args.output or args.dry_run:
                raise PatchError("--list-caves cannot be combined with patching options")
            if args.list_caves < 4 or args.list_caves % 4:
                raise PatchError("--list-caves MIN_BYTES must be a multiple of 4 and at least 4")
            if args.limit < 1:
                raise PatchError("--limit must be positive")
            caves = list_caves(nso, args.list_caves)
            for va, offset, size, kind in caves[:args.limit]:
                print(f"  {kind:4} padding: VA {va:#x}, .text+{offset:#x}, {size} bytes")
            if len(caves) > args.limit:
                print(f"  ... {len(caves) - args.limit} more runs omitted (raise --limit to show them)")
            return 0
        if not args.spec:
            raise PatchError("a patch spec is required unless --verify or --list-caves is used")
        if not args.dry_run and not args.output:
            raise PatchError("--output is required (the input is never overwritten)")
        if args.output and args.output.resolve() == args.nso.resolve():
            raise PatchError("refusing to overwrite the input; choose a different --output path")
        if args.output and args.output.exists() and not args.force:
            raise PatchError(f"{args.output} already exists; pass --force to replace it")

        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise PatchError("top-level JSON value must be an object")
        print(f"spec: {spec.get('name', args.spec.name)}")
        for line in apply_spec(nso, spec):
            print(f"  {line}")
        rebuilt = rebuild(nso)
        print(f"rebuilt: {len(rebuilt):,} bytes, sha256={hashlib.sha256(rebuilt).hexdigest()}")
        if args.dry_run:
            print("dry run complete; no file written")
            return 0

        assert args.output is not None
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=args.output.parent, prefix=f".{args.output.name}.", delete=False) as tmp:
            temporary_output = Path(tmp.name)
            tmp.write(rebuilt)
        try:
            os.replace(temporary_output, args.output)
        except Exception:
            temporary_output.unlink(missing_ok=True)
            raise
        print(f"wrote: {args.output}")
        return 0
    except (OSError, json.JSONDecodeError, PatchError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

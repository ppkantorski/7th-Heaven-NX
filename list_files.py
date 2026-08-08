#!/usr/bin/env python3

import os
import sys
from pathlib import Path


# Files/directories that should not be included in the output.
IGNORED_NAMES = {
    ".DS_Store",
    ".Spotlight-V100",
    ".Trashes",
    ".TemporaryItems",
    ".fseventsd",
    "Thumbs.db",
    "desktop.ini",
}


def should_ignore(path: Path) -> bool:
    """Return True if this file/directory is considered metadata/junk."""
    return path.name in IGNORED_NAMES


def format_tree(directory: Path) -> list[str]:
    """Build a neatly formatted recursive file tree."""
    lines = []

    def walk(current: Path, prefix: str = ""):
        try:
            entries = sorted(
                (
                    entry
                    for entry in current.iterdir()
                    if not should_ignore(entry)
                ),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except PermissionError:
            lines.append(f"{prefix}└── [Permission denied]")
            return

        for index, entry in enumerate(entries):
            is_last = index == len(entries) - 1
            branch = "└── " if is_last else "├── "
            next_prefix = prefix + ("    " if is_last else "│   ")

            if entry.is_dir():
                lines.append(f"{prefix}{branch}{entry.name}/")
                walk(entry, next_prefix)
            else:
                lines.append(f"{prefix}{branch}{entry.name}")

    # Put the root folder at the top.
    lines.append(f"{directory.name}/")
    walk(directory)

    return lines


def main():
    if len(sys.argv) != 3:
        print(
            "Usage:\n"
            "  python3 list_files.py /path/to/folder /path/to/output.txt"
        )
        sys.exit(1)

    source = Path(sys.argv[1]).expanduser().resolve()
    output = Path(sys.argv[2]).expanduser().resolve()

    if not source.exists():
        print(f"Error: Folder does not exist:\n  {source}")
        sys.exit(1)

    if not source.is_dir():
        print(f"Error: Not a directory:\n  {source}")
        sys.exit(1)

    # Don't accidentally include the output log if it's being created
    # inside the directory we're scanning.
    if output.parent.exists():
        output.parent.mkdir(parents=True, exist_ok=True)

    tree = format_tree(source)

    try:
        output.write_text("\n".join(tree) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"Error writing output file:\n  {e}")
        sys.exit(1)

    # Count files and directories, excluding ignored metadata.
    file_count = 0
    directory_count = 0

    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in IGNORED_NAMES]
        files[:] = [f for f in files if f not in IGNORED_NAMES]

        directory_count += len(dirs)
        file_count += len(files)

    print(f"Done.")
    print(f"Folder:      {source}")
    print(f"Output:      {output}")
    print(f"Directories: {directory_count}")
    print(f"Files:       {file_count}")


if __name__ == "__main__":
    main()
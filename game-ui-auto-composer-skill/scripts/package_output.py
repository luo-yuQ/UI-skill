#!/usr/bin/env python3
import shutil
import sys
from pathlib import Path

def main():
    if len(sys.argv) != 2:
        print("Usage: python package_output.py <directory>")
        raise SystemExit(1)

    directory = Path(sys.argv[1])
    if not directory.exists() or not directory.is_dir():
        print(f"Not a directory: {directory}")
        raise SystemExit(1)

    archive = shutil.make_archive(str(directory), "zip", root_dir=directory)
    print(f"Created {archive}")

if __name__ == "__main__":
    main()

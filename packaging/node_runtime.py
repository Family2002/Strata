#!/usr/bin/env python3
"""Unpack the Windows Node zip the offline folder and the desktop bundle ship.

Official nodejs.org Windows zips put node.exe next to the Visual C++ runtime DLLs it imports.
Copying only node.exe leaves a PC without the VC++ redistributable unable to start the host, and
Windows then shows its own missing-DLL dialog. This tool reads the PE import tables and extracts
node.exe plus every DLL it needs that is not part of Windows or the graphics driver. A DLL the zip
does not contain is an error: nothing is extracted and nothing is downloaded to fill the gap.

This runs in the preparation environment. The Windows PC that later runs `strata offline` never
runs it.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "packaging") not in sys.path:
    sys.path.insert(0, str(ROOT / "packaging"))

import binary_deps  # noqa: E402


class NodeRuntimeError(RuntimeError):
    """The zip cannot supply a DLL the runtime imports. Nothing was fetched to replace it."""


def _basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _find(names: list[str], wanted: str) -> str | None:
    folded = wanted.lower()
    for name in names:
        if _basename(name).lower() == folded:
            return name
    return None


def closure(names: list[str], read, start: str) -> list[str]:
    """Zip member names to extract: `start` (node.exe) and every non-system DLL it reaches.

    `read(member) -> bytes`. Raises NodeRuntimeError naming the missing DLL and the file that
    imports it. Does not talk to the network.
    """
    chosen: list[str] = []
    seen: set[str] = set()
    queue = [start]
    while queue:
        member = queue.pop(0)
        key = member.lower()
        if key in seen:
            continue
        seen.add(key)
        chosen.append(member)
        try:
            kind, imports = binary_deps.imports_bytes(read(member))
        except (ValueError, OSError) as exc:
            raise NodeRuntimeError(f"{_basename(member)}: cannot read its imports ({exc})") from None
        if kind != "pe":
            raise NodeRuntimeError(f"{_basename(member)}: not a Windows PE file, so its DLL closure cannot be checked")
        for imported in imports:
            if binary_deps._match(imported, binary_deps.WINDOWS_OS, True) or binary_deps._match(imported, binary_deps.WINDOWS_DRIVER, True):
                continue
            found = _find(names, imported)
            if found is None:
                raise NodeRuntimeError(
                    f"missing local file {_basename(imported)} (imported by {_basename(member)}). "
                    "The Node zip does not contain it. Nothing was downloaded.")
            if found.lower() not in seen:
                queue.append(found)
    return chosen


def extract(archive: Path, dest: Path) -> list[str]:
    """Extract the closure into `dest` (flat). Returns the file names written."""
    archive = Path(archive)
    dest = Path(dest)
    if not archive.is_file():
        raise NodeRuntimeError(f"missing local file {archive}")
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        start = _find(names, "node.exe")
        if start is None:
            raise NodeRuntimeError(f"{archive.name}: no node.exe inside. Nothing was downloaded.")
        members = closure(names, lambda name: zf.read(name), start)
        dest.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        for member in members:
            target = dest / _basename(member)
            target.write_bytes(zf.read(member))
            written.append(target.name)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract node.exe and the DLLs it imports from a local Node zip")
    ap.add_argument("--zip", required=True, help="a node-*-win-x64.zip already on disk")
    ap.add_argument("--dest", required=True, help="folder that receives node.exe and its DLLs")
    args = ap.parse_args(argv)
    try:
        written = extract(Path(args.zip), Path(args.dest))
    except (NodeRuntimeError, zipfile.BadZipFile, OSError) as exc:
        print(f"node runtime: {exc}", file=sys.stderr)
        return 1
    print("node runtime: " + ", ".join(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

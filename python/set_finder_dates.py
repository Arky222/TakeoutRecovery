#!/usr/bin/env python3
"""
set_finder_dates.py — Set macOS Finder creation dates from EXIF metadata.

For every media file under the given folder, reads DateTimeOriginal (or
CreateDate / TrackCreateDate / MediaCreateDate) via exiftool and sets the
macOS Finder creation date using SetFile.

Prerequisites:
  - exiftool  (brew install exiftool)
  - SetFile   (ships with Xcode Command Line Tools — xcode-select --install)

Usage:
  python3 set_finder_dates.py [root_folder] [--dry-run] [--verbose]
"""

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEDIA_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif",
    ".nef", ".tiff", ".tif", ".bmp", ".webp", ".dng",
    ".mp4", ".mov", ".3gp", ".mkv", ".avi", ".m4v", ".mp",
})

# Date keys to try, in preference order
DATE_KEYS = ("DateTimeOriginal", "CreateDate", "TrackCreateDate", "MediaCreateDate")

# Exiftool date/time format
EXIF_DT_FMT = "%Y:%m:%d %H:%M:%S"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _progress(done: int, total: int, label: str = "") -> None:
    """Print an in-place progress bar to stderr. No-op if stderr is not a TTY."""
    if not sys.stderr.isatty():
        return
    width = 25
    pct = done / total if total else 1.0
    filled = int(width * pct)
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    suffix = f"  {label[:45]}" if label else ""
    end = "\n" if done >= total else ""
    sys.stderr.write(f"\r  [{bar}] {done}/{total} ({pct:.0%}){suffix}   {end}")
    sys.stderr.flush()


def check_setfile() -> bool:
    """Return True if SetFile is available on this system."""
    try:
        result = subprocess.run(["SetFile", "--help"], capture_output=True)
        return True  # even if it exits non-zero, it exists
    except FileNotFoundError:
        return False


def read_exif_dates_batch(root: Path, verbose: bool = False) -> dict:
    """
    Run exiftool once over the whole tree and return a dict mapping
    absolute file path → datetime (UTC-aware) for every file that has
    a recognisable date tag.
    """
    if not verbose and sys.stderr.isatty():
        sys.stderr.write("  Scanning media files with exiftool (may take a minute)...\n")
        sys.stderr.flush()
    logging.info("Reading EXIF dates from all files under %s …", root)
    cmd = [
        "exiftool", "-json", "-r", "-q",
        "-ext", "jpg", "-ext", "jpeg", "-ext", "png", "-ext", "gif",
        "-ext", "heic", "-ext", "heif", "-ext", "nef", "-ext", "tiff",
        "-ext", "tif", "-ext", "bmp", "-ext", "webp", "-ext", "dng",
        "-ext", "mp4", "-ext", "mov", "-ext", "3gp", "-ext", "mkv",
        "-ext", "avi", "-ext", "m4v", "-ext", "mp",
        "-DateTimeOriginal", "-CreateDate", "-TrackCreateDate", "-MediaCreateDate",
        "-d", EXIF_DT_FMT,
        str(root),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        logging.critical("exiftool not found — install with:  brew install exiftool")
        sys.exit(1)

    if not result.stdout.strip():
        logging.warning("exiftool returned no output — no media files found or all lack EXIF dates")
        return {}

    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logging.error("Failed to parse exiftool JSON output: %s", exc)
        return {}

    date_map: dict = {}
    for entry in entries:
        source = entry.get("SourceFile")
        if not source:
            continue
        for key in DATE_KEYS:
            val = entry.get(key, "")
            if not val or val.startswith("0000"):
                continue
            # Strip any timezone suffix exiftool may append (e.g. "+02:00")
            val = val[:19]
            try:
                # Dates were stored as UTC by recover_metadata.py
                dt = datetime.strptime(val, EXIF_DT_FMT).replace(tzinfo=timezone.utc)
                date_map[source] = dt
                break
            except ValueError:
                continue

    logging.info("Found EXIF dates for %d files", len(date_map))
    return date_map


def set_finder_creation_date(file_path: str, dt: datetime, dry_run: bool) -> bool:
    """
    Set the Finder creation date (birth time) of file_path using SetFile.
    dt should be a timezone-aware datetime; it is converted to local time
    before being passed to SetFile.
    """
    local_dt = dt.astimezone()  # convert UTC → system local timezone
    date_str = local_dt.strftime("%m/%d/%Y %H:%M:%S")
    if dry_run:
        logging.info("[dry-run] SetFile -d '%s'  %s", date_str, Path(file_path).name)
        return True
    result = subprocess.run(
        ["SetFile", "-d", date_str, file_path],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True
    logging.warning("SetFile failed for %s: %s", Path(file_path).name, result.stderr.strip())
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set macOS Finder creation dates from EXIF DateTimeOriginal.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Root folder to process (default: current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print per-file progress",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    root = Path(args.root).resolve()
    if not root.is_dir():
        logging.critical("Not a directory: %s", root)
        sys.exit(1)

    if not check_setfile():
        logging.critical(
            "SetFile not found.\n"
            "Install Xcode Command Line Tools:  xcode-select --install"
        )
        sys.exit(1)

    if args.dry_run:
        logging.info("=== DRY RUN — no changes will be made ===")

    # Step 1: Collect all EXIF dates in one fast batch call
    date_map = read_exif_dates_batch(root, verbose=args.verbose)
    if not date_map:
        logging.info("Nothing to do.")
        return

    # Step 2: Apply SetFile for each file
    total = len(date_map)
    ok = skipped = failed = 0
    for i, (file_path, dt) in enumerate(date_map.items(), start=1):
        if not args.verbose:
            _progress(i, total, Path(file_path).name)
        if set_finder_creation_date(file_path, dt, args.dry_run):
            ok += 1
            logging.debug("  OK  %s → %s", Path(file_path).name, dt.strftime(EXIF_DT_FMT))
        else:
            failed += 1

    total = ok + failed + skipped
    logging.info(
        "\nDone. %d files processed — %d OK, %d failed",
        total, ok, failed,
    )
    if args.dry_run:
        logging.info("(dry-run — no changes made)")


if __name__ == "__main__":
    main()

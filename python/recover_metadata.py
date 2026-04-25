#!/usr/bin/env python3
"""
recover_metadata.py — Google Photos Takeout metadata recovery tool.

Reads .supplemental*.json sidecar files produced by Google Takeout,
writes the stored metadata (date/time, GPS, description) into the
corresponding media file using exiftool, then removes the JSON sidecar
only if the write succeeded.

Also handles "-editada" (edited) copies that have no sidecar of their
own by borrowing metadata from the matching original file.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Matches every truncation variant produced by Google Takeout:
#   .supplemental-metadata.json  .supplemental-metadat.json  ...
#   .supplemental-me.json  .suppl.json  .supp.json  .su.json
#   Also handles Google Takeout duplicate suffix: supplemental-metadata(1).json
SIDECAR_RE = re.compile(r"\.su[a-z-]*(?:\(\d+\))?\.json$", re.IGNORECASE)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mp", ".3gp", ".mkv", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".nef",
                    ".tiff", ".tif", ".bmp", ".webp"}

EDITED_SUFFIX = "-editada"

# Return values from run_exiftool
EXIF_OK = "ok"
EXIF_FAILED = "failed"
EXIF_UNSUPPORTED = "unsupported"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool, log_file: Optional[str]) -> None:
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        handlers=handlers,
    )


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


def ts_to_exif(timestamp: str) -> str:
    """Convert a Unix timestamp string to exiftool's 'YYYY:MM:DD HH:MM:SS' format (UTC)."""
    dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    return dt.strftime("%Y:%m:%d %H:%M:%S")

def is_nonzero_gps(lat: float, lon: float) -> bool:
    return not (lat == 0.0 and lon == 0.0)


def find_file_icase(directory: Path, name: str) -> Optional[Path]:
    """
    Find a file in *directory* whose name matches *name* case-insensitively.
    Falls back to prefix matching to handle cases where both the JSON title
    and the media filename were truncated by the filesystem at different lengths
    (e.g. title='Screenshot...com.google.android.keep.png' but the actual file
    is 'Screenshot...com.google.a.png'). The file with the longest matching
    prefix (same extension) is returned.
    """
    name_lower = name.lower()
    name_path = Path(name)
    name_ext = name_path.suffix.lower()
    name_stem_lower = name_path.stem.lower()

    best: Optional[Path] = None
    best_len = 0
    try:
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            entry_lower = entry.name.lower()
            # Exact match — return immediately
            if entry_lower == name_lower:
                return entry
            # Prefix match: same extension, and one name is a prefix of the other
            if entry.suffix.lower() == name_ext:
                entry_stem_lower = entry.stem.lower()
                shorter, longer = sorted(
                    [entry_stem_lower, name_stem_lower], key=len
                )
                if longer.startswith(shorter) and len(shorter) > best_len:
                    best = entry
                    best_len = len(shorter)
    except PermissionError:
        pass
    if best is not None:
        logging.debug("  Prefix-matched '%s' → '%s'", name, best.name)
    return best


def strip_sidecar_suffix(json_path: Path) -> Optional[str]:
    """
    Remove the .supple*.json suffix from a JSON filename to get the
    bare media filename, e.g.:
        IMG_001.jpg.supplemental-metada.json  →  IMG_001.jpg
        PXL_20231228.MP.jpg.suppl.json        →  PXL_20231228.MP.jpg
    Returns None if the pattern doesn't match.
    """
    name = json_path.name
    m = SIDECAR_RE.search(name)
    if m:
        return name[: m.start()]
    # Content-based fallback files have no sidecar suffix to strip;
    # the title field is used directly so this returning None is fine.
    return None


def build_exiftool_args(meta: dict, media_path: Path) -> List[str]:
    """
    Build the exiftool argument list for writing metadata into *media_path*.
    Returns a list suitable for subprocess (without the leading 'exiftool').
    """
    args: List[str] = ["-overwrite_original", "-quiet"]

    # Date / time
    taken = meta.get("photoTakenTime", {}).get("timestamp")
    if taken:
        exif_date = ts_to_exif(taken)
        args += [
            f"-DateTimeOriginal={exif_date}",
            f"-CreateDate={exif_date}",
        ]
        ext = media_path.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            args += [
                f"-TrackCreateDate={exif_date}",
                f"-MediaCreateDate={exif_date}",
            ]

    # GPS — prefer geoDataExif, fall back to geoData
    geo = meta.get("geoDataExif") or meta.get("geoData") or {}
    lat = geo.get("latitude", 0.0)
    lon = geo.get("longitude", 0.0)
    alt = geo.get("altitude", 0.0)
    if is_nonzero_gps(lat, lon):
        args += [
            f"-GPSLatitude={abs(lat)}",
            f"-GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
            f"-GPSLongitude={abs(lon)}",
            f"-GPSLongitudeRef={'E' if lon >= 0 else 'W'}",
        ]
        if alt != 0.0:
            args += [
                f"-GPSAltitude={abs(alt)}",
                f"-GPSAltitudeRef={'Above Sea Level' if alt >= 0 else 'Below Sea Level'}",
            ]

    # Description
    desc = meta.get("description", "").strip()
    if desc:
        args.append(f"-Description={desc}")
        args.append(f"-Comment={desc}")

    args.append(str(media_path))
    return args


def run_exiftool(args: List[str], dry_run: bool) -> str:
    """
    Call exiftool with *args*.  Returns EXIF_OK, EXIF_FAILED, or EXIF_UNSUPPORTED.
    Always passes -m (ignore minor errors, e.g. IFD0 out of sequence).
    In dry-run mode always returns EXIF_OK without executing.
    """
    if dry_run:
        logging.debug("  [dry-run] exiftool %s", " ".join(args))
        return EXIF_OK

    def _run(cmd_args: List[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["exiftool", "-m"] + cmd_args, capture_output=True, text=True
            )
        except FileNotFoundError:
            logging.critical("exiftool not found. Install it with:  brew install exiftool")
            sys.exit(1)

    result = _run(args)
    if result.returncode == 0:
        return EXIF_OK

    stderr = result.stderr.strip()

    # Unsupported format (e.g. AVI/RIFF) — cannot write, keep JSON
    if "Can't currently write" in stderr:
        logging.warning("  Unsupported format, metadata not written: %s", args[-1])
        return EXIF_UNSUPPORTED

    # File has wrong extension but is actually JPEG (Google re-encoded RAW).
    # Use a unique temp name to avoid colliding with an existing .jpg file.
    if "looks more like" in stderr:
        file_path = Path(args[-1])
        tmp_path = file_path.parent / (file_path.stem + "__tmp_recover__.jpg")
        logging.debug("  Retrying as JPEG (temp rename): %s", file_path.name)
        os.rename(file_path, tmp_path)
        try:
            retry = _run(args[:-1] + [str(tmp_path)])
        finally:
            os.rename(tmp_path, file_path)  # always rename back
        if retry.returncode == 0:
            return EXIF_OK
        stderr = retry.stderr.strip()

    # Corrupted IFD / GPS data — strip all EXIF first to clear the corrupted
    # IFD structure, then do a clean write. This is the only reliable fix for
    # "Error reading OtherImageStart", "Can't read GPS data", etc.
    _IFD_KEYWORDS = ("GPS", "IFD", "OtherImage", "ExifIFD")
    if any(k in stderr for k in _IFD_KEYWORDS):
        file_path = Path(args[-1])
        logging.debug("  Stripping corrupt EXIF then retrying: %s", file_path.name)
        strip = _run(["-F", "-all=", "-overwrite_original", str(file_path)])
        if strip.returncode == 0:
            retry = _run(args)
            if retry.returncode == 0:
                return EXIF_OK
            stderr = retry.stderr.strip()
        else:
            stderr = strip.stderr.strip()

    logging.error("  exiftool failed (rc=%d): %s", result.returncode, stderr)
    return EXIF_FAILED


def set_finder_creation_date(media_path: Path, timestamp: int, dry_run: bool) -> bool:
    """
    Set the macOS Finder creation date (birth time) using SetFile.
    Returns True on success, False if SetFile is not available or fails.
    Available on macOS with Xcode Command Line Tools installed.
    """
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone()
    date_str = dt.strftime("%m/%d/%Y %H:%M:%S")
    if dry_run:
        logging.debug("  [dry-run] SetFile -d '%s' %s", date_str, media_path)
        return True
    try:
        result = subprocess.run(
            ["SetFile", "-d", date_str, str(media_path)],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            logging.debug("  SetFile creation date set to %s for %s", date_str, media_path.name)
            return True
        logging.warning("  SetFile failed: %s", result.stderr.strip())
        return False
    except FileNotFoundError:
        logging.debug("  SetFile not found (install Xcode Command Line Tools to set Finder creation dates)")
        return False


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

class Stats:
    def __init__(self) -> None:
        self.processed = 0       # JSONs successfully applied
        self.failed = 0          # JSONs where exiftool failed
        self.unsupported = 0     # JSONs for formats exiftool cannot write (e.g. AVI)
        self.skipped = 0         # JSONs without photoTakenTime (non-metadata)
        self.unmatched = 0       # JSONs where media file was not found
        self.json_deleted = 0    # JSON files removed after success
        self.editada_ok = 0      # edited copies updated
        self.editada_skip = 0    # edited copies with no metadata source


def process_sidecar(
    json_path: Path,
    dry_run: bool,
    metadata_cache: Dict[str, dict],  # absolute media path → parsed metadata
    stats: Stats,
) -> None:
    """Parse one sidecar JSON, find the media file, write metadata, clean up."""

    # --- Parse JSON ---
    try:
        with open(json_path, encoding="utf-8") as f:
            meta = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logging.warning("Cannot read %s: %s", json_path, exc)
        stats.skipped += 1
        return

    # Skip non-metadata JSONs (album files, exported variants, etc.)
    if "photoTakenTime" not in meta:
        logging.debug("Skipping (no photoTakenTime): %s", json_path.name)
        stats.skipped += 1
        return

    directory = json_path.parent

    # --- Find matching media file ---
    media_path: Optional[Path] = None

    # Strategy 1: use the 'title' field (most reliable)
    title = meta.get("title", "").strip()
    if title:
        media_path = find_file_icase(directory, title)

    # Strategy 2: strip .supple*.json suffix
    if media_path is None:
        bare = strip_sidecar_suffix(json_path)
        if bare:
            media_path = find_file_icase(directory, bare)

    if media_path is None:
        logging.warning("No media file found for: %s", json_path.name)
        stats.unmatched += 1
        return

    # Skip if the resolved path is itself a JSON (shouldn't happen but be safe)
    if media_path.suffix.lower() == ".json":
        logging.warning("Resolved media file is a JSON — skipping: %s", media_path.name)
        stats.unmatched += 1
        return

    logging.debug("Processing %s → %s", json_path.name, media_path.name)

    # --- Build and run exiftool ---
    args = build_exiftool_args(meta, media_path)
    result = run_exiftool(args, dry_run)

    if result == EXIF_OK:
        stats.processed += 1
        # Cache metadata so edited copies can reuse it
        metadata_cache[str(media_path)] = meta
        # Delete the sidecar
        if not dry_run:
            try:
                json_path.unlink()
                stats.json_deleted += 1
                logging.debug("Deleted sidecar: %s", json_path.name)
            except OSError as exc:
                logging.error("Could not delete %s: %s", json_path.name, exc)
        else:
            stats.json_deleted += 1  # count "would delete" in dry-run
    elif result == EXIF_UNSUPPORTED:
        stats.unsupported += 1
        # Even though exiftool can't write internal metadata, we can still set
        # the Finder creation date using SetFile (macOS only).
        ts_str = meta.get("photoTakenTime", {}).get("timestamp", "")
        if ts_str:
            try:
                ts = int(ts_str)
                if set_finder_creation_date(media_path, ts, dry_run):
                    # Delete the sidecar since we've done what we can
                    if not dry_run:
                        try:
                            json_path.unlink()
                            stats.json_deleted += 1
                            logging.debug("Deleted sidecar (after SetFile): %s", json_path.name)
                        except OSError as exc:
                            logging.error("Could not delete %s: %s", json_path.name, exc)
                    else:
                        stats.json_deleted += 1
            except ValueError:
                pass
    else:
        stats.failed += 1


def process_editada(
    directory: Path,
    dry_run: bool,
    metadata_cache: Dict[str, dict],
    stats: Stats,
) -> None:
    """
    Find all *-editada.* files in *directory* and apply metadata sourced
    from the original file's cached metadata (or its sidecar if still present).
    """
    try:
        entries = list(directory.iterdir())
    except PermissionError:
        return

    for entry in entries:
        if not entry.is_file():
            continue
        stem = entry.stem          # e.g.  "IMG_001-editada"
        suffix = entry.suffix      # e.g.  ".jpg"
        if not stem.endswith(EDITED_SUFFIX):
            continue

        original_stem = stem[: -len(EDITED_SUFFIX)]
        original_name = original_stem + suffix  # e.g.  "IMG_001.jpg"

        # Look up metadata from cache
        original_path = find_file_icase(directory, original_name)
        meta: Optional[dict] = None

        if original_path and str(original_path) in metadata_cache:
            meta = metadata_cache[str(original_path)]
        else:
            # Try to find and read the sidecar directly (in case Pass 1 failed it)
            for candidate in directory.iterdir():
                if (
                    candidate.is_file()
                    and candidate.suffix.lower() == ".json"
                    and SIDECAR_RE.search(candidate.name)
                ):
                    bare = strip_sidecar_suffix(candidate)
                    if bare and bare.lower() == original_name.lower():
                        try:
                            with open(candidate, encoding="utf-8") as f:
                                parsed = json.load(f)
                            if "photoTakenTime" in parsed:
                                meta = parsed
                                break
                        except (json.JSONDecodeError, OSError):
                            pass

        if meta is None:
            # JSON was already deleted in a previous run — copy EXIF directly
            # from the original file if it exists.
            if original_path and original_path.exists():
                logging.debug(
                    "Copying EXIF from original: %s → %s",
                    original_path.name, entry.name,
                )
                copy_args = [
                    "-tagsFromFile", str(original_path),
                    "-DateTimeOriginal", "-CreateDate",
                    "-GPSLatitude", "-GPSLatitudeRef",
                    "-GPSLongitude", "-GPSLongitudeRef",
                    "-GPSAltitude", "-GPSAltitudeRef",
                    "-Description", "-Comment",
                    "-overwrite_original", "-quiet",
                    str(entry),
                ]
                if run_exiftool(copy_args, dry_run) == EXIF_OK:
                    stats.editada_ok += 1
                    continue
            logging.warning("No metadata source for edited copy: %s", entry.name)
            stats.editada_skip += 1
            continue

        logging.debug("Applying metadata to edited copy: %s", entry.name)
        args = build_exiftool_args(meta, entry)
        if run_exiftool(args, dry_run) == EXIF_OK:
            stats.editada_ok += 1
        else:
            stats.editada_skip += 1


def walk_and_process(root: Path, dry_run: bool, stats: Stats, verbose: bool = False) -> None:
    """
    Two-pass walk:
      Pass 1 — process all sidecar JSONs in every directory.
      Pass 2 — handle -editada copies using metadata gathered in Pass 1.
    """
    metadata_cache: Dict[str, dict] = {}

    # Collect directories first so we can do two passes per directory
    all_dirs: List[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames.sort()  # deterministic order
        all_dirs.append(Path(dirpath))

    logging.info("Found %d directories under %s", len(all_dirs), root)

    # Pre-collect all JSON paths so we know the total for the progress bar
    dir_jsons: List[tuple] = []
    for directory in all_dirs:
        try:
            jsons = sorted(
                p for p in directory.iterdir()
                if p.is_file() and p.suffix.lower() == ".json"
            )
        except PermissionError:
            logging.warning("Cannot read directory: %s", directory)
            jsons = []
        dir_jsons.append((directory, jsons))

    total_json = sum(len(jsons) for _, jsons in dir_jsons)
    done_json = 0

    # --- Pass 1: process sidecar JSONs ---
    logging.info("Pass 1: processing %d JSON files...", total_json)
    for directory, all_json in dir_jsons:
        matched_by_pattern: set = set()
        for json_path in all_json:
            done_json += 1
            if not verbose:
                _progress(done_json, total_json, json_path.name)
            if SIDECAR_RE.search(json_path.name):
                matched_by_pattern.add(json_path)
                process_sidecar(json_path, dry_run, metadata_cache, stats)

        # Content-based fallback: catch JSONs whose name was truncated so
        # far that the '.su...' sidecar pattern is completely gone
        # (e.g. Screenshot...com.google..json).
        # Only process if the JSON has photoTakenTime and title points to a
        # real media file in the same directory.
        for json_path in all_json:
            if json_path in matched_by_pattern:
                continue
            try:
                with open(json_path, encoding="utf-8") as f:
                    probe = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            if "photoTakenTime" not in probe:
                continue
            title = probe.get("title", "").strip()
            if not title:
                continue
            candidate = find_file_icase(directory, title)
            if candidate is None or candidate.suffix.lower() == ".json":
                continue
            logging.debug("Content-match fallback: %s → %s", json_path.name, candidate.name)
            process_sidecar(json_path, dry_run, metadata_cache, stats)

    if not verbose:
        _progress(total_json, total_json)  # ensure bar reaches 100%

    # --- Pass 2: apply metadata to -editada copies ---
    logging.info("Pass 2: applying metadata to edited copies (*%s.*)...", EDITED_SUFFIX)
    for directory in all_dirs:
        process_editada(directory, dry_run, metadata_cache, stats)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="recover_metadata.py",
        description=(
            "Recover Google Photos metadata from Takeout sidecar JSON files.\n"
            "Writes date/time, GPS and description into each media file via exiftool,\n"
            "then removes the JSON sidecar on success."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Root folder to scan (default: current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate all operations without writing or deleting any files.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print each file as it is processed (DEBUG level output).",
    )
    parser.add_argument(
        "--log",
        metavar="FILE",
        help="Write log output to FILE in addition to stdout.",
    )
    args = parser.parse_args()

    setup_logging(args.verbose, args.log)

    root = Path(args.root)
    if not root.is_dir():
        logging.critical("Root folder not found: %s", root)
        sys.exit(1)

    if args.dry_run:
        logging.info("*** DRY-RUN MODE — no files will be modified or deleted ***")

    stats = Stats()
    walk_and_process(root, args.dry_run, stats, verbose=args.verbose)

    # --- Summary ---
    dry_label = " (would delete)" if args.dry_run else ""
    print()
    print("=" * 56)
    print("  SUMMARY")
    print("=" * 56)
    print(f"  Sidecars processed (metadata written):     {stats.processed}")
    print(f"  Sidecar JSONs deleted{dry_label}:         {stats.json_deleted}")
    print(f"  Sidecars failed (exiftool error):          {stats.failed}")
    print(f"  Sidecars unsupported format (kept):        {stats.unsupported}")
    print(f"  Sidecars skipped (no media file):          {stats.unmatched}")
    print(f"  Sidecars skipped (non-metadata JSON):      {stats.skipped}")
    print(f"  Edited copies updated:                     {stats.editada_ok}")
    print(f"  Edited copies skipped (no source):         {stats.editada_skip}")
    print("=" * 56)

    if stats.failed > 0 or stats.unmatched > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()

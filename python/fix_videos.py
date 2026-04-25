#!/usr/bin/env python3
"""
fix_videos.py — Fix video file issues in Google Photos Takeout archives.

Two operations (can be combined):

  --rename-mp
      Rename .MP files (Google Motion Photo video clips) to .mp4.
      Every .MP in a Google Takeout is a valid MP4 container; the unusual
      extension just prevents most players from recognising them.
      Each .MP is the short video component of a Motion Photo — it is
      always accompanied by a .jpg still (the actual photo). Both files
      are kept; only the video clip is renamed.

  --fix-vp9
      Re-encode VP9-coded MP4 files to H.264 (libx264) using ffmpeg.
      VP9 inside an MP4 container is non-standard and not supported by
      QuickTime or any Apple software — these files play audio but show
      no video on macOS. Re-encoding to H.264 fixes this permanently.
      Requires ffmpeg (brew install ffmpeg).

Usage:
  python3 fix_videos.py [ROOT_FOLDER] [--rename-mp] [--fix-vp9]
                        [--dry-run] [--verbose] [--crf N]
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Quality setting for H.264 re-encoding (0=lossless … 51=worst).
# 18 is visually near-lossless. 23 is ffmpeg default.
DEFAULT_CRF = 18


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

def _progress(done: int, total: int, label: str = "") -> None:
    """In-place progress bar on stderr. No-op when stderr is not a TTY."""
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
        return True
    except FileNotFoundError:
        return False


def get_video_codec(file_path: Path) -> Optional[str]:
    """Return the video CompressorID string via exiftool, or None on failure."""
    try:
        result = subprocess.run(
            ["exiftool", "-q", "-CompressorID", str(file_path)],
            capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            if "Compressor ID" in line:
                return line.split(":", 1)[1].strip().lower()
    except FileNotFoundError:
        pass
    return None


# ---------------------------------------------------------------------------
# Operation 1: rename .MP → .mp4
# ---------------------------------------------------------------------------

def rename_mp_files(root: Path, dry_run: bool, verbose: bool) -> None:
    """Rename all .MP files under root to .mp4."""
    mp_files: List[Path] = sorted(root.rglob("*.[Mm][Pp]"))
    # Filter to only files whose suffix is exactly .MP or .mp (not .mp4 etc.)
    mp_files = [p for p in mp_files if p.suffix.lower() == ".mp"]

    total = len(mp_files)
    if total == 0:
        print("No .MP files found.")
        return

    print(f"Found {total} .MP file(s) to rename.")
    ok = skipped = failed = 0

    for i, src in enumerate(mp_files, start=1):
        dst = src.with_suffix(".mp4")
        label = src.name

        if not verbose:
            _progress(i, total, label)

        if dst.exists():
            if verbose:
                print(f"  SKIP (target exists): {src.name} → {dst.name}")
            skipped += 1
            continue

        if verbose:
            print(f"  {'[dry-run] ' if dry_run else ''}RENAME  {src.name} → {dst.name}")

        if not dry_run:
            try:
                os.rename(src, dst)
                ok += 1
            except OSError as exc:
                print(f"  ERROR: {src.name}: {exc}", file=sys.stderr)
                failed += 1
        else:
            ok += 1

    if not verbose:
        _progress(total, total)

    label = "(would rename)" if dry_run else "renamed"
    print(f"\nDone — {ok} {label}, {skipped} skipped (target existed), {failed} failed.")


# ---------------------------------------------------------------------------
# Operation 2: re-encode VP9 → H.264
# ---------------------------------------------------------------------------

def collect_vp9_files(root: Path, verbose: bool) -> List[Path]:
    """
    Walk root and return all MP4 files whose video track is VP9.
    Uses a single batch exiftool call for speed.
    """
    import json

    print("Scanning for VP9-encoded MP4 files (this may take a moment)…")

    # Collect all .mp4 / .MP4 files
    all_mp4 = [
        str(p) for p in root.rglob("*")
        if p.suffix.lower() in {".mp4", ".mp"}
    ]
    if not all_mp4:
        return []

    # Batch exiftool read
    cmd = ["exiftool", "-json", "-q", "-CompressorID"] + all_mp4
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("ERROR: exiftool not found — install with: brew install exiftool",
              file=sys.stderr)
        sys.exit(1)

    try:
        entries = json.loads(result.stdout)
    except Exception:
        return []

    vp9_files = []
    for entry in entries:
        codec = entry.get("CompressorID", "").lower()
        if "vp09" in codec or "vp9" in codec:
            vp9_files.append(Path(entry["SourceFile"]))

    return sorted(vp9_files)


def reencode_vp9(root: Path, dry_run: bool, verbose: bool, crf: int) -> None:
    """Re-encode VP9 MP4 files to H.264 in-place using ffmpeg."""
    if not check_ffmpeg():
        if dry_run:
            print("  WARNING: ffmpeg not found (brew install ffmpeg) — continuing dry run anyway.")
        else:
            print(
                "ERROR: ffmpeg not found.\n"
                "Install it with:  brew install ffmpeg\n"
                "Then re-run this script with --fix-vp9.",
                file=sys.stderr,
            )
            sys.exit(1)

    vp9_files = collect_vp9_files(root, verbose)
    total = len(vp9_files)

    if total == 0:
        print("No VP9-encoded MP4 files found.")
        return

    print(f"Found {total} VP9-encoded MP4 file(s) to re-encode (CRF {crf}).")
    if not dry_run:
        print("  Note: re-encoding is lossy but at CRF 18 quality loss is imperceptible.")

    ok = failed = 0

    for i, src in enumerate(vp9_files, start=1):
        if not verbose:
            _progress(i, total, src.name)

        if dry_run:
            if verbose:
                print(f"  [dry-run] REENCODE  {src}")
            ok += 1
            continue

        # Write to a temp file alongside the original, then replace on success
        tmp = src.with_name(src.stem + "__tmp_h264__.mp4")

        if verbose:
            print(f"  Re-encoding: {src.name}")

        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(src),
                    "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
                    "-c:a", "copy",          # keep original audio untouched
                    "-movflags", "+faststart",  # web-friendly moov placement
                    str(tmp),
                ],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr else "unknown error")

            # Replace original with re-encoded file
            os.replace(tmp, src)
            ok += 1
            if verbose:
                print(f"    OK  {src.name}")

        except Exception as exc:
            print(f"\n  ERROR re-encoding {src.name}: {exc}", file=sys.stderr)
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            failed += 1

    if not verbose:
        _progress(total, total)

    label = "(would re-encode)" if dry_run else "re-encoded"
    print(f"\nDone — {ok} {label}, {failed} failed.")
    if failed > 0:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fix_videos.py",
        description=(
            "Fix video file issues from Google Photos Takeout archives.\n\n"
            "  --rename-mp   Rename .MP Motion Photo clips to .mp4\n"
            "  --fix-vp9     Re-encode VP9 MP4s to H.264 for macOS compatibility\n\n"
            "Both options can be used together."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Root folder to process (default: current directory)",
    )
    parser.add_argument(
        "--rename-mp",
        action="store_true",
        help="Rename .MP Google Motion Photo clips to .mp4",
    )
    parser.add_argument(
        "--fix-vp9",
        action="store_true",
        help="Re-encode VP9-coded MP4 files to H.264 using ffmpeg",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making any changes",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print each file as it is processed; disables the progress bar",
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=DEFAULT_CRF,
        metavar="N",
        help=f"H.264 quality for --fix-vp9 (0=lossless, 51=worst, default: {DEFAULT_CRF})",
    )
    args = parser.parse_args()

    if not args.rename_mp and not args.fix_vp9:
        parser.error("Specify at least one operation: --rename-mp and/or --fix-vp9")

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"ERROR: Not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("=== DRY RUN — no changes will be made ===")

    if args.rename_mp:
        print("\n--- Renaming .MP files to .mp4 ---")
        rename_mp_files(root, args.dry_run, args.verbose)

    if args.fix_vp9:
        print("\n--- Re-encoding VP9 files to H.264 ---")
        reencode_vp9(root, args.dry_run, args.verbose, args.crf)


if __name__ == "__main__":
    main()

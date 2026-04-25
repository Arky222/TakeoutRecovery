# Google Photos Takeout Metadata Recovery

Two Python utilities that restore metadata from a **Google Takeout** export:

| Script | What it does |
|---|---|
| `python/recover_metadata.py` | Reads the `.supplemental*.json` sidecar files, writes date/time, GPS and description into each media file via **exiftool**, then deletes each sidecar on success. |
| `python/set_finder_dates.py` | Reads `DateTimeOriginal` from already-processed media files and sets the **macOS Finder creation date** via `SetFile`, so Finder and Spotlight show the original photo date. |

Run `python/recover_metadata.py` first, then `python/set_finder_dates.py`.

---

## Requirements

### Python 3.6 or later

No third-party packages — only the standard library is used.

```bash
python3 --version
```

### exiftool

Used by both scripts to read and write EXIF metadata.

```bash
# macOS
brew install exiftool

# Linux (Debian/Ubuntu)
sudo apt install libimage-exiftool-perl

# Verify
exiftool -ver
```

### SetFile  *(macOS only — required for `python/set_finder_dates.py`)*

Ships with **Xcode Command Line Tools**. If you have Xcode or have run `git` before, it is probably already installed.

```bash
xcode-select --install   # install if missing
SetFile --help           # verify
```

> `SetFile` is not available on Linux. `python/set_finder_dates.py` will exit with an error if it is missing.

---

## Supported file types

Both scripts handle the following extensions (case-insensitive):

| Category | Extensions |
|---|---|
| Photos | `.jpg` `.jpeg` `.png` `.gif` `.heic` `.heif` `.webp` `.bmp` |
| RAW images | `.nef` `.dng` `.tiff` `.tif` |
| Videos | `.mp4` `.mov` `.m4v` `.mkv` `.3gp` `.avi` `.mp` |

> **Note on AVI files:** exiftool cannot write metadata inside RIFF AVI containers. `python/recover_metadata.py` will set the Finder creation date via `SetFile` instead and still delete the sidecar.

---

## Script 1 — `python/recover_metadata.py`

### What it does

1. Recursively walks the root folder.
2. Finds every sidecar JSON in all truncated-name variants Google Takeout produces:
   `.supplemental-metadata.json`, `.supplemental-metadat.json`, `.suppl.json`, `.supp.json`, `.su.json`, and duplicates like `.supplemental-metadata(1).json`.
3. Matches each JSON to its media file using the `title` field inside the JSON, with a filename-based fallback.
4. Writes metadata via `exiftool`:
   - **Date/time** — `DateTimeOriginal`, `CreateDate` (+ `TrackCreateDate`, `MediaCreateDate` for video)
   - **GPS** — `GPSLatitude`, `GPSLongitude`, `GPSAltitude` (only when real coordinates exist)
   - **Description** — `Description` / `Comment` (only when non-empty)
5. Deletes each sidecar JSON only after a confirmed successful write.
6. **Edited copies** (`-editada` suffix) inherit metadata from their matching original.
7. Handles special cases automatically:
   - RAW files re-encoded as JPEG (wrong extension) — temp-renamed for the write, then restored
   - Corrupted EXIF IFD structures — stripped clean before rewriting
   - Deeply truncated JSON names — matched by reading the JSON content

### Usage

```
python3 python/recover_metadata.py [ROOT_FOLDER] [OPTIONS]
```

If `ROOT_FOLDER` is omitted the **current directory** is used.

### Options

| Option | Description |
|---|---|
| `ROOT_FOLDER` | Path to the folder to process (default: current directory) |
| `--dry-run` | Simulate everything without writing or deleting any file |
| `--verbose` / `-v` | Print each file as it is processed; disables the progress bar |
| `--log FILE` | Write log output to `FILE` in addition to the terminal |
| `-h` / `--help` | Show help and exit |

### Examples

```bash
# Always do a dry run first
python3 python/recover_metadata.py /path/to/Takeout/Google\ Fotos --dry-run

# Process with a progress bar (default)
python3 python/recover_metadata.py /path/to/Takeout/Google\ Fotos

# Verbose per-file output + log file
python3 python/recover_metadata.py /path/to/Takeout/Google\ Fotos --verbose --log recovery.log

# Process a single year folder
python3 python/recover_metadata.py "GoogleFotos/Fotos del 2023"

# Run from inside the archive folder (uses current directory)
cd /path/to/Takeout/Google\ Fotos
python3 /path/to/python/recover_metadata.py
```

### Progress bar

When running without `--verbose`, an in-place progress bar is shown:

```
  [████████████░░░░░░░░░░░░░] 4500/7920 (57%)  IMG_20180904_123456.jpg
```

### Output summary

```
========================================================
  SUMMARY
========================================================
  Sidecars processed (metadata written):     7883
  Sidecar JSONs deleted:                     7884
  Sidecars failed (exiftool error):             0
  Sidecars unsupported format (kept):           1
  Sidecars skipped (no media file):             7
  Sidecars skipped (non-metadata JSON):        14
  Edited copies updated:                       37
  Edited copies skipped (no source):            0
========================================================
```

Exit code `0` = clean run. Exit code `2` = one or more failures or unmatched sidecars.

---

## Script 2 — `python/set_finder_dates.py`

### What it does

Reads `DateTimeOriginal` (or `CreateDate` / `TrackCreateDate` / `MediaCreateDate`) from every media file in the folder tree using a single batch `exiftool` call, then sets the **macOS Finder creation date** on each file using `SetFile`.

This makes Finder, Spotlight and the macOS Photos app show the original capture date rather than the date you copied the files from the Takeout archive.

Run this **after** `python/recover_metadata.py` has written the EXIF dates.

### Usage

```
python3 python/set_finder_dates.py [ROOT_FOLDER] [OPTIONS]
```

If `ROOT_FOLDER` is omitted the **current directory** is used.

### Options

| Option | Description |
|---|---|
| `ROOT_FOLDER` | Path to the folder to process (default: current directory) |
| `--dry-run` | Show what would be done without making any changes |
| `--verbose` / `-v` | Print each file as it is processed; disables the progress bar |
| `-h` / `--help` | Show help and exit |

### Examples

```bash
# Dry run first
python3 python/set_finder_dates.py /path/to/Takeout/Google\ Fotos --dry-run

# Apply to all files
python3 python/set_finder_dates.py /path/to/Takeout/Google\ Fotos

# Run from inside the archive folder
cd /path/to/Takeout/Google\ Fotos
python3 /path/to/python/set_finder_dates.py
```

### Progress bar

```
  Scanning media files with exiftool (may take a minute)...
  [████████████████████░░░░░] 12400/15047 (82%)  VID_20190714_183020.mp4
```

---

## Recommended workflow

```bash
# 1. Go into the Takeout archive root
cd "/path/to/Takeout"

# 2. Preview the metadata recovery (no changes made)
python3 /path/to/python/recover_metadata.py "Google Fotos" --dry-run

# 3. Run the metadata recovery for real
python3 /path/to/python/recover_metadata.py "Google Fotos" --log recovery.log

# 4. Set Finder creation dates on all media files (macOS only)
python3 /path/to/python/set_finder_dates.py "Google Fotos"
```

---

## Notes

- **Safe by default** — JSON sidecars are deleted only after `exiftool` reports success. If anything fails the JSON is kept so you can retry.
- **Idempotent** — running either script again on already-processed files is harmless.
- **Case-insensitive matching** — file lookup handles mixed-case extensions (`.JPG` vs `.jpg`).
- **Duplicate filenames** — files Google renamed with a `(1)` suffix are matched via the `title` field inside the JSON.
- **Edited copies** — files ending in `-editada` (Google's edited-copy suffix) automatically inherit the date, GPS and description from the corresponding original.
- **No external Python packages** — both scripts use only the Python standard library.

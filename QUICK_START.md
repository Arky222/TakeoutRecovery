# Quick Start Guide

## 1. Prepare your Google Photos export

1. Go to [Google Takeout](https://takeout.google.com), select **Google Fotos**, and download the archive.
2. Extract the zip. You should end up with a folder called something like `Google Fotos/` containing year subfolders and `.json` sidecar files next to every photo/video.

---

## 2. Install requirements

**Python 3.6+**
```bash
# macOS
brew install python
# Linux
sudo apt install python3
```

**exiftool** *(required for both scripts)*
```bash
# macOS
brew install exiftool
# Linux
sudo apt install libimage-exiftool-perl
```

**SetFile** *(macOS only — required for `python/set_finder_dates.py`)*
```bash
xcode-select --install
```

---

## 3. Recover metadata into your files

Writes the original date, GPS and description from the `.json` sidecars back into each photo/video. Deletes each `.json` after a successful write.

```bash
# Dry run first — no files are changed
python3 python/recover_metadata.py /path/to/GoogleFotos --dry-run

# Run for real
python3 python/recover_metadata.py /path/to/GoogleFotos
```

| Option | Effect |
|---|---|
| `--dry-run` | Preview only, no changes made |
| `--verbose` / `-v` | Print each file as it is processed |
| `--log FILE` | Save the log to a file |

---

## 4. Set Finder creation dates *(macOS only)*

Makes Finder show the original capture date instead of today.  
Run this **after** step 3.

```bash
python3 python/set_finder_dates.py /path/to/GoogleFotos
```

| Option | Effect |
|---|---|
| `--dry-run` | Preview only, no changes made |
| `--verbose` / `-v` | Print each file as it is processed |

---

## Tips

- Always do `--dry-run` before the real run.
- Both scripts are safe to re-run on already-processed files.
- For full details see [README.md](README.md).

# Cover-Posterizer

This script was mainly made for myself, but I wanted to share it.
I use it to make posters to accompany an upload on MyAnonamouse.

Turn a folder of `.cbr` comic book archives into a single, neatly-tiled poster of their cover art. Great for archiving and uploading on MAM.

![Example Output](placeholder)

---

##  Features & Workflow

- Scans a folder for `.cbr` files (supports `.cbz` mislabeled as `.cbr`)
- Extracts the **first image** (usually the cover) from each archive
- Resizes all covers to a uniform height (default: 600px)
- Tiles them into a single poster, auto-adjusting columns and rows
- Saves the final result as a high-quality JPEG image
- Auto-moves the finished poster to a hidden `.poster/` directory with a cleaned-up filename

---

##  Requirements

- Python 3.10+
- `pip install pillow rarfile`
- `unrar` installed and accessible in your PATH (WinRAR or equivalent)

This script also uses standard Python libraries:
- `zipfile`, `io`, `math`, `re`, `shutil`, `sys`, `pathlib`
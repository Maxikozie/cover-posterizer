import math, shutil, sys, re
from pathlib import Path

from PIL import Image
import rarfile, zipfile, io

POSSIBLE_UNRAR = [
    r"C:\Program Files\WinRAR\unrar.exe",
    r"C:\Program Files\WinRAR\UnRAR.exe",
    r"C:\Program Files (x86)\WinRAR\unrar.exe",
    r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
    "unrar.exe",
    "unrar",
]

def find_unrar() -> str | None:
    for p in POSSIBLE_UNRAR:
        if shutil.which(p) or Path(p).is_file():
            return str(Path(shutil.which(p) or p))
    return None


def clean_name(folder_name: str) -> str:
    """Drop trailing ranges/volume numbers so file becomes 'Largo Winch_poster'."""
    name = re.sub(r"[-_ ]\d+(\s*[-–]\s*\d+)?$", "", folder_name).strip()
    return re.sub(r"\s{2,}", " ", name)  # collapse dbl spaces


def _save_image(raw: bytes, member: str, out: Path):
    ext = member.lower().split(".")[-1]
    if ext in {"jpg", "jpeg"}:
        out.write_bytes(raw)
    else:
        with Image.open(io.BytesIO(raw)) as im:
            im.convert("RGB").save(out, "JPEG", quality=90)


def extract_covers(src: Path, out: Path, unrar: str) -> list[Path]:
    """Grab first JPG/PNG/WebP from each .cbr (or mis-labelled .cbz)."""
    rarfile.UNRAR_TOOL = unrar
    out.mkdir(exist_ok=True)
    got: list[Path] = []

    for arc in sorted(src.glob("*.cbr")):
        stem = arc.stem
        handled = False

        # try WinRAR first
        try:
            with rarfile.RarFile(arc) as rf:
                imgs = sorted(
                    f for f in rf.namelist()
                    if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                )
                if imgs:
                    with rf.open(imgs[0]) as s:
                        _save_image(s.read(), imgs[0], out / f"{stem}.jpg")
                    print(f" ✔  {arc.name}")
                    got.append(out / f"{stem}.jpg")
                else:
                    print(f" ✘  no pages in {arc.name}")
                handled = True
        except rarfile.Error:
            pass

        # fallback: ZIP ( mis-labelled CBZ CBR )
        if not handled:
            try:
                with zipfile.ZipFile(arc) as zf:
                    imgs = sorted(
                        f for f in zf.namelist()
                        if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                    )
                    if imgs:
                        with zf.open(imgs[0]) as s:
                            _save_image(s.read(), imgs[0], out / f"{stem}.jpg")
                        print(f" ✔  (ZIP) {arc.name}")
                        got.append(out / f"{stem}.jpg")
                    else:
                        print(f" ✘  no pages in {arc.name}")
            except zipfile.BadZipFile:
                print(f" ❌  {arc.name}: not RAR/ZIP")

    return got


def build_poster(images: list[Path], out_file: Path,
                 height: int = 600, pad: int = 0):
    if not images:
        return
    cols = math.ceil(math.sqrt(len(images)))
    rows = math.ceil(len(images) / cols)

    thumbs = []
    for p in images:
        with Image.open(p) as im:
            scale = height / im.height
            thumbs.append(im.resize((round(im.width*scale), height), Image.LANCZOS))

    col_w = [0]*cols
    for i, im in enumerate(thumbs):
        col_w[i % cols] = max(col_w[i % cols], im.width)

    width  = sum(col_w) + pad*(cols-1)
    poster = Image.new("RGB", (width, rows*height + pad*(rows-1)), (0, 0, 0))

    x_offsets = [0]
    for w in col_w[:-1]:
        x_offsets.append(x_offsets[-1] + w + pad)

    for idx, im in enumerate(thumbs):
        r, c = divmod(idx, cols)
        poster.paste(im, (x_offsets[c], r*(height+pad)))

    poster.save(out_file, quality=95)
    print(f" ✅  Poster saved as {out_file}")


if __name__ == "__main__":
    try:
        tgt = Path(input("Enter full path to folder with .cbr files: ").strip('"')).expanduser()
    except KeyboardInterrupt:
        sys.exit(0)

    if not tgt.is_dir():
        print("Folder doesn’t exist, bro. Learn to type.")
        sys.exit(1)

    unrar = find_unrar()
    if not unrar:
        print("❌  UnRAR not found. Install/point correctly.")
        sys.exit(1)
    print(f"Using UnRAR @ {unrar}")

    scratch = tgt / "first_pages"
    covers = extract_covers(tgt, scratch, unrar)

    poster_path = tgt / "poster.jpg"
    if covers:
        build_poster(covers, poster_path)
    else:
        print("Extraction produced zero covers; poster not created.")

    # ── tidy up ─────────────────────────────────────────────────────────────────
    shutil.rmtree(scratch, ignore_errors=True)

    if poster_path.exists():
        parent  = tgt.parent
        poster_dir = parent / ".poster"
        poster_dir.mkdir(exist_ok=True)

        final_name = f"{clean_name(tgt.name)}_poster.jpg"
        final_path = poster_dir / final_name
        if final_path.exists():
            final_path.unlink()
        shutil.move(poster_path, final_path)
        print(f" 📂  Moved → {final_path}")

    print("🏁  Done.")

from __future__ import annotations

import argparse
import io
import math
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps

try:
    import rarfile
except ModuleNotFoundError:
    rarfile = None


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ARCHIVE_EXTENSIONS = {".cbr", ".cbz"}
IGNORED_PATH_PARTS = {"__macosx", ".ds_store", "thumbs.db"}
POSSIBLE_UNRAR = [
    r"C:\Program Files\WinRAR\unrar.exe",
    r"C:\Program Files\WinRAR\UnRAR.exe",
    r"C:\Program Files (x86)\WinRAR\unrar.exe",
    r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
    "unrar.exe",
    "unrar",
]


@dataclass(frozen=True)
class ArchiveResult:
    archive: Path
    cover: Path | None
    page_name: str | None
    status: str


def find_unrar() -> str | None:
    for candidate in POSSIBLE_UNRAR:
        found = shutil.which(candidate)
        if found:
            return str(Path(found))
        if Path(candidate).is_file():
            return str(Path(candidate))
    return None


def clean_name(folder_name: str) -> str:
    """Drop common trailing volume/range markers from a pack folder name."""
    name = re.sub(r"[-_ ]\d+(\s*[-\u2013]\s*\d+)?$", "", folder_name).strip()
    name = re.sub(r"\b(?:v|vol(?:ume)?)\.?\s*\d+\s*$", "", name, flags=re.I).strip()
    return re.sub(r"\s{2,}", " ", name)


def natural_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def is_image_member(name: str) -> bool:
    path = Path(name)
    parts = {part.casefold() for part in path.parts}
    return path.suffix.casefold() in IMAGE_EXTENSIONS and not parts.intersection(IGNORED_PATH_PARTS)


def cover_sort_key(name: str) -> tuple[int, int, list[int | str]]:
    basename = Path(name).name.casefold()
    is_coverish = bool(re.search(r"\b(cover|front|fc)\b", basename))
    is_page_zero = bool(re.search(r"(^|[^0-9])0*1([^0-9]|$)", basename))
    return (0 if is_coverish else 1, 0 if is_page_zero else 1, natural_key(name))


def archive_paths(src: Path, recursive: bool) -> list[Path]:
    iterator = src.rglob("*") if recursive else src.iterdir()
    return sorted(
        (p for p in iterator if p.is_file() and p.suffix.casefold() in ARCHIVE_EXTENSIONS),
        key=lambda p: natural_key(p.name),
    )


def save_image(raw: bytes, member: str, out: Path, quality: int) -> None:
    with Image.open(io.BytesIO(raw)) as image:
        image = ImageOps.exif_transpose(image)
        if Path(member).suffix.casefold() in {".jpg", ".jpeg"} and image.mode == "RGB":
            out.write_bytes(raw)
            return
        image.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)


def extract_from_zip(archive: Path, out_file: Path, quality: int) -> tuple[Path | None, str | None, str]:
    try:
        with zipfile.ZipFile(archive) as zf:
            images = sorted((name for name in zf.namelist() if is_image_member(name)), key=cover_sort_key)
            if not images:
                return None, None, "no image pages found"
            with zf.open(images[0]) as stream:
                save_image(stream.read(), images[0], out_file, quality)
            return out_file, images[0], "ok"
    except zipfile.BadZipFile:
        return None, None, "not a ZIP archive"
    except Exception as exc:
        return None, None, f"ZIP read failed: {exc}"


def extract_from_rar(
    archive: Path,
    out_file: Path,
    quality: int,
    unrar: str | None,
) -> tuple[Path | None, str | None, str]:
    if rarfile is None:
        return None, None, "RAR support unavailable; install the rarfile Python package"
    if not unrar:
        return None, None, "RAR support unavailable; install WinRAR/UnRAR or use --unrar"

    rarfile.UNRAR_TOOL = unrar
    try:
        with rarfile.RarFile(archive) as rf:
            images = sorted((name for name in rf.namelist() if is_image_member(name)), key=cover_sort_key)
            if not images:
                return None, None, "no image pages found"
            with rf.open(images[0]) as stream:
                save_image(stream.read(), images[0], out_file, quality)
            return out_file, images[0], "ok"
    except rarfile.Error as exc:
        return None, None, f"RAR read failed: {exc}"
    except Exception as exc:
        return None, None, f"RAR extraction failed: {exc}"


def extract_cover(archive: Path, out_dir: Path, quality: int, unrar: str | None) -> ArchiveResult:
    out_file = out_dir / f"{archive.stem}.jpg"
    suffix = archive.suffix.casefold()

    attempts: list[tuple[Path | None, str | None, str]]
    if suffix == ".cbz":
        attempts = [extract_from_zip(archive, out_file, quality)]
        if attempts[0][0] is None:
            attempts.append(extract_from_rar(archive, out_file, quality, unrar))
    else:
        attempts = [extract_from_rar(archive, out_file, quality, unrar)]
        if attempts[0][0] is None:
            attempts.append(extract_from_zip(archive, out_file, quality))

    for cover, page_name, status in attempts:
        if cover:
            return ArchiveResult(archive, cover, page_name, status)

    return ArchiveResult(archive, None, None, " / ".join(status for _, _, status in attempts))


def extract_covers(src: Path, out: Path, quality: int, unrar: str | None, recursive: bool) -> list[ArchiveResult]:
    out.mkdir(parents=True, exist_ok=True)
    archives = archive_paths(src, recursive)
    if not archives:
        return []

    results: list[ArchiveResult] = []
    for archive in archives:
        result = extract_cover(archive, out, quality, unrar)
        results.append(result)
        if result.cover:
            print(f"OK   {archive.name} -> {result.page_name}")
        else:
            print(f"SKIP {archive.name}: {result.status}")
    return results


def grid_size(count: int, columns: int | None) -> tuple[int, int]:
    cols = columns or math.ceil(math.sqrt(count))
    return max(1, cols), math.ceil(count / max(1, cols))


def build_poster(
    images: Iterable[Path],
    out_file: Path,
    height: int,
    pad: int,
    background: tuple[int, int, int],
    quality: int,
    columns: int | None,
) -> None:
    paths = list(images)
    if not paths:
        raise ValueError("No cover images were extracted.")

    cols, rows = grid_size(len(paths), columns)
    thumbs: list[Image.Image] = []
    for path in paths:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            scale = height / image.height
            thumbs.append(image.resize((round(image.width * scale), height), Image.Resampling.LANCZOS))

    col_widths = [0] * cols
    for index, image in enumerate(thumbs):
        col_widths[index % cols] = max(col_widths[index % cols], image.width)

    width = sum(col_widths) + pad * (cols - 1)
    total_height = rows * height + pad * (rows - 1)
    poster = Image.new("RGB", (width, total_height), background)

    x_offsets = [0]
    for col_width in col_widths[:-1]:
        x_offsets.append(x_offsets[-1] + col_width + pad)

    for index, image in enumerate(thumbs):
        row, col = divmod(index, cols)
        x = x_offsets[col] + (col_widths[col] - image.width) // 2
        y = row * (height + pad)
        poster.paste(image, (x, y))

    out_file.parent.mkdir(parents=True, exist_ok=True)
    poster.save(out_file, "JPEG", quality=quality, optimize=True)


def folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def make_upload_text(
    src: Path,
    results: list[ArchiveResult],
    poster_path: Path,
    image_url: str | None,
    markdown: bool,
) -> str:
    good = [result for result in results if result.cover]
    skipped = [result for result in results if not result.cover]
    title = clean_name(src.name)
    pack_size = human_size(folder_size(src))
    archive_lines = "\n".join(f"- {result.archive.name}" for result in good)
    skipped_lines = "\n".join(f"- {result.archive.name}: {result.status}" for result in skipped)

    if markdown:
        poster_line = f"![Cover poster]({image_url or poster_path.name})"
        code_open = "```text"
        code_close = "```"
    else:
        poster_line = f"[img]{image_url}[/img]" if image_url else f"[img]{poster_path.name}[/img]"
        code_open = "[code]"
        code_close = "[/code]"

    parts = [
        f"{title}",
        "",
        poster_line,
        "",
        "Pack information",
        code_open,
        f"Archives: {len(good)}",
        f"Folder size: {pack_size}",
        f"Source folder: {src.name}",
        code_close,
        "",
        "Included archives",
        code_open,
        archive_lines or "No readable archives found.",
        code_close,
    ]
    if skipped:
        parts.extend(["", "Skipped archives", code_open, skipped_lines, code_close])
    return "\n".join(parts) + "\n"


def parse_color(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise argparse.ArgumentTypeError("Use a 6-digit hex color, for example #111111.")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a cover poster and tracker-friendly upload text from CBR/CBZ comic packs.",
    )
    parser.add_argument("folder", nargs="?", type=Path, help="Folder containing .cbr/.cbz files.")
    parser.add_argument("--height", type=int, default=600, help="Cover thumbnail height in pixels.")
    parser.add_argument("--pad", type=int, default=8, help="Padding between covers in pixels.")
    parser.add_argument("--columns", type=int, help="Force a specific number of poster columns.")
    parser.add_argument("--quality", type=int, default=92, help="JPEG quality from 1 to 95.")
    parser.add_argument("--background", type=parse_color, default=(18, 18, 18), help="Poster background hex color.")
    parser.add_argument("--recursive", action="store_true", help="Scan subfolders for archives.")
    parser.add_argument("--keep-covers", action="store_true", help="Keep extracted first-page JPG files.")
    parser.add_argument("--output-dir", type=Path, help="Destination folder. Defaults to sibling .poster folder.")
    parser.add_argument("--name", help="Output base name. Defaults to a cleaned folder name.")
    parser.add_argument("--unrar", type=Path, help="Path to UnRAR/WinRAR unrar.exe.")
    parser.add_argument("--image-url", help="Image host URL to place in generated BBCode/Markdown.")
    parser.add_argument("--markdown", action="store_true", help="Write Markdown instead of BBCode.")
    parser.add_argument("--no-text", action="store_true", help="Do not generate upload text.")
    args = parser.parse_args(argv)

    if args.folder is None:
        raw = input("Folder with .cbr/.cbz files: ").strip().strip('"')
        args.folder = Path(raw)
    if args.height < 100:
        parser.error("--height must be at least 100.")
    if args.pad < 0:
        parser.error("--pad cannot be negative.")
    if args.columns is not None and args.columns < 1:
        parser.error("--columns must be at least 1.")
    if not 1 <= args.quality <= 95:
        parser.error("--quality must be between 1 and 95.")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    src = args.folder.expanduser().resolve()
    if not src.is_dir():
        print(f"Folder does not exist: {src}", file=sys.stderr)
        return 1

    unrar = str(args.unrar.expanduser().resolve()) if args.unrar else find_unrar()
    if unrar:
        print(f"Using UnRAR: {unrar}")
    else:
        print("UnRAR not found. CBZ files will still work; CBR/RAR files will be skipped.")

    output_dir = (args.output_dir.expanduser().resolve() if args.output_dir else src.parent / ".poster")
    base_name = args.name or clean_name(src.name)
    poster_path = output_dir / f"{base_name}_poster.jpg"
    text_path = output_dir / f"{base_name}_{'description.md' if args.markdown else 'bbcode.txt'}"
    scratch = output_dir / f".{base_name}_covers"

    try:
        results = extract_covers(src, scratch, args.quality, unrar, args.recursive)
        covers = [result.cover for result in results if result.cover]
        if not covers:
            print("No covers were extracted; poster not created.", file=sys.stderr)
            return 1

        build_poster(covers, poster_path, args.height, args.pad, args.background, args.quality, args.columns)
        print(f"Poster saved: {poster_path}")

        if not args.no_text:
            text = make_upload_text(src, results, poster_path, args.image_url, args.markdown)
            text_path.write_text(text, encoding="utf-8")
            print(f"Upload text saved: {text_path}")
    finally:
        if not args.keep_covers:
            shutil.rmtree(scratch, ignore_errors=True)

    skipped = sum(1 for result in results if not result.cover)
    print(f"Done. Covers: {len(covers)} extracted, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

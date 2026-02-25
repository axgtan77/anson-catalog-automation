from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

def _try_import_rembg():
    try:
        from rembg import remove  # type: ignore
        return remove
    except Exception:
        return None

@dataclass
class ProcessResult:
    out_path: Path
    width: int
    height: int
    file_size: int

def process_to_white_bg(in_path: str | Path, out_path: str | Path, size: int = 1200, padding_ratio: float = 0.10, try_remove_bg: bool = True) -> ProcessResult:
    from PIL import Image, ImageOps, ImageEnhance, ImageFilter
    in_path = Path(in_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.open(in_path).convert("RGBA")

    remove_bg = _try_import_rembg() if try_remove_bg else None
    if remove_bg is not None:
        try:
            raw = in_path.read_bytes()
            out_bytes = remove_bg(raw)
            import io
            img = Image.open(io.BytesIO(out_bytes)).convert("RGBA")
        except Exception:
            pass

    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

    pad = int(size * padding_ratio)
    target = size - 2 * pad
    img.thumbnail((target, target), Image.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    x = (size - img.width) // 2
    y = (size - img.height) // 2
    canvas.alpha_composite(img, (x, y))

    out = canvas.convert("RGB")
    out = ImageOps.autocontrast(out)
    out = ImageEnhance.Sharpness(out).enhance(1.15)
    out = out.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))

    out.save(out_path, format="JPEG", quality=92, optimize=True)

    stat = out_path.stat()
    return ProcessResult(out_path=out_path, width=size, height=size, file_size=stat.st_size)

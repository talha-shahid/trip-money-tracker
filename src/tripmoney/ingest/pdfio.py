"""PDF -> page images (PyMuPDF, no external programs needed)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class PageImage:
    page_no: int  # 1-based
    total: int
    rgb: "object"  # HxWx3 uint8 numpy array
    jpeg: bytes  # for the review screen


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_pages(path: str | Path, dpi: int = 200) -> Iterator[PageImage]:
    try:
        import numpy as np
        import pymupdf
    except ImportError as e:  # pragma: no cover
        raise RuntimeError('PDF reading needs the extra packages: pip install -e ".[ocr]"') from e
    with pymupdf.open(str(path)) as doc:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
            yield PageImage(i + 1, len(doc), rgb.copy(), pix.tobytes("jpg", jpg_quality=70))


def jpeg_bytes(rgb, quality: int = 70) -> bytes:
    """Encode an HxWx3 uint8 array as JPEG (used for pages that had to be turned)."""
    import pymupdf

    h, w = rgb.shape[:2]
    return pymupdf.Pixmap(pymupdf.csRGB, w, h, rgb.tobytes(), False).tobytes("jpg", jpg_quality=quality)

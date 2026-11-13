"""OCR behind a small interface so the engine can be swapped (RapidOCR today)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Box:
    """One recognised text line with its pixel rectangle."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float = 1.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Box":
        return cls(d["text"], d["x0"], d["y0"], d["x1"], d["y1"], d.get("conf", 1.0))


def boxes_to_json(boxes: list[Box], width: int, height: int) -> str:
    return json.dumps({"w": width, "h": height, "boxes": [b.to_dict() for b in boxes]})


def boxes_from_json(text: str) -> tuple[list[Box], int, int]:
    d = json.loads(text)
    return [Box.from_dict(b) for b in d["boxes"]], int(d["w"]), int(d["h"])


class OcrEngine(Protocol):
    def read(self, image) -> list[Box]:  # image: HxWx3 uint8 RGB numpy array
        ...


class RapidOcrEngine:
    """RapidOCR (ONNX, runs on CPU). Imported lazily so the rest of the package works without it."""

    def __init__(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:  # pragma: no cover
            raise RuntimeError('OCR needs the extra packages: pip install -e ".[ocr]"') from e
        self._ocr = RapidOCR()

    def read(self, image) -> list[Box]:
        result, _ = self._ocr(image[:, :, :3])
        boxes = []
        for poly, text, conf in result or []:
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            boxes.append(Box(text, min(xs), min(ys), max(xs), max(ys), float(conf)))
        return boxes


class CachedEngine:
    """Wraps another engine and remembers results on disk, keyed by the image's hash."""

    def __init__(self, inner: OcrEngine, cache_dir: str | Path) -> None:
        self.inner, self.dir = inner, Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def read(self, image) -> list[Box]:
        key = hashlib.sha256(image.tobytes()).hexdigest()[:32]
        f = self.dir / f"{key}.json"
        if f.exists():
            return boxes_from_json(f.read_text())[0]
        boxes = self.inner.read(image)
        f.write_text(boxes_to_json(boxes, image.shape[1], image.shape[0]))
        return boxes

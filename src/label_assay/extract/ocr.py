"""Local OCR spine (RapidOCR) — the offline, deterministic second channel.

Runs with no network and no API key. Its per-line confidence is a genuine signal
(unlike a vision model's self-report), and it is the independent read the
confidence engine later cross-checks against the vision extraction. RapidOCR
ships its ONNX models in the wheel, so there is no download and no PaddlePaddle
dependency.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def read_lines(image: bytes) -> list[OcrLine]:
    """Detected text lines with per-line confidence, top-to-bottom as returned."""
    import numpy as np
    from PIL import Image

    rgb = Image.open(io.BytesIO(image)).convert("RGB")
    result, _elapsed = _engine()(np.asarray(rgb))
    if not result:
        return []
    return [OcrLine(text=str(text), confidence=float(score)) for _box, text, score in result]

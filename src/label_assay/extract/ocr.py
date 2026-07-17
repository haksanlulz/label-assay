"""Local OCR spine (RapidOCR) — the offline, deterministic second channel.

Runs with no network and no API key. Its per-line confidence is a genuine signal
(unlike a vision model's self-report), and it is the independent read the
confidence engine later cross-checks against the vision extraction. RapidOCR
ships its ONNX models in the wheel, so there is no download and no PaddlePaddle
dependency.
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from functools import lru_cache

# One inference at a time. The engine is shared across threads (batch work runs in
# a thread pool), its thread-safety is not guaranteed, and each concurrent
# inference holds its own working set — running several at once on a small machine
# is how the process gets killed. The network-bound vision calls stay parallel;
# this only serializes the local CPU work.
_ENGINE_LOCK = threading.Lock()


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...] | None = None  # 4 corner points, if known


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def read_lines(image: bytes) -> list[OcrLine]:
    """Detected text lines with per-line confidence, top-to-bottom as returned."""
    import numpy as np
    from PIL import Image

    rgb = Image.open(io.BytesIO(image)).convert("RGB")
    with _ENGINE_LOCK:
        result, _elapsed = _engine()(np.asarray(rgb))
    if not result:
        return []
    return [
        OcrLine(
            text=str(text),
            confidence=float(score),
            box=tuple((float(x), float(y)) for x, y in box),
        )
        for box, text, score in result
    ]

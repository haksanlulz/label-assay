"""Bold detection for the Government Warning heading — 27 CFR 16.22(a)(2).

The heading words of the warning must appear in bold and the remainder must not.
Bold cannot be read from a font name (labels arrive as raster images), so it is
measured: the mean stroke width of the heading is compared against the body text
sitting on the same line, at the same size. The comparison is RELATIVE and
INTERNAL — no absolute DPI is needed, only "are the heading's strokes meaningfully
thicker than the body's, right next to it."

Method: mean stroke width from the distance transform over the skeleton of the
tall connected components (which drops small punctuation). Because the heading
and the body share a line and a type size, their raw stroke widths compare
directly — a ratio at/above 1.30 is bold, at/below 1.12 is not, in between is a
human call. Cap height is used only to abstain when the text is under ~14px (too
small to judge from pixels). Tuned so a false "bold" (approving a non-compliant
label) is near-zero; residual errors fall to review.
"""

from __future__ import annotations

import enum
import io
import re
from dataclasses import dataclass

_MIN_CAP_PX = 14.0
_BOLD_RATIO = 1.30
_NOT_BOLD_RATIO = 1.12


class BoldVerdict(enum.StrEnum):
    BOLD_OK = "bold_ok"      # heading bold, remainder not
    NOT_BOLD = "not_bold"    # heading not distinctly bolder than the body
    REVIEW = "review"        # too small / could not measure


@dataclass(frozen=True)
class BoldFinding:
    verdict: BoldVerdict
    detail: str
    ratio: float | None


@dataclass(frozen=True)
class _Strokes:
    stroke_px: float  # mean stroke width in pixels
    cap_px: float     # cap height in pixels, for the too-small-to-judge guard


def measure_strokes(gray) -> _Strokes | None:
    """Mean stroke-width-to-cap-height for the dark text in a grayscale crop."""
    import cv2
    import numpy as np
    from skimage.morphology import skeletonize

    if gray is None or gray.size == 0 or min(gray.shape[:2]) < 4:
        return None

    # Otsu; on a light label, THRESH_BINARY_INV makes dark text the foreground.
    _thr, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    if (binary > 0).mean() > 0.5:  # text should be the minority; fix polarity if not
        binary = cv2.bitwise_not(binary)
    if int((binary > 0).sum()) < 20:
        return None

    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if count <= 1:
        return None
    heights = stats[1:, cv2.CC_STAT_HEIGHT]
    tall_threshold = 0.6 * float(heights.max())
    tall_ids = [i for i in range(1, count) if stats[i, cv2.CC_STAT_HEIGHT] >= tall_threshold]
    if not tall_ids:
        return None

    cap = float(np.median([stats[i, cv2.CC_STAT_HEIGHT] for i in tall_ids]))
    tall_mask = np.isin(labels, tall_ids)
    skeleton = skeletonize(tall_mask)
    if not skeleton.any() or cap <= 0:
        return None

    dist = cv2.distanceTransform(tall_mask.astype(np.uint8), cv2.DIST_L2, 5)
    stroke = float(np.mean(2.0 * dist[skeleton]))
    return _Strokes(stroke_px=stroke, cap_px=cap)


def bold_ratio_verdict(head_gray, body_gray) -> BoldFinding:
    head, body = measure_strokes(head_gray), measure_strokes(body_gray)
    if head is None or body is None or body.stroke_px <= 0:
        return BoldFinding(BoldVerdict.REVIEW, "Could not measure stroke widths reliably.", None)
    if min(head.cap_px, body.cap_px) < _MIN_CAP_PX:
        return BoldFinding(BoldVerdict.REVIEW, "Warning text is too small to judge boldness from the image.", None)

    ratio = head.stroke_px / body.stroke_px
    if ratio >= _BOLD_RATIO:
        return BoldFinding(BoldVerdict.BOLD_OK, f"The heading is bolder than the body text (ratio {ratio:.2f}).", ratio)
    if ratio <= _NOT_BOLD_RATIO:
        return BoldFinding(
            BoldVerdict.NOT_BOLD,
            f"The heading is not distinctly bolder than the rest of the statement (ratio {ratio:.2f}).",
            ratio,
        )
    return BoldFinding(BoldVerdict.REVIEW, f"Heading weight is borderline (ratio {ratio:.2f}).", ratio)


def check_warning_bold(image: bytes, ocr_lines) -> BoldFinding:
    """Locate the warning heading via OCR, split it into the heading words and the
    body text on the same line, and compare their stroke widths."""
    import numpy as np
    from PIL import Image

    gray = np.asarray(Image.open(io.BytesIO(image)).convert("L"))
    # OCR often drops the space between rendered words ("GOVERNMENTWARNING"), so
    # locate the heading on the space-insensitive form.
    line = next(
        (ln for ln in ocr_lines if ln.box and "governmentwarning" in re.sub(r"[^a-z0-9]", "", ln.text.casefold())),
        None,
    )
    if line is None:
        return BoldFinding(BoldVerdict.REVIEW, "Could not locate the warning heading to check boldness.", None)

    xs = [p[0] for p in line.box]
    ys = [p[1] for p in line.box]
    x0, x1, y0, y1 = int(min(xs)), int(max(xs)), int(min(ys)), int(max(ys))

    match = re.search(r"warning", line.text, re.IGNORECASE)
    if not match:
        return BoldFinding(BoldVerdict.REVIEW, "Could not delimit the warning heading.", None)
    split = x0 + int((x1 - x0) * (match.end() / len(line.text)))

    head, body = gray[y0:y1, x0:split], gray[y0:y1, split:x1]
    if head.size == 0 or body.size == 0 or (x1 - split) < 10:
        return BoldFinding(BoldVerdict.REVIEW, "Not enough body text beside the heading to compare.", None)
    return bold_ratio_verdict(head, body)

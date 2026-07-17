"""Bounded image decode — the shared guard against decompression bombs.

A PNG well under the 5 MB upload cap can decode to a raster of hundreds of
megabytes (a 12000x12000 solid-color PNG is ~440 KB compressed, ~430 MB
decoded), and several of those decoding at once is how a small machine gets
OOM-killed. PNG and JPEG headers carry the pixel dimensions, and ``Image.open``
parses only the header, so an oversized image is rejected before any raster is
allocated. ``_MAX_PIXELS`` is the single owner of the bound; every decode site
in the app goes through ``open_bounded``.
"""

from __future__ import annotations

import io
import warnings

from PIL import Image

# Well above any legitimate label scan: a 300-DPI scan of an 8x10 sheet is ~7 MP.
_MAX_PIXELS = 40_000_000

# Backstop for any future decode site that skips open_bounded: Pillow warns above
# this count and raises DecompressionBombError above twice it, instead of its
# default ~89 MP threshold.
Image.MAX_IMAGE_PIXELS = _MAX_PIXELS


class ImageTooLarge(ValueError):
    """The image's decoded pixel count exceeds the processing bound."""


# The hosted vision API rejects images over 8000 px on a side and downscales
# anything past ~1.6 K px itself, so this is the largest edge worth uploading.
_VISION_MAX_EDGE = 1568


def downscale_for_vision(image: bytes, max_edge: int = _VISION_MAX_EDGE) -> bytes:
    """Bytes for the vision call: the original image when it already fits,
    otherwise a re-encode with the long edge capped at ``max_edge``. The
    re-encode is PNG, not JPEG — label art is text, and compression artifacts
    would land in the strokes the model is asked to quote."""
    img = open_bounded(image)
    if max(img.size) <= max_edge:
        return image
    img.thumbnail((max_edge, max_edge))
    if img.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
        img = img.convert("RGB")  # e.g. CMYK, which PNG cannot carry
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def open_bounded(image: bytes) -> Image.Image:
    """Open image bytes lazily and reject before decode if the header declares
    more than ``_MAX_PIXELS`` pixels. Raises ``ImageTooLarge`` (or PIL's own
    error for bytes that are not an image)."""
    try:
        with warnings.catch_warnings():
            # The explicit check below is the enforcement; Pillow's advisory
            # warning at the same threshold is noise here.
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(image))
    except Image.DecompressionBombError as exc:  # over twice the bound: PIL refuses at open
        raise ImageTooLarge(str(exc)) from exc
    width, height = img.size
    if width * height > _MAX_PIXELS:
        raise ImageTooLarge(
            f"image is {width}x{height} ({width * height / 1e6:.0f} MP), "
            f"over the {_MAX_PIXELS / 1e6:.0f} MP processing bound"
        )
    return img

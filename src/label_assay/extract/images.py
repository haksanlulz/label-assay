"""Bounded image decode — the shared guard against decompression bombs.

A PNG well under the 5 MB upload cap can decode to a raster of hundreds of
megabytes (a 12000x12000 solid-color PNG is ~440 KB compressed, ~430 MB
decoded), and several of those decoding at once is how a small machine gets
OOM-killed. PNG and JPEG headers carry the pixel dimensions, and ``Image.open``
parses only the header, so an oversized image is rejected before any raster is
allocated. ``_MAX_PIXELS`` is the single owner of the bound; every decode site
in the app goes through ``open_bounded``.

``open_bounded`` also applies EXIF orientation. A phone photo stores its
rotation as a tag over sideways pixels; applying it here, once, means every
consumer of a decode — OCR, the vision copy, the preview — sees upright pixels
without each re-implementing the transpose.
"""

from __future__ import annotations

import io
import warnings

from PIL import Image, ImageOps

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

# Lossless right-angle transposes, keyed by degrees counter-clockwise — PIL's
# ROTATE_* constants turn counter-clockwise. One owner for both consumers: the
# OCR rotation retry probes with these, and the interactive path's
# operator-stated rotation corrects with them (an image that looks rotated N
# degrees clockwise comes upright under the N-degree counter-clockwise turn).
RIGHT_ANGLE_TRANSPOSES = {
    90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}


_ORIENTATION = 0x0112  # the EXIF Orientation tag


def _stored_sideways(image: bytes) -> bool:
    """Do these bytes carry an EXIF orientation that ``open_bounded`` applies?
    A header-only read; the 2..8 range mirrors the orientations
    ``ImageOps.exif_transpose`` acts on (1 and absent mean already upright)."""
    orientation = Image.open(io.BytesIO(image)).getexif().get(_ORIENTATION, 1)
    return isinstance(orientation, int) and 2 <= orientation <= 8


def downscale_for_vision(image: bytes, max_edge: int = _VISION_MAX_EDGE) -> bytes:
    """Bytes for the vision call: the original image when it already fits,
    otherwise a re-encode with the long edge capped at ``max_edge``. The
    re-encode is PNG, not JPEG — label art is text, and compression artifacts
    would land in the strokes the model is asked to quote."""
    img = open_bounded(image)
    if max(img.size) <= max_edge and not _stored_sideways(image):
        # Byte-identical only when the stored pixels are already upright: a
        # sideways-stored upload is re-encoded even when small, so the model
        # seeing it upright never depends on the provider honoring EXIF.
        return image
    img.thumbnail((max_edge, max_edge))
    return _encode_png(img)


def transpose_image(image: bytes, rotation: int) -> bytes:
    """Re-encode ``image`` with its raster turned ``rotation`` degrees
    counter-clockwise — the lossless right-angle transpose, applied to the
    bytes once so every downstream consumer of them sees the same turned
    raster. The decode goes through ``open_bounded`` (so EXIF orientation is
    applied first, and the pixel bound holds), and the re-encode is PNG for
    the same reason as the vision copy's: label art is text, and lossy
    artifacts would land in the strokes the readers measure. Raises
    ``ValueError`` on a rotation outside the transpose map."""
    transpose = RIGHT_ANGLE_TRANSPOSES.get(rotation)
    if transpose is None:
        raise ValueError(f"rotation must be 90, 180, or 270, not {rotation}")
    return _encode_png(open_bounded(image).transpose(transpose))


def _encode_png(img: Image.Image) -> bytes:
    if img.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
        img = img.convert("RGB")  # e.g. CMYK, which PNG cannot carry
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


# The result page's collapsible preview: large enough to eyeball a label,
# small enough that a 5 MB scan never rides back into the page at full weight.
_PREVIEW_MAX_EDGE = 1200
_PREVIEW_QUALITY = 85


def preview_jpeg(image: bytes, max_edge: int = _PREVIEW_MAX_EDGE) -> bytes:
    """Bytes for the result page's inline preview: decoded through the same
    ``open_bounded`` guard as every other decode site, downscaled to
    ``max_edge`` on the long side, and re-encoded as JPEG. Unlike the vision
    copy, stroke fidelity is not the point — a person is glancing at the image,
    not reading it — so JPEG's smaller payload wins. Re-encoding into a fresh
    buffer also drops any metadata the upload carried, so EXIF (GPS and the
    like) never reaches the rendered page."""
    img = open_bounded(image)
    img.thumbnail((max_edge, max_edge))
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")  # JPEG carries no alpha, palette, or 1-bit modes
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=_PREVIEW_QUALITY)
    return buffer.getvalue()


def open_bounded(image: bytes) -> Image.Image:
    """Open image bytes, reject before any decode if the header declares more
    than ``_MAX_PIXELS`` pixels, and hand back upright pixels (EXIF orientation
    applied). Raises ``ImageTooLarge`` (or PIL's own error for bytes that are
    not an image)."""
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
    # After the size gate, so an oversized image is still rejected on its
    # header alone; in_place leaves an untagged image lazy and uncopied.
    ImageOps.exif_transpose(img, in_place=True)
    return img

"""Purpose-built test images that no fixture corpus should contain, and the
suite's shared image helpers.

The bomb builder streams rows through zlib so the test process itself never
allocates the raster it describes — the point is proving the app rejects the
image before decoding it.
"""

from __future__ import annotations

import base64
import io
import re
import struct
import zlib
from functools import lru_cache

from PIL import Image


@lru_cache(maxsize=4)
def bomb_png(width: int, height: int) -> bytes:
    """A valid solid-white PNG: tiny compressed, ``width*height*3`` bytes decoded."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    comp = zlib.compressobj(level=9)
    row = b"\x00" + b"\xff" * (width * 3)  # filter byte + one white scanline
    idat = b"".join(comp.compress(row) for _ in range(height)) + comp.flush()
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def solid_png(width: int, height: int, color: str | tuple[int, int, int] = "white") -> bytes:
    """A solid-color RGB PNG encoded by Pillow — the plain upload for tests
    whose subject is not the raster's content."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def preview_image_from(html: str) -> Image.Image:
    """Decode the page's embedded preview back into pixels, so the assertions
    run against what the browser would actually render."""
    match = re.search(r'src="data:image/jpeg;base64,([^"]+)"', html)
    assert match, "no JPEG data URI found on the page"
    return Image.open(io.BytesIO(base64.b64decode(match.group(1))))

"""Fixture extractor — a content-keyed replay adapter for tests.

Replays a stored Extraction keyed by what the image shows: ``fixture_key``
hashes the decoded raster (size plus RGB pixels), never the encoded bytes.
The service re-encodes every upload before the vision call (see
``downscale_for_vision``), and two zlib builds can emit different PNG streams
for the same pixels, so a key over encoded bytes registered from an upload
misses the re-encode the extractor actually receives on another platform.
Decoding is exact, so the pixel key survives any change of encoder. An
unknown image raises, so a test cannot silently pass on a missing fixture.
Nothing wires this in at runtime: when no extractor is configured, the
service reports the reader unavailable rather than falling back.
"""

from __future__ import annotations

import hashlib

from label_assay.extract.base import Extraction
from label_assay.extract.images import open_bounded


def fixture_key(image: bytes) -> str:
    """Content address of an image: sha256 over its size and decoded RGB
    pixels, so the same picture keys identically however it was encoded.
    Registration and lookup both compute the key here — one owner, no way for
    the two sides to drift. The decode goes through ``open_bounded`` like
    every other decode site, so EXIF orientation is baked in exactly as it is
    on the raster the vision copy carries."""
    img = open_bounded(image).convert("RGB")
    hasher = hashlib.sha256()
    hasher.update(f"{img.width}x{img.height}:".encode())
    hasher.update(img.tobytes())
    return hasher.hexdigest()


class FixtureExtractor:
    def __init__(self, fixtures: dict[str, Extraction]) -> None:
        self._fixtures = fixtures  # keyed by fixture_key: pixel content, not bytes

    def extract(self, image: bytes) -> Extraction:
        digest = fixture_key(image)
        if digest not in self._fixtures:
            raise KeyError(f"no fixture for image pixels sha256={digest[:12]}")
        return self._fixtures[digest]

"""Fixture extractor — a hash-keyed replay adapter for tests.

Replays a stored Extraction keyed by the sha256 of the image bytes, so tests
run deterministically with no network and no model variance. An unknown image
raises, so a test cannot silently pass on a missing fixture. Nothing wires
this in at runtime: when no extractor is configured, the service reports the
reader unavailable rather than falling back.
"""

from __future__ import annotations

import hashlib

from label_assay.extract.base import Extraction


class FixtureExtractor:
    def __init__(self, fixtures: dict[str, Extraction]) -> None:
        self._fixtures = fixtures  # keyed by sha256 hexdigest of the image bytes

    def extract(self, image: bytes) -> Extraction:
        digest = hashlib.sha256(image).hexdigest()
        if digest not in self._fixtures:
            raise KeyError(f"no fixture for image sha256={digest[:12]}")
        return self._fixtures[digest]

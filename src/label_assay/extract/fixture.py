"""Fixture extractor — replays a stored Extraction keyed by image hash.

Two jobs: deterministic tests (no network, no model variance) and an offline
demo path if the AI endpoint is unreachable, which is the failure mode the
client named. An unknown image raises, so a test cannot silently pass on a
missing fixture.
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

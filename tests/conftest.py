"""Shared test bootstrap.

The startup reader warm-up is a real network call and a budget reservation, so
no test may fire it by accident just by entering a TestClient lifespan — a dev
machine with a key in .env would otherwise pay it on every context-managed
client. Tests that exercise the warm-up flip the flag back on explicitly.
"""

from __future__ import annotations

import pytest

from label_assay.web import app as webapp


@pytest.fixture(autouse=True)
def _no_startup_warm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webapp, "_WARM_ON_STARTUP", False)

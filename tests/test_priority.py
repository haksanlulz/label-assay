"""Interactive checks outrank batch items at the serialized OCR stage.

A batch holds the OCR lock for minutes of queued inferences; the priority gate
in extract/ocr.py is what keeps a single-label check from waiting behind the
whole queue. The OCR engine is stubbed slow so the schedule is observable; the
gate, the lock, and the service path are the real code under test.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

import fixture_corpus
from fixture_corpus import AbsentExtractor
from label_assay.domain.models import Application
from label_assay.extract import ocr as ocrmod
from label_assay.web.batch import _CONCURRENCY
from label_assay.web.service import check_label
from synthetic_images import solid_png

# Stub engines return the mandated warning as their one detected line, so the
# service's rotation retry (three extra passes per check when the warning is
# missing) stays out of the schedules these tests pin.
_WARNING_RESULT = [
    [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]], fixture_corpus.mandated_warning(), 0.99]
]


class _SlowEngine:
    """Stands in for RapidOCR: records each inference's raster width, holds the
    engine lock for a fixed slice, finds the mandated warning."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.widths: list[int] = []

    def __call__(self, array) -> tuple[list, float]:
        self.widths.append(int(array.shape[1]))
        time.sleep(self.delay)
        return _WARNING_RESULT, 0.0


def test_interactive_check_does_not_wait_behind_the_batch_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One batch worker drains four items back to back — each item is a fresh
    # lock acquisition, which is exactly where the gate applies. An interactive
    # check fired mid-queue must run after at most the one inference already in
    # flight, never behind the remaining queue. With a single worker at most one
    # reader can ever be queued at the lock, so this covers the between-items
    # yield only; the many-workers-already-queued schedule is pinned below.
    engine = _SlowEngine(delay=0.25)
    monkeypatch.setattr(ocrmod, "_engine", lambda: engine)
    batch_image, interactive_image = solid_png(32, 32), solid_png(48, 48)
    extractor = AbsentExtractor()

    def batch_worker() -> None:
        for _ in range(4):
            check_label(batch_image, Application(), extractor=extractor, background=True)

    async def scenario() -> None:
        # to_thread on both sides mirrors how the web app runs them: batch items
        # and the /check route each hand check_label to a worker thread.
        batch = asyncio.create_task(asyncio.to_thread(batch_worker))
        await asyncio.sleep(0.1)  # let the first batch item take the OCR lock
        await asyncio.to_thread(
            check_label, interactive_image, Application(), extractor=extractor
        )
        assert 48 in engine.widths, "interactive check finished without its OCR running"
        assert len(engine.widths) < 5, "interactive check returned only after the whole queue"
        await batch

    asyncio.run(scenario())

    position = engine.widths.index(48)
    assert position <= 1, f"interactive OCR ran at queue position {position}: {engine.widths}"
    assert engine.widths.count(32) == 4  # the batch still completed every item


class _GatedEngine:
    """Stands in for RapidOCR: records each inference's raster width; the first
    inference blocks until the test releases it, the rest are instant. Lets a
    test pin the mid-batch steady state — one inference in flight, every other
    worker already committed to the lock — before the interactive check fires."""

    def __init__(self) -> None:
        self.widths: list[int] = []
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def __call__(self, array) -> tuple[list, float]:
        self.widths.append(int(array.shape[1]))
        if len(self.widths) == 1:
            self.first_started.set()
            self.release_first.wait(timeout=10)
        return _WARNING_RESULT, 0.0


def test_interactive_check_overtakes_workers_already_queued_at_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The deployed batch runs _CONCURRENCY workers against one serialized
    # engine, so the steady state of a batch is one inference running and every
    # other worker already past the priority gate, blocked inside the engine
    # lock's acquire(). Those workers never revisit the gate for the current
    # item, and the lock wakes waiters in no promised order — an interactive
    # check registered after they queued outranks them only because a worker
    # re-checks the gate at the moment it acquires the lock and hands the lock
    # back. The single-worker test above structurally cannot produce this
    # schedule; this one pins it. With the re-check, the interactive read runs
    # at position 1 regardless of platform wake order; without it, it queues
    # behind the workers.
    engine = _GatedEngine()
    monkeypatch.setattr(ocrmod, "_engine", lambda: engine)
    batch_image, interactive_image = solid_png(32, 32), solid_png(48, 48)
    extractor = AbsentExtractor()

    workers = [
        threading.Thread(
            target=check_label,
            args=(batch_image, Application()),
            kwargs={"extractor": extractor, "background": True},
        )
        for _ in range(_CONCURRENCY)
    ]
    for worker in workers:
        worker.start()
    assert engine.first_started.wait(timeout=10), "no batch inference ever started"
    time.sleep(0.2)  # let the remaining workers pass the gate and reach acquire()

    interactive = threading.Thread(
        target=check_label,
        args=(interactive_image, Application()),
        kwargs={"extractor": extractor},
    )
    interactive.start()
    deadline = time.monotonic() + 10
    while not ocrmod._interactive_pending:
        assert time.monotonic() < deadline, "interactive check never registered at the gate"
        time.sleep(0.01)

    engine.release_first.set()  # the in-flight inference finishes; the lock is contested
    interactive.join(timeout=10)
    for worker in workers:
        worker.join(timeout=10)
    assert not interactive.is_alive(), "interactive check never completed"
    assert not any(w.is_alive() for w in workers), "a batch worker never completed"

    assert engine.widths[0] == 32  # the inference that was already in flight
    assert engine.widths.count(32) == _CONCURRENCY  # the batch still ran every item
    position = engine.widths.index(48)
    assert position == 1, (
        f"interactive OCR ran at queue position {position} of {len(engine.widths)} "
        f"({engine.widths}): it waited behind queued batch workers, not just the "
        "one inference already running"
    )


def test_batch_read_waits_until_no_interactive_check_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The gate primitive itself: while an interactive read is registered, a
    # background read blocks at the gate; releasing the last interactive lets it
    # through. Exercised via read_lines so the wiring is the shipped one.
    engine = _SlowEngine(delay=0.0)
    monkeypatch.setattr(ocrmod, "_engine", lambda: engine)
    image = solid_png(32, 32)

    with ocrmod._interactive_scope():
        done = threading.Event()

        def background_read() -> None:
            ocrmod.read_lines(image, background=True)
            done.set()

        thread = threading.Thread(target=background_read)
        thread.start()
        time.sleep(0.15)
        assert not done.is_set(), "background read ran while an interactive check was pending"
    thread.join(timeout=5)
    assert done.is_set(), "background read never resumed after the interactive check cleared"

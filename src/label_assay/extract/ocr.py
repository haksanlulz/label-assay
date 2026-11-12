"""Local OCR spine (RapidOCR) — the offline, deterministic second channel.

Runs with no network and no API key. Its per-line confidence is a genuine signal
(unlike a vision model's self-report), and it is the independent read the
confidence engine later cross-checks against the vision extraction. RapidOCR
ships its ONNX models in the wheel, so there is no download and no PaddlePaddle
dependency.
"""

from __future__ import annotations

import math
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from label_assay.extract.images import RIGHT_ANGLE_TRANSPOSES

# One inference at a time. The engine is shared across threads (batch work runs in
# a thread pool), its thread-safety is not guaranteed, and each concurrent
# inference holds its own working set — running several at once on a small machine
# is how the process gets killed. The network-bound vision calls stay parallel;
# this only serializes the local CPU work.
_ENGINE_LOCK = threading.Lock()

# Priority gate for the lock above. A single-label check is a person waiting at a
# browser with a 5-second expectation; a batch item is one of hundreds nobody is
# watching individually. Interactive reads register as pending, and a background
# read defers to them at two points: it parks here BEFORE trying for the engine
# lock, and if it acquires the lock while an interactive check is pending it
# hands the lock straight back and parks. The second point is load-bearing at
# batch concurrency: the steady state of a batch is one inference running and
# every other worker already past the gate, blocked inside the lock's acquire()
# — threading.Lock wakes waiters in no promised order, so without the re-check
# an interactive check queues behind all of them. With it, an interactive check
# waits behind at most the one inference already running — not the batch
# workers queued for the lock. Nothing preempts a lock already held, and the
# gate is never held while waiting for the lock, so no acquisition cycle
# exists. The trade, stated plainly: continuous interactive traffic pauses
# batch progress entirely (there is no aging). For one reviewer clicking during
# a batch — the stated use — that is exactly right; a stream of interactive
# users would need a fairer scheduler.
_INTERACTIVE = threading.Condition()
_interactive_pending = 0


@contextmanager
def _interactive_scope() -> Iterator[None]:
    global _interactive_pending
    with _INTERACTIVE:
        _interactive_pending += 1
    try:
        yield
    finally:
        with _INTERACTIVE:
            _interactive_pending -= 1
            _INTERACTIVE.notify_all()


def _yield_to_interactive() -> None:
    with _INTERACTIVE:
        while _interactive_pending:
            _INTERACTIVE.wait()


@contextmanager
def _engine_slot(background: bool) -> Iterator[None]:
    """Hold _ENGINE_LOCK for one inference, honoring the priority gate."""
    if not background:
        with _interactive_scope(), _ENGINE_LOCK:
            yield
        return
    while True:
        _yield_to_interactive()
        _ENGINE_LOCK.acquire()
        with _INTERACTIVE:
            if not _interactive_pending:
                break
        # An interactive check registered while this worker sat inside
        # acquire(), past the gate. Hand the lock back unused and park; the
        # worker would otherwise run ahead of the person waiting.
        _ENGINE_LOCK.release()
    try:
        yield
    finally:
        _ENGINE_LOCK.release()


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...] | None = None  # 4 corner points, if known
    # Degrees the raster was rotated before this pass (a rotation-retry read).
    # Non-zero means ``box`` is in the rotated frame, not the upright image's —
    # geometry consumers must not crop the upright image with it.
    rotation: int = 0


_CGROUP_ROOT = Path("/sys/fs/cgroup")


def cgroup_cpu_quota(root: Path = _CGROUP_ROOT) -> int | None:
    """CPUs the container's quota allows, rounded up, or None when there is no
    quota to read. Reads the unified layout (``cpu.max``: "<quota> <period>" or
    "max <period>") and falls back to the older split files."""
    try:
        quota_text, period_text = (root / "cpu.max").read_text(encoding="ascii").split()[:2]
    except (OSError, ValueError):
        try:
            quota_text = (root / "cpu" / "cpu.cfs_quota_us").read_text(encoding="ascii").strip()
            period_text = (root / "cpu" / "cpu.cfs_period_us").read_text(encoding="ascii").strip()
        except OSError:
            return None
    try:
        quota, period = int(quota_text), int(period_text)
    except ValueError:  # "max", or anything unreadable: no limit known
        return None
    if quota <= 0 or period <= 0:
        return None
    return max(1, math.ceil(quota / period))


def _visible_cpus() -> int:
    try:
        return len(os.sched_getaffinity(0))  # honours a pinned CPU set where the OS has one
    except AttributeError:
        return os.cpu_count() or 1


def usable_cpus(root: Path = _CGROUP_ROOT) -> int:
    """CPUs this process can actually run on: the cores it can see, lowered to
    the container's quota when one is set. ``os.cpu_count()`` alone reports the
    host's cores, which inside a two-CPU container on a large host is wrong by
    the size of the host."""
    quota = cgroup_cpu_quota(root)
    visible = _visible_cpus()
    return max(1, min(visible, quota) if quota is not None else visible)


_configured_threads = 0


def set_engine_threads(count: int) -> None:
    """State the thread count outright; 0 goes back to detection. The web shell
    passes its ``OCR_THREADS`` setting here at startup. This module does not
    read configuration itself: the deciding layer imports ``OcrLine`` from it,
    and that layer stays clear of the settings module."""
    global _configured_threads
    if count < 0:
        raise ValueError(f"thread count must be 0 or more, not {count}")
    _configured_threads = count
    _engine.cache_clear()


def engine_threads() -> int:
    """Threads for one OCR inference: the stated count when there is one,
    otherwise the usable CPU count."""
    return _configured_threads or usable_cpus()


def engine_options() -> dict[str, int]:
    """Keyword arguments for the engine. onnxruntime sizes its pool from the
    machine's cores, so under a CPU quota it starts one spinning thread per
    host core and they fight over the quota: on two cores of a sixteen-core
    machine the 11 registry labels averaged 12.2 s a read with the default pool
    and 2.4 s with two threads, the same lines read either way. The count is
    passed only when it is below the machine's, so a host with every core free
    keeps the engine's own choice."""
    threads = engine_threads()
    if threads < (os.cpu_count() or 1):
        return {"intra_op_num_threads": threads}
    return {}


def build_engine(options: dict[str, int]):
    from rapidocr_onnxruntime import RapidOCR

    # A top-level keyword is copied into all three models' session settings; a
    # det_/cls_/rec_-prefixed one is overwritten by that copy and never lands.
    return RapidOCR(**options)


@lru_cache(maxsize=1)
def _engine():
    return build_engine(engine_options())


def read_lines(image: bytes, *, background: bool = False, rotation: int = 0) -> list[OcrLine]:
    """Detected text lines with per-line confidence, top-to-bottom as returned.

    ``background=True`` marks a batch-worker read: it parks at the priority
    gate while an interactive read is pending — before queueing for the engine
    lock, and again by returning the lock unused if an interactive read
    registered while this one waited in the lock's queue.

    ``rotation`` (90, 180, or 270, degrees counter-clockwise — the shared
    right-angle transpose map) rotates the decoded raster before inference
    — one pass of the caller's retry for labels that print text sideways. Each
    such call is an ordinary read: it queues for the engine lock and runs the
    bounded decode exactly like an upright pass, and no lock is held between
    passes (the caller loops, so acquisitions never nest). The returned lines
    carry the rotation so geometry consumers know their boxes are not in the
    upright frame.
    """
    import numpy as np

    from label_assay.extract.images import open_bounded

    if rotation and rotation not in RIGHT_ANGLE_TRANSPOSES:
        raise ValueError(f"rotation must be 0, 90, 180, or 270, not {rotation}")

    # The decode — and the rotation, which is raster work of the same size —
    # happens inside the lock too: a decoded raster is the largest allocation on
    # this path, and bounding the process to one at a time is what keeps a batch
    # of concurrent workers from stacking them.
    with _engine_slot(background):
        rgb = open_bounded(image).convert("RGB")
        if rotation:
            rgb = rgb.transpose(RIGHT_ANGLE_TRANSPOSES[rotation])
        result, _elapsed = _engine()(np.asarray(rgb))
    if not result:
        return []
    return [
        OcrLine(
            text=str(text),
            confidence=float(score),
            box=tuple((float(x), float(y)) for x, y in box),
            rotation=rotation,
        )
        for box, text, score in result
    ]

"""The OCR engine's thread count follows the CPUs the process may use.

onnxruntime sizes its thread pool from the machine's core count. In a container
held to two CPUs by a quota on a many-core host, that is one spinning thread
per host core fighting over two CPUs' worth of time, which measured about five
times slower than two threads on the same two cores.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from label_assay.extract import ocr


def _cgroup(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"cpu.max": "200000 100000\n"}, 2),
        ({"cpu.max": "150000 100000\n"}, 2),  # a fractional quota rounds up
        ({"cpu.max": "50000 100000\n"}, 1),  # never below one
        ({"cpu.max": "max 100000\n"}, None),  # no quota set
        ({"cpu/cpu.cfs_quota_us": "400000\n", "cpu/cpu.cfs_period_us": "100000\n"}, 4),
        ({"cpu/cpu.cfs_quota_us": "-1\n", "cpu/cpu.cfs_period_us": "100000\n"}, None),
        ({"cpu.max": "garbage\n"}, None),
        ({"cpu.max": "200000 0\n"}, None),
        ({}, None),
    ],
)
def test_cgroup_quota_reads_both_layouts(tmp_path: Path, files: dict[str, str], expected: int | None) -> None:
    assert ocr.cgroup_cpu_quota(_cgroup(tmp_path, files)) == expected


def test_usable_cpus_is_the_lower_of_the_quota_and_the_visible_cores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocr, "_visible_cpus", lambda: 16)
    assert ocr.usable_cpus(_cgroup(tmp_path / "a", {"cpu.max": "200000 100000\n"})) == 2
    assert ocr.usable_cpus(_cgroup(tmp_path / "b", {"cpu.max": "max 100000\n"})) == 16
    monkeypatch.setattr(ocr, "_visible_cpus", lambda: 1)
    assert ocr.usable_cpus(_cgroup(tmp_path / "c", {"cpu.max": "800000 100000\n"})) == 1


def test_an_explicit_setting_wins_over_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "usable_cpus", lambda: 2)
    monkeypatch.setenv("OCR_THREADS", "3")
    assert ocr.engine_threads() == 3
    monkeypatch.setenv("OCR_THREADS", "0")
    assert ocr.engine_threads() == 2


def test_the_engine_is_left_alone_when_every_core_is_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCR_THREADS", raising=False)
    monkeypatch.setattr(ocr, "usable_cpus", lambda: 16)
    monkeypatch.setattr(ocr.os, "cpu_count", lambda: 16)
    assert ocr.engine_options() == {}
    monkeypatch.setattr(ocr, "usable_cpus", lambda: 2)
    assert ocr.engine_options() == {"intra_op_num_threads": 2}


def test_the_real_engine_takes_the_thread_count() -> None:
    """The count has to reach all three models' sessions. A keyword the engine
    drops leaves every session at its default and nothing else would say so."""
    engine = ocr.build_engine({"intra_op_num_threads": 1})
    sessions = (engine.text_det.infer, engine.text_cls.infer, engine.text_rec.session)
    assert [s.session.get_session_options().intra_op_num_threads for s in sessions] == [1, 1, 1]


def test_health_reports_the_thread_count(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from label_assay.web.app import app

    monkeypatch.setattr(ocr, "usable_cpus", lambda: 2)
    monkeypatch.setattr(ocr.os, "cpu_count", lambda: 16)
    monkeypatch.delenv("OCR_THREADS", raising=False)
    assert TestClient(app).get("/health").json()["ocr_threads"] == 2

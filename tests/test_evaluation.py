"""The offline gate must detect changed decisions and missing coverage."""
from copy import deepcopy
from pathlib import Path

import pytest

from label_assay.evaluation import evaluate, load_manifest
from label_assay.domain.models import Verdict

MANIFEST = Path(__file__).parent / "fixtures/evaluation/cases.json"


def test_committed_expectations_cover_every_rule_and_preserve_abstentions():
    result = evaluate(load_manifest(MANIFEST))
    assert result["passed"], result["errors"]
    assert result["cases"] == 12
    assert result["decisions"] == 48
    assert result["actual_counts"]["not_evaluable"] >= 12
    assert result["actual_counts"]["needs_review"] > 0
    assert len(result["confusion_by_rule"]) == 4


@pytest.mark.parametrize("mutation", ["wrong", "missing", "extra", "duplicate", "version"])
def test_changed_engine_output_fails_the_gate(mutation):
    from label_assay.verify.engine import verify

    def changed(*args, **kwargs):
        report = verify(*args, **kwargs).model_copy(deep=True)
        if mutation == "wrong":
            report.findings[0].verdict = Verdict.FAIL
        elif mutation == "missing":
            report.findings.pop()
        elif mutation == "extra":
            report.findings.append(report.findings[0].model_copy(update={"rule_id": "extra"}))
        elif mutation == "duplicate":
            report.findings.append(report.findings[0])
        else:
            report.rulebook_version = "changed"
        return report

    assert not evaluate(load_manifest(MANIFEST), verifier=changed)["passed"]


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "missing", "unknown", "stale"])
def test_incomplete_or_stale_baseline_is_rejected(mutation):
    manifest = deepcopy(load_manifest(MANIFEST))
    if mutation == "empty":
        manifest["cases"] = []
    elif mutation == "duplicate":
        manifest["cases"].append(manifest["cases"][0])
    elif mutation == "missing":
        manifest["cases"][0]["expected"].pop("health_warning_bold")
    elif mutation == "unknown":
        manifest["cases"][0]["expected"]["imaginary"] = "pass"
    else:
        manifest["rulebook_version"] = "stale"
    with pytest.raises(ValueError):
        evaluate(manifest)


def test_cli_replaces_stale_green_evidence_on_refusal(tmp_path):
    import json
    import subprocess
    import sys

    runner = MANIFEST.parents[3] / "tools/evaluate_offline.py"
    good = subprocess.run([sys.executable, str(runner), "--output", str(tmp_path)],
                          capture_output=True, text=True)
    assert good.returncode == 0, good.stderr
    broken = load_manifest(MANIFEST)
    broken["rulebook_version"] = "stale"
    baseline = tmp_path / "broken.json"
    baseline.write_text(json.dumps(broken))
    bad = subprocess.run([sys.executable, str(runner), "--manifest", str(baseline),
                          "--output", str(tmp_path)], capture_output=True, text=True)
    assert bad.returncode == 2
    assert not json.loads((tmp_path / "results.json").read_text())["passed"]

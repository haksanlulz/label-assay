"""Regressions from the 2026-11-12 audit of the tools, the server routes and
the checks that could not fail. Each test failed on the code at 2ec82d7."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from label_assay.web import app as webapp
from label_assay.web.batch import ApplicationCSVError, create_job, parse_application_csv

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "label_assay"
client = TestClient(webapp.app)


def _tool(name: str):
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    return __import__(name)


verify_deploy = _tool("verify_deploy")
eval_cola = _tool("eval_cola")


# --- the deciding layer never reaches a model or the network (GAUNTLET §3) ---

DECIDING = ("verify", "match", "text", "rulebook", "domain")
FORBIDDEN = {"anthropic", "httpx", "requests", "urllib", "http", "socket", "openai", "aiohttp", "subprocess"}
FORBIDDEN_INTERNAL = {"label_assay.extract.haiku", "label_assay.web", "label_assay.config"}


def _imports(path: Path) -> set[str]:
    package = ".".join(path.relative_to(SRC.parent).with_suffix("").parts[:-1])
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join([*package.split(".")[: len(package.split(".")) - node.level + 1], base]).strip(".")
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def _module_file(name: str) -> Path | None:
    if not name.startswith("label_assay"):
        return None
    rel = Path(*name.split(".")[1:])
    for candidate in (SRC / rel.with_suffix(".py"), SRC / rel / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def deciding_layer_violations() -> list[str]:
    seen: set[Path] = set()
    queue = [p for part in DECIDING for p in sorted((SRC / part).rglob("*.py"))]
    assert queue, "no deciding-layer modules found; the scan would pass on nothing"
    problems: list[str] = []
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        for name in sorted(_imports(path)):
            where = path.relative_to(SRC).as_posix()
            if name.split(".")[0] in FORBIDDEN:
                problems.append(f"{where} imports {name}")
            if any(name == bad or name.startswith(bad + ".") for bad in FORBIDDEN_INTERNAL):
                problems.append(f"{where} imports {name}")
            target = _module_file(name)
            if target is not None:
                queue.append(target)
    return problems


def test_the_deciding_layer_imports_no_model_or_network_client() -> None:
    assert deciding_layer_violations() == []


def test_the_purity_scan_can_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Plant the fault in a copy of the tree and require the scan to name it.
    import shutil

    copy = tmp_path / "label_assay"
    shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__"))
    engine = copy / "verify" / "engine.py"
    engine.write_text(engine.read_text(encoding="utf-8") + "\nimport anthropic\n", encoding="utf-8")
    warning = copy / "match" / "warning.py"
    warning.write_text(warning.read_text(encoding="utf-8") + "\nfrom label_assay.extract.haiku import HaikuExtractor\n",
                       encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "SRC", copy)
    problems = deciding_layer_violations()
    assert "verify/engine.py imports anthropic" in problems
    assert any(p.startswith("match/warning.py imports label_assay.extract.haiku") for p in problems)


# --- tools/verify_deploy.py ---

def _li(verdict: str, cite: str) -> str:
    return (f'<li class="finding finding--{verdict}"><div class="finding__head"><span class="badge badge--{verdict}">x'
            f'</span><span class="finding__cite">27 CFR {cite}</span></div><p class="finding__detail">d</p></li>')


def _page(**verdicts: str) -> str:
    cites = {"warning": "16.21", "bold": "16.22(a)(2)", "brand": "5.64", "alcohol": "5.65"}
    return '<div class="alert alert--pass"></div>' + "".join(_li(v, cites[k]) for k, v in verdicts.items())


def test_a_deploy_missing_the_brand_or_alcohol_finding_is_not_live() -> None:
    problems = verify_deploy.check_problems(200, _page(warning="pass", bold="pass"))
    assert any("5.64" in p for p in problems)
    assert any("5.65" in p for p in problems)


def test_a_deploy_missing_the_bold_finding_is_not_live() -> None:
    problems = verify_deploy.check_problems(200, _page(warning="pass", brand="pass", alcohol="pass"))
    assert any("16.22" in p for p in problems)


@pytest.mark.parametrize("rule", ["warning", "bold", "brand", "alcohol"])
def test_a_rule_that_stopped_evaluating_is_not_live(rule: str) -> None:
    verdicts = {"warning": "pass", "bold": "pass", "brand": "pass", "alcohol": "pass"} | {rule: "not_evaluable"}
    assert verify_deploy.check_problems(200, _page(**verdicts)) != []


def test_all_four_findings_passing_is_live() -> None:
    assert verify_deploy.check_problems(200, _page(warning="pass", bold="pass", brand="pass", alcohol="pass")) == []


def test_the_speed_target_is_enforced_not_printed() -> None:
    assert verify_deploy.timing_problems(3.2, 5.0) == []
    assert verify_deploy.timing_problems(5.0, 5.0) == []
    slow = verify_deploy.timing_problems(7.4, 5.0)
    assert len(slow) == 1 and "7.4" in slow[0] and "5" in slow[0]
    missing = verify_deploy.timing_problems(None, 5.0)
    assert len(missing) == 1 and "no timing" in missing[0]
    assert verify_deploy.timing_problems(99.0, None) == []  # explicitly switched off


# --- tools/eval_cola.py ---

def _row(filename: str, verdict: str = "pass", status: str = "done") -> dict[str, str]:
    return {"filename": filename, "status": status, "verdict": verdict, "detail": "d"}


def test_cola_run_with_only_passes_and_reviews_is_clean() -> None:
    rows = [_row("a.png"), _row("b.png", "needs_review")]
    assert eval_cola.outcome_problems(rows, expected_rows=2) == []


def test_cola_run_fails_on_a_fail_an_error_or_a_missing_row() -> None:
    assert len(eval_cola.outcome_problems([_row("a.png", "fail")], expected_rows=1)) == 1
    assert len(eval_cola.outcome_problems([_row("a.png", "", "error")], expected_rows=1)) == 1
    assert len(eval_cola.outcome_problems([_row("a.png")], expected_rows=2)) == 1


def test_cola_run_allows_the_one_documented_true_positive() -> None:
    assert eval_cola.outcome_problems([_row("cola_24100001000120.png", "fail")], expected_rows=1) == []


# --- server routes ---

def test_csv_export_waits_for_the_batch_to_finish() -> None:
    job = create_job(["pending.png"])
    response = client.get(f"/batch/{job.id}/export.csv")
    assert response.status_code == 409
    assert "still running" in response.text


def test_application_csv_headers_tolerate_spaces_and_hyphens() -> None:
    parsed = parse_application_csv(b"Filename,Brand Name,Class-Type,Fanciful Name\na.png,Old Tom,Bourbon,Reserve\n")
    application = parsed["a.png"]
    assert application.brand_name == "Old Tom"
    assert application.class_type == "Bourbon"
    assert application.fanciful_name == "Reserve"


def test_application_csv_without_a_brand_column_is_refused() -> None:
    with pytest.raises(ApplicationCSVError, match="brand_name"):
        parse_application_csv(b"filename,brand,class_type\na.png,Old Tom,Bourbon\n")


def test_compare_does_not_show_interpreter_internals() -> None:
    deeply_nested = b"[" * 30000 + b"]" * 30000
    response = client.post("/compare", files={
        "before_file": ("before.json", deeply_nested, "application/json"),
        "after_file": ("after.json", b"{}", "application/json"),
    })
    assert response.status_code == 422
    assert "Stack overflow" not in response.text
    assert "recursion" not in response.text.lower()
    # Which of the two written messages appears depends on the interpreter:
    # where the decoder gives up on the nesting the file is "not a valid
    # report"; where it parses, the file is a list and not a report export.
    # Either is text written for the page.
    assert ("not a valid LabelAssay report" in response.text
            or "Choose a LabelAssay full-report JSON export" in response.text)

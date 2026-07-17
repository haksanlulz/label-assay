"""Verify that the deployed instance is live and serving this working tree.

The deploy pipeline is push-triggered (.github/workflows/hf-deploy.yml), so a
green local suite proves nothing about the public URL: unpushed work leaves it
serving older code, and a stopped machine leaves it serving nothing, both
without any local signal. This script is the ritual after every push — the
Live link in the README is claimed only after it passes.

Three probes, weakest to strongest:

1. /health must answer with every subsystem ready and this tree's exact
   rulebook hash. The hash pins the served rulebook byte-for-byte, but only
   the rulebook — application code does not move it.
2. /sample must 404. The bundled demo route was removed; an instance still
   answering it is serving a build from before the removal.
3. One real check of a known-compliant fixture, timed. The probe is the label
   whose warning statement body is painted in capitals: 16.22(a)(2) fixes only
   the heading's case, so a comparator that wrongly enforces case on the body
   fails this label while a plain compliant label passes either way — it
   discriminates current code from stale where the rulebook hash cannot. The
   measured time is the per-deploy re-take of the single-label latency number
   in docs/DESIGN.md.

httpx is a dev-group dependency, so run under the dev environment:

    uv run python tools/verify_deploy.py                 # against the deployed Space URL
    uv run python tools/verify_deploy.py --health-only   # no model spend
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
LABELS = REPO / "tests" / "fixtures" / "labels"

_LIGHT_PALETTES = ("palette=white", "palette=cream")
# Anchored to result.html's alert block; a test pins the template to this marker.
_VERDICT_RE = re.compile(r'class="alert alert--([a-z_]+)"')
_ELAPSED_RE = re.compile(r"Checked in ([0-9.]+)")


DEPLOY_URL = "https://haksanlulz-label-assay.hf.space"


def default_base_url() -> str:
    """The deployed Space URL; a test pins the README's Live link to this."""
    return DEPLOY_URL


def expected_rulebook_version() -> str:
    from label_assay.rulebook.loader import load_rulebook

    return load_rulebook().version


def health_problems(payload: dict[str, object], expected_version: str) -> list[str]:
    problems: list[str] = []
    if payload.get("status") != "ok":
        problems.append(f"health status is {payload.get('status')!r}, expected 'ok'")
    served = payload.get("rulebook_version")
    if served != expected_version:
        problems.append(
            f"served rulebook_version {served!r} != this tree's {expected_version!r} — "
            "the instance is running a different rulebook (unpushed or undeployed work)"
        )
    if payload.get("ocr") != "ready":
        problems.append(f"ocr is {payload.get('ocr')!r}, expected 'ready'")
    if payload.get("ai_reader") != "configured":
        problems.append(f"ai_reader is {payload.get('ai_reader')!r}, expected 'configured'")
    return problems


def sample_problems(status_code: int) -> list[str]:
    if status_code == 404:
        return []
    return [
        f"GET /sample returned {status_code}, expected 404 — the bundled demo route was "
        "removed, so an instance answering it is serving a build from before the removal"
    ]


def page_verdict(page: str) -> str | None:
    found = _VERDICT_RE.search(page)
    return found.group(1) if found else None


def server_elapsed(page: str) -> float | None:
    """The instance's own measurement, as printed on the result page."""
    found = _ELAPSED_RE.search(page)
    return float(found.group(1)) if found else None


_FINDING_RE = re.compile(
    r'<li class="finding[^>]*>.*?badge--([a-z_]+)".*?finding__cite">([^<]+)<', re.S
)


def page_findings(page: str) -> list[tuple[str, str]]:
    """(verdict, citation) per finding, in page order."""
    return [(m.group(1), m.group(2).strip()) for m in _FINDING_RE.finditer(page)]


def check_problems(status_code: int, page: str) -> list[str]:
    """Judge the live check per finding, not by the overall verdict alone.

    The probe label discriminates the comparator (capitals body, 27 CFR 16.21
    must PASS — the pre-fix comparator fails it), but its bold check may
    legitimately abstain when the render puts the heading on its own line, and
    an abstention is the designed behavior, not a deploy fault.
    """
    if status_code != 200:
        return [f"POST /check returned {status_code}, expected 200"]
    if page_verdict(page) is None:
        return ["the response carries no verdict marker; it is not a result page"]
    findings = page_findings(page)
    if not findings:
        return ["the result page carries no findings; it is not a result page"]
    problems = []
    warning_text = [v for v, cite in findings if "16.21" in cite]
    if not warning_text:
        problems.append("no 27 CFR 16.21 finding on the result page")
    elif warning_text[0] != "pass":
        problems.append(
            f"the 16.21 warning-text check returned {warning_text[0]!r} on the "
            "capitals-body fixture — the deployed comparator does not behave like "
            "this tree's"
        )
    for verdict, cite in findings:
        if verdict == "fail":
            problems.append(f"the deployed checker failed {cite} on a known-compliant label")
        elif verdict == "needs_review" and "16.22" not in cite and "16.21" not in cite:
            problems.append(
                f"{cite} abstained on a known-legible fixture; investigate the "
                "instance's OCR read"
            )
    return problems


def pick_probe(rows: list[dict[str, str]]) -> dict[str, str]:
    """The capitals-body warning label, on the lightest palette available so the
    OCR corroboration gate is not the reason for an abstention."""
    candidates = [
        row
        for row in rows
        if row["defect"] == "warning_body_caps" and row["expected_verdict"] == "pass"
    ]
    if not candidates:
        raise LookupError("no warning_body_caps fixture in the manifest; regenerate the corpus")
    for row in candidates:
        if any(palette in row["notes"] for palette in _LIGHT_PALETTES):
            return row
    return candidates[0]


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default=None, help="deployed instance (default: the deployed Space URL)"
    )
    parser.add_argument(
        "--health-only", action="store_true", help="skip the live check; no model spend"
    )
    parser.add_argument(
        "--timeout", type=float, default=120.0, help="seconds to allow the live check"
    )
    args = parser.parse_args()
    base_url = args.base_url or default_base_url()

    manifest = LABELS / "manifest.csv"
    applications = LABELS / "applications.csv"
    if not args.health_only and not (manifest.exists() and applications.exists()):
        print("fixture corpus not found; run tools/make_test_labels.py first", file=sys.stderr)
        return 2

    expected = expected_rulebook_version()
    problems: list[str] = []
    print(f"verifying {base_url} against rulebook {expected}")

    try:
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            health = client.get("/health")
            if health.status_code != 200:
                problems.append(f"GET /health returned {health.status_code}, expected 200")
            else:
                payload = health.json()
                problems += health_problems(payload, expected)
                print(
                    f"  instance {payload.get('instance')!r}, "
                    f"served rulebook {payload.get('rulebook_version')!r}"
                )

            problems += sample_problems(client.get("/sample").status_code)

            # Do not spend a model call proving behavior on an instance the
            # identity probes already rejected.
            if not args.health_only and not problems:
                probe = pick_probe(_load_rows(manifest))
                filed = {row["filename"]: row for row in _load_rows(applications)}
                application = filed[probe["filename"]]
                image = (LABELS / probe["filename"]).read_bytes()
                started = time.perf_counter()
                response = client.post(
                    "/check",
                    files={"image": (probe["filename"], image, "image/png")},
                    data={
                        "brand_name": application["brand_name"],
                        "class_type": application["class_type"],
                    },
                    timeout=args.timeout,
                )
                wall = time.perf_counter() - started
                problems += check_problems(response.status_code, response.text)
                measured = server_elapsed(response.text)
                print(
                    f"  live check of {probe['filename']}: {wall:.1f}s round trip"
                    + (f", {measured:.1f}s on the instance" if measured is not None else "")
                )
    except httpx.TransportError as exc:
        problems.append(
            f"could not reach {base_url}: {exc!r}. A dropped TCP connection or TLS "
            "handshake usually means the machine is stopped or the app suspended — "
            "check the Space build logs, then re-run the deploy workflow "
            "(.github/workflows/hf-deploy.yml supports workflow_dispatch)."
        )

    if problems:
        print("\nNOT LIVE — do not claim the URL:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("live: every probe passed; the URL serves this tree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

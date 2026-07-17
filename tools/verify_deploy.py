"""Verify that the deployed instance is live and serving this working tree.

The deploy pipeline is push-triggered (.github/workflows/fly-deploy.yml), so a
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

    uv run python tools/verify_deploy.py                 # base URL from fly.toml
    uv run python tools/verify_deploy.py --health-only   # no model spend
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import tomllib
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
LABELS = REPO / "tests" / "fixtures" / "labels"

_LIGHT_PALETTES = ("palette=white", "palette=cream")
# Anchored to result.html's alert block; a test pins the template to this marker.
_VERDICT_RE = re.compile(r'class="alert alert--([a-z_]+)"')
_ELAPSED_RE = re.compile(r"Checked in ([0-9.]+)")


def default_base_url() -> str:
    """Derive the public URL from the app name in fly.toml, which owns it."""
    with (REPO / "fly.toml").open("rb") as handle:
        app = tomllib.load(handle)["app"]
    return f"https://{app}.fly.dev"


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


def check_problems(status_code: int, page: str) -> list[str]:
    if status_code != 200:
        return [f"POST /check returned {status_code}, expected 200"]
    verdict = page_verdict(page)
    if verdict is None:
        return ["the response carries no verdict marker; it is not a result page"]
    if verdict == "pass":
        return []
    if verdict == "fail":
        return [
            "the deployed checker failed a known-compliant label — its comparator does "
            "not behave like this tree's"
        ]
    return [
        f"the deployed checker returned {verdict!r} on a known-compliant, legible fixture; "
        "investigate the instance's OCR read before claiming the deploy"
    ]


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
        "--base-url", default=None, help="deployed instance (default: derived from fly.toml)"
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
            "check `flyctl status`, then re-run the deploy workflow "
            "(.github/workflows/fly-deploy.yml supports workflow_dispatch)."
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

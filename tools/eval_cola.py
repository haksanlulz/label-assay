"""Evaluate the checker against the real-label corpus, through the app itself.

Uploads the corpus (tests/fixtures/cola/) to a running instance's /batch
endpoint with its applications.csv, polls /batch/{id}/data to completion,
downloads the CSV export, and prints per-label outcomes plus a summary.

How to read the results: every label in this corpus is TTB-approved, so a FAIL
from a content rule (warning wording, alcohol content, brand match) is a
candidate false positive worth investigating — with one known exception:
cola_24100001000120 misspells the printed heading ("GOVERMENT WARNING", the
first N of GOVERNMENT missing), a real defect approval did not catch, so a warning-wording
FAIL on that row is a true positive. The corpus README documents it.
NEEDS_REVIEW is legitimate
abstention — several labels print the warning rotated 90° or in low contrast,
exactly what the legibility gate exists to hold for a human. Typography (bold)
findings are soft ground truth either way: TTB's registry disclaims the
rendered type ("may appear differently, with respect to type size, characters
per inch and contrasting background, than actual labels"), so the corpus
README treats a bold-check fail as suspect rather than proven.

Going through /batch rather than importing the engine keeps this an end-to-end
measurement of the deployed artifact: upload handling, the extractor, the
engine, and the export all sit in the measured path.

httpx is a dev-group dependency (it ships for the test client), so this script
runs under the dev environment: `uv run` from the repo root provides it.

Run:  uv run python tools/eval_cola.py --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "cola"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for the batch")
    args = parser.parse_args()

    images = sorted(args.fixtures.glob("cola_*.png"))
    applications = args.fixtures / "applications.csv"
    if not images or not applications.exists():
        print(f"corpus not found under {args.fixtures}", file=sys.stderr)
        return 2

    files = [("images", (p.name, p.read_bytes(), "image/png")) for p in images]
    files.append(("applications", (applications.name, applications.read_bytes(), "text/csv")))

    with httpx.Client(base_url=args.base_url, timeout=60.0, follow_redirects=False) as client:
        created = client.post("/batch", files=files)
        if created.status_code != 303:
            # The app renders failures as an HTML error page; the message is in the body.
            print(f"batch create failed: HTTP {created.status_code}: {created.text[:300]}", file=sys.stderr)
            return 1
        job_url = created.headers["location"]
        job_id = job_url.rstrip("/").rsplit("/", 1)[-1]

        deadline = time.monotonic() + args.timeout
        while True:
            polled = client.get(f"/batch/{job_id}/data")
            if polled.status_code != 200:
                print(f"poll failed: HTTP {polled.status_code} (did the instance restart?)", file=sys.stderr)
                return 1
            data = polled.json()
            if data["done"] >= data["total"]:
                break
            if time.monotonic() > deadline:
                print(f"timed out at {data['done']}/{data['total']}", file=sys.stderr)
                return 1
            time.sleep(3)

        export = client.get(f"/batch/{job_id}/export.csv")
        if export.status_code != 200:
            print(f"export failed: HTTP {export.status_code}", file=sys.stderr)
            return 1

    rows = list(csv.DictReader(io.StringIO(export.text)))
    if not rows:
        print("export contained no rows", file=sys.stderr)
        return 1
    fails = [r for r in rows if r["verdict"] == "fail"]
    errors = [r for r in rows if r["status"] == "error"]
    width = max(len(r["filename"]) for r in rows)
    print(f"\n{'label':<{width}}  verdict        detail")
    for r in rows:
        print(f"{r['filename']:<{width}}  {r['verdict'] or r['status']:<13}  {r['detail'][:100]}")
    print(
        f"\n{len(rows)} TTB-approved labels: "
        f"{sum(r['verdict'] == 'pass' for r in rows)} pass, "
        f"{sum(r['verdict'] == 'needs_review' for r in rows)} needs review, "
        f"{len(fails)} fail, {len(errors)} error."
    )
    if fails:
        print("A FAIL on an approved label is a candidate false positive — investigate each:")
        for r in fails:
            print(f"  {r['filename']}: {r['detail'][:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

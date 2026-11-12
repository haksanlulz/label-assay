"""Score a generated corpus against its manifest, through a running instance's /batch.

Uploads every label_*.png under --corpus with its applications.csv, waits for
the batch, downloads the CSV export, and joins it to the generator's
manifest.csv. A label is compliant when its expected verdict is pass (the plain
compliant rows and the all-capitals body variant); every other row carries one
defect.

    false pass = a defective label the app passed
    false fail = a compliant label the app failed
    held       = needs_review, on either side

Writes ledger.json with one row per label, so every label is accounted for, and
exits non-zero when a label is missing, errored, or falsely judged.

What a clean run does and does not show: the generator pins its ground truth to
the engine's own matchers, so zero false verdicts here says the deployed path
agrees with the corpus it was built against. It is not an accuracy figure for
real artwork.

Run:  uv run python tools/make_test_labels.py --seed 20261110 --count 100 --out /tmp/corpus
      uv run python tools/score_batch.py --corpus /tmp/corpus --out /tmp/run
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

VERDICTS = ("pass", "needs_review", "fail")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--meta", default="{}", help="JSON object stored as the ledger's run header"
    )
    parser.add_argument(
        "--timeout", type=float, default=2400.0, help="seconds to wait for the batch"
    )
    args = parser.parse_args()

    with (args.corpus / "manifest.csv").open(encoding="utf-8", newline="") as handle:
        manifest = list(csv.DictReader(handle))
    images = sorted(args.corpus.glob("label_*.png"))
    applications = args.corpus / "applications.csv"
    if not manifest or len(images) != len(manifest) or not applications.exists():
        print(
            f"corpus mismatch: {len(images)} images, {len(manifest)} manifest rows", file=sys.stderr
        )
        return 2

    files = [("images", (p.name, p.read_bytes(), "image/png")) for p in images]
    files.append(("applications", (applications.name, applications.read_bytes(), "text/csv")))

    started = time.monotonic()
    with httpx.Client(base_url=args.base_url, timeout=120.0, follow_redirects=False) as client:
        created = client.post("/batch", files=files, data={"recover_rotation": "on"})
        if created.status_code != 303:
            print(
                f"batch create failed: HTTP {created.status_code}: {created.text[:300]}",
                file=sys.stderr,
            )
            return 1
        job_id = created.headers["location"].rstrip("/").rsplit("/", 1)[-1]
        deadline = time.monotonic() + args.timeout
        while True:
            polled = client.get(f"/batch/{job_id}/data")
            if polled.status_code != 200:
                print(f"poll failed: HTTP {polled.status_code}", file=sys.stderr)
                return 1
            data = polled.json()
            if data["done"] >= data["total"]:
                break
            if time.monotonic() > deadline:
                print(f"timed out at {data['done']}/{data['total']}", file=sys.stderr)
                return 1
            time.sleep(5)
        export = client.get(f"/batch/{job_id}/export.csv")
        if export.status_code != 200:
            print(f"export failed: HTTP {export.status_code}", file=sys.stderr)
            return 1
    elapsed = time.monotonic() - started

    ledger = score(manifest, export.text, json.loads(args.meta), elapsed)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "ledger.json").write_text(
        json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(ledger["summary"], indent=2))
    for problem in ledger["problems"]:
        print("PROBLEM", problem)
    return 1 if ledger["problems"] else 0


def score(manifest: list[dict], export_csv: str, run: dict, elapsed: float) -> dict:
    """Join the export to the manifest. Every manifest row yields exactly one ledger row."""
    observed = {row["filename"]: row for row in csv.DictReader(io.StringIO(export_csv))}
    summary_columns = ("filename", "status", "verdict", "detail")
    rows: list[dict] = []
    problems: list[str] = []
    tally: Counter[tuple[str, str]] = Counter()
    for expected in manifest:
        name = expected["filename"]
        group = "compliant" if expected["expected_verdict"] == "pass" else "defective"
        got = observed.get(name)
        if got is None:
            status, verdict, detail, rules = "missing", "missing", "", {}
            problems.append(f"{name}: missing from the export")
        else:
            status, verdict, detail = got["status"], got["verdict"], got["detail"]
            rules = {key: value for key, value in got.items() if key not in summary_columns}
            if status == "error" or verdict not in VERDICTS:
                problems.append(f"{name}: status={status} verdict={verdict!r} {detail[:120]}")
        if group == "compliant" and verdict == "fail":
            problems.append(f"{name}: false fail on a compliant label: {detail[:160]}")
        if group == "defective" and verdict == "pass":
            problems.append(f"{name}: false pass on defect {expected['defect']}")
        tally[(group, verdict)] += 1
        rows.append(
            {
                "filename": name,
                "defect": expected["defect"],
                "expected_verdict": expected["expected_verdict"],
                "group": group,
                "status": status,
                "verdict": verdict,
                "matches_expected": verdict == expected["expected_verdict"],
                "detail": detail,
                "rules": rules,
            }
        )

    def side(group: str) -> dict:
        counts = {v: tally[(group, v)] for v in (*VERDICTS, "missing") if tally[(group, v)]}
        return {"total": sum(counts.values()), **counts}

    by_defect: dict[str, dict[str, int]] = {}
    for row in rows:
        counts = by_defect.setdefault(row["defect"], {})
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    summary = {
        "labels": len(rows),
        "compliant": side("compliant"),
        "defective": side("defective"),
        "false_passes": tally[("defective", "pass")],
        "false_fails": tally[("compliant", "fail")],
        "matches_expected_verdict": sum(row["matches_expected"] for row in rows),
        "errors_or_missing": sum(row["status"] in ("error", "missing") for row in rows),
        "elapsed_seconds": round(elapsed, 1),
        "seconds_per_label": round(elapsed / max(len(rows), 1), 2),
        "by_defect": dict(sorted(by_defect.items())),
    }
    return {"run": run, "summary": summary, "problems": problems, "labels": rows}


if __name__ == "__main__":
    raise SystemExit(main())

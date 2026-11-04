"""Validate a review record's structure and report digest, not its authorship."""
import argparse
import json
from pathlib import Path

from label_assay.domain.review import validate_review

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("record", type=Path)
args = parser.parse_args()
try:
    # A 5 MB source report can acquire up to 500 bounded reviewer notes.
    if args.record.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Review record exceeds 8 MB.")
    record = validate_review(json.loads(args.record.read_bytes()))
except (ValueError, OSError, RecursionError) as exc:
    parser.exit(1, f"Invalid review record: {exc}\n")
print(f"Consistent report digest; {len(record.decisions)} decisions; "
      f"{'complete' if record.complete else 'pending review'}. Reviewer identity is self-entered.")

"""Validate a review record's structure and report digest, not its authorship."""
import argparse
from pathlib import Path

from label_assay.domain.review import MAX_REVIEW_RECORD_BYTES, load_review

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("record", type=Path)
args = parser.parse_args()
try:
    # Bound the read itself, including if a file grows after it is opened.
    with args.record.open("rb") as stream:
        record = load_review(stream.read(MAX_REVIEW_RECORD_BYTES + 1))
except (ValueError, OSError, RecursionError) as exc:
    parser.exit(1, f"Invalid review record: {exc}\n")
print(f"Consistent report digest; {len(record.decisions)} decisions; "
      f"{'complete' if record.complete else 'pending review'}. Reviewer identity is self-entered.")

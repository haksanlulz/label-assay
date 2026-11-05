"""Run the committed regression baseline without a server, model, image or API key."""
import argparse
import json
from pathlib import Path

from label_assay.evaluation import evaluate, load_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parents[1]
                        / "tests/fixtures/evaluation/cases.json")
    parser.add_argument("--output", type=Path, default=Path("artifacts/evaluation"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        result = evaluate(load_manifest(args.manifest))
    except Exception as exc:
        # Replace a previous green artifact even when baseline validation fails.
        (args.output / "results.json").write_text(
            json.dumps({"passed": False, "errors": [str(exc)]}, indent=2) + "\n", encoding="utf-8")
        (args.output / "summary.md").write_text(
            f"# Offline rule-engine evaluation\n\nGate: REFUSED\n\n{exc}\n", encoding="utf-8")
        parser.exit(2, f"Evaluation refused: {exc}\n")
    (args.output / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    lines = ["# Offline rule-engine evaluation", "", f"Gate: {'PASS' if result['passed'] else 'FAIL'}",
             f"Cases: {result['cases']}; decisions: {result['decisions']}",
             f"Rulebook: {result['rulebook_version']}", f"Baseline SHA-256: {result['baseline_sha256']}",
             "", result["scope"], "", "## Actual outcomes (including abstentions)", ""]
    lines += [f"- {key}: {value}" for key, value in result["actual_counts"].items()]
    lines += ["", "Per-rule expected/actual matrices and full findings: results.json.",
              "This gate measures fixture agreement, not production accuracy or legal approval."]
    lines += ["", *result["errors"]]
    (args.output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{'PASS' if result['passed'] else 'FAIL'}: {result['cases']} cases, "
          f"{result['decisions']} decisions; evidence in {args.output}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

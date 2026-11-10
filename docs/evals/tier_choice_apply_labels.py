"""Copy labels into tier_choice_fresh_set.json.

The labels come as a JSON object of request id to tier, {"q001": "fast", ...}: what the
labelling page's "Copy labels" gives, or what Claude reads back from the page's store. Unknown
ids or tiers are refused, so a typo cannot slip into the fixture. Run from the repository root:

    python -I -B docs/evals/tier_choice_apply_labels.py labels.json --by Jeevan
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIERS = ("fast", "smart", "ultra")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", help='JSON file: {"q001": "fast", ...}')
    parser.add_argument("--by", required=True, help="who labelled them")
    parser.add_argument("--set", default=str(ROOT / "docs/evals/tier_choice_fresh_set.json"))
    args = parser.parse_args()

    path = Path(args.set)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    known = {item["id"] for item in fixture["items"]}
    unknown = sorted(set(labels) - known)
    invalid = sorted(qid for qid, tier in labels.items() if tier not in TIERS)
    if unknown or invalid:
        raise SystemExit(f"Refused: unknown ids {unknown[:5]}, invalid tiers on {invalid[:5]}")
    for item in fixture["items"]:
        if item["id"] in labels:
            item["tier"] = labels[item["id"]]
    fixture["labelled_by"] = args.by
    path.write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    missing = [item["id"] for item in fixture["items"] if item["tier"] not in TIERS]
    counts = {tier: sum(item["tier"] == tier for item in fixture["items"]) for tier in TIERS}
    print(f"Labelled {len(fixture['items']) - len(missing)} of {len(fixture['items'])}: {counts}")
    if missing:
        print(f"Still unlabelled: {missing}")


if __name__ == "__main__":
    main()

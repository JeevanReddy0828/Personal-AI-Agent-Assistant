"""Score the tier choices already recorded against the outcome labels in tier_outcome_labels.json.

Laya's prediction is its preset difficulty score at the app's thresholds with the explicit cues,
read from the recorded GPU runs (tier_choice_variants_results.json for p-ids, the confirmation
run for q-ids); the heuristic is `_complexity` as recorded in the same runs. No model is called.
Run from the repository root:

    python -B docs/evals/tier_outcome_score.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from laptop_agent.decisions import EXPLICIT_CUES, _level_for  # noqa: E402

TIERS = ("fast", "smart", "ultra")
EVALS = ROOT / "docs/evals"


def _load(name: str) -> dict:
    return json.loads((EVALS / name).read_text(encoding="utf-8"))


def recorded_predictions() -> tuple[dict[str, str], dict[str, str]]:
    texts = {item["id"]: item["text"] for name in ("tier_choice_set.json", "tier_choice_fresh_set.json") for item in _load(name)["items"]}
    laya: dict[str, str] = {}
    heuristic: dict[str, str] = {}
    for row in _load("tier_choice_variants_results.json")["per_item"]:
        cued = any(cue in texts[row["id"]].lower() for cue in EXPLICIT_CUES)
        laya[row["id"]] = TIERS[2 if cued else _level_for(row["english"]["preset"])]
    for row in _load("tier_choice_results_cuda.json")["per_item"]:
        heuristic[row["id"]] = row["heuristic"]
    for row in _load("tier_choice_confirm_results.json")["per_item"]:
        laya[row["id"]] = row["4_preset_baseline"]
        heuristic[row["id"]] = row["today"]
    return laya, heuristic


def score(labels: dict[str, str], predicted: dict[str, str]) -> dict:
    hard = [i for i in labels if labels[i] != "fast"]
    easy = [i for i in labels if labels[i] == "fast"]
    return {
        "accuracy": round(sum(labels[i] == predicted[i] for i in labels) / len(labels), 3),
        "hard_sent_up": f"{sum(predicted[i] != 'fast' for i in hard)}/{len(hard)}",
        "easy_sent_up": f"{sum(predicted[i] != 'fast' for i in easy)}/{len(easy)}",
        "confusion": {t: [sum(labels[i] == t and predicted[i] == p for i in labels) for p in TIERS] for t in TIERS},
    }


def main() -> None:
    labels = {item["id"]: item["label"] for item in _load("tier_outcome_labels.json")["items"]}
    laya, heuristic = recorded_predictions()
    result = {
        "laya": score(labels, laya),
        "heuristic": score(labels, heuristic),
        "always_fast": score(labels, {i: "fast" for i in labels}),
    }
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()

"""Confirmation run: the four candidates frozen in tier_choice_variants.md, on the fresh set.

Every candidate is fitted on the original 155 requests only (tier_choice_set.json, with the Laya
answers recorded in tier_choice_variants_results.json), by the committed code, before it sees a
fresh request. The fresh set's labels are used for scoring and nothing else.

Selection rule, fixed before any fresh label existed: the winner has the highest accuracy among
candidates that send no more fast requests to ultra than `_complexity` does on the fresh set (ties
go to fewer fitted values). It is adopted only if its accuracy beats `_complexity` by at least ten
points.

Run from the repository root, once every "tier" in tier_choice_fresh_set.json is filled in:
    python -I -B docs/evals/tier_choice_confirm.py --models <dir> --device cuda
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("tier_choice_variants", ROOT / "docs/evals/tier_choice_variants.py")
variants = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(variants)
base = variants.base
TIERS = base.TIERS
LAYA_COLS = ["preset", "tiers_score", *variants.CHOICE_KEYS, "short", "steps", "deep"]
CHOICE_THRESHOLDS = (0.85, 1.05)
PRESET_THRESHOLDS = (1.55, 2.05)
LENGTH_CUTS = (10, 17)


def cued(items: list[dict], levels: list[int]) -> list[int]:
    return [2 if base.explicit_ultra(item["text"]) else level for item, level in zip(items, levels)]


def load_fresh(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [item["id"] for item in data["items"] if item.get("tier") not in TIERS]
    if missing or not data.get("labelled_by"):
        raise SystemExit(f"Not labelled yet: set labelled_by and a tier for {len(missing)} requests, e.g. {missing[:5]}")
    return data["items"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", default=str(ROOT / "docs/evals/tier_choice_fresh_set.json"))
    parser.add_argument("--models", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--out", default=str(ROOT / "docs/evals/tier_choice_confirm_results.json"))
    args = parser.parse_args()

    fresh = load_fresh(Path(args.set))
    train = json.loads((ROOT / "docs/evals/tier_choice_set.json").read_text(encoding="utf-8"))["items"]
    recorded = json.loads((ROOT / "docs/evals/tier_choice_variants_results.json").read_text(encoding="utf-8"))
    train_laya = {row["id"]: row["english"] for row in recorded["per_item"]}
    train_labels = [TIERS.index(item["tier"]) for item in train]
    train_heur = [variants.heuristic_features(item["text"]) for item in train]
    train_rows = [[train_laya[item["id"]][c] for c in LAYA_COLS] for item in train]

    heuristic_model = variants.fit_softmax(train_heur, train_labels)
    combined_model = variants.fit_softmax([a + b for a, b in zip(train_rows, train_heur)], train_labels)

    run = variants.score_with_laya(fresh, Path(args.models), args.device)
    feats = run["checkpoints"]["english"]["features"]
    fresh_heur = [variants.heuristic_features(item["text"]) for item in fresh]
    fresh_rows = [[f[c] for c in LAYA_COLS] for f in feats]
    words = [len(item["text"].split()) for item in fresh]

    candidates = {
        "1_heuristic_signals_logistic": (15, cued(fresh, [variants.predict_softmax(heuristic_model, r) for r in fresh_heur])),
        "2_tiers_choice_expected": (2, cued(fresh, [base.to_level(f["several_steps"] + 2 * f["careful_reasoning"], CHOICE_THRESHOLDS)
                                                    for f in feats])),
        "3_all_laya_plus_heuristic_logistic": (39, cued(fresh, [variants.predict_softmax(combined_model, a + b)
                                                               for a, b in zip(fresh_rows, fresh_heur)])),
        "4_preset_baseline": (2, cued(fresh, [base.to_level(f["preset"], PRESET_THRESHOLDS) for f in feats])),
    }
    today = base.summarise(fresh, [variants.AgentOrchestrator._complexity(item["text"]) for item in fresh])
    report = {name: {**base.summarise(fresh, levels), "fitted_values": n} for name, (n, levels) in candidates.items()}
    eligible = [n for n in report if report[n]["fast_sent_to_ultra"] <= today["fast_sent_to_ultra"]]
    winner = max(eligible, key=lambda n: (report[n]["accuracy"], -report[n]["fitted_values"])) if eligible else None
    labels = [TIERS.index(item["tier"]) for item in fresh]
    results = {
        "items": len(fresh),
        "labelled_by": json.loads(Path(args.set).read_text(encoding="utf-8"))["labelled_by"],
        "device": run["device"], "laya": run["laya"],
        "timing": {name: {k: v for k, v in data.items() if k != "features"} for name, data in run["checkpoints"].items()},
        "length_vs_label_spearman": round(base.spearman(words, labels), 3),
        "length_only_reference": base.summarise(fresh, [0 if w < LENGTH_CUTS[0] else 1 if w < LENGTH_CUTS[1] else 2 for w in words]),
        "complexity_today": today,
        "candidates": report,
        "winner": winner,
        "adopt": bool(winner) and report[winner]["accuracy"] >= today["accuracy"] + 0.10,
        "per_item": [{"id": item["id"], "tier": item["tier"], "today": TIERS[lv],
                      **{name: TIERS[levels[i]] for name, (_, levels) in candidates.items()}}
                     for i, (item, lv) in enumerate(zip(fresh, [variants.AgentOrchestrator._complexity(x["text"]) for x in fresh]))],
    }
    Path(args.out).write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {k: v for k, v in results.items() if k != "per_item"}
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

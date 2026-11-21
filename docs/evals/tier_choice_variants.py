"""Can better questions to Laya, or combining it with the heuristic, choose the tier more accurately?

Follows tier_choice_experiment.py on the same frozen set. Everything below was fixed before the
first run of this file:

- QUESTIONS: the six questions asked in one forward pass per request.
- VARIANTS: how each is turned into a tier. Anything fitted (thresholds, logistic weights) is
  fitted by 5-fold cross-validation, with the same round-robin-within-tier folds as before.
- The selection rule: the best variant has the highest cross-validated accuracy among those that
  send no more fast requests to ultra than `_complexity` does (3). Ties go to the variant with
  fewer fitted parameters. The result is exploratory: this set has now been looked at twice, so
  only a fresh set labelled by Jeevan can confirm it.

Run from the repository root in the environment that has `laya`:
    python -I -B docs/evals/tier_choice_variants.py --models <dir> --device cuda
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("tier_choice_experiment", ROOT / "docs/evals/tier_choice_experiment.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
AgentOrchestrator = base.AgentOrchestrator
TIERS = base.TIERS

QUESTIONS = {
    "tiers_score": {
        "type": "score",
        "instructions": "How much work does a good answer to `request` take for an AI assistant?",
        "criteria": [
            "quick: a greeting, a fact, a definition, a short rewrite or a one-line answer",
            "several steps: an explanation, a comparison, a plan, ordinary code or a bug fix",
            "careful reasoning: a proof, a puzzle, a system design or multi-part debugging",
        ],
    },
    "tiers_choice": {
        "type": "choice",
        "instructions": "Which kind of answer does `request` need from an AI assistant?",
        "criteria": {
            "quick_answer": "a greeting, a fact, a definition, a short rewrite or a one-line answer",
            "several_steps": "an explanation, a comparison, a plan, ordinary code or a bug fix",
            "careful_reasoning": "a proof, a puzzle, a system design or multi-part debugging",
        },
    },
    "short": {"type": "noul", "instructions": "Can `request` be answered well in one or two sentences?"},
    "steps": {"type": "noul",
              "instructions": "Does a good answer to `request` explain several steps, compare options or include code?"},
    "deep": {"type": "noul",
             "instructions": "Does answering `request` need long, careful reasoning such as a proof, a puzzle, "
                             "a system design or multi-part debugging?"},
}
CHOICE_KEYS = ("quick_answer", "several_steps", "careful_reasoning")
L2 = 1.0
STEPS = 3000
RATE = 0.5


def fold_of(items: list[dict], folds: int = 5) -> list[int]:
    seen: dict[str, int] = {}
    out = []
    for item in items:
        out.append(seen.get(item["tier"], 0) % folds)
        seen[item["tier"]] = seen.get(item["tier"], 0) + 1
    return out


def fit_softmax(rows: list[list[float]], labels: list[int]):
    """Multinomial logistic regression with L2 (bias unpenalised), on standardised features."""
    import numpy as np

    x = np.asarray(rows, dtype=float)
    mean, std = x.mean(axis=0), x.std(axis=0)
    std[std == 0] = 1.0
    z = np.hstack([(x - mean) / std, np.ones((len(x), 1))])
    y = np.eye(3)[labels]
    w = np.zeros((z.shape[1], 3))
    for _ in range(STEPS):
        logits = z @ w
        logits -= logits.max(axis=1, keepdims=True)
        p = np.exp(logits)
        p /= p.sum(axis=1, keepdims=True)
        grad = z.T @ (p - y)
        grad[:-1] += L2 * w[:-1]
        w -= RATE * grad / len(z)
    return mean, std, w


def predict_softmax(model, row: list[float]) -> int:
    import numpy as np

    mean, std, w = model
    z = np.append((np.asarray(row, dtype=float) - mean) / std, 1.0)
    return int(np.argmax(z @ w))


def cv_softmax(items: list[dict], rows: list[list[float]]) -> list[int]:
    labels = [TIERS.index(item["tier"]) for item in items]
    folds = fold_of(items)
    out = [0] * len(items)
    for fold in range(5):
        train = [i for i in range(len(items)) if folds[i] != fold]
        model = fit_softmax([rows[i] for i in train], [labels[i] for i in train])
        for i in range(len(items)):
            if folds[i] == fold:
                out[i] = predict_softmax(model, rows[i])
    return out


def heuristic_features(text: str) -> list[float]:
    level = AgentOrchestrator._complexity(text)
    return [math.log(1 + len(text.split())), float(level == 1), float(level == 2), float(base.explicit_ultra(text))]


def score_with_laya(items: list[dict], models: Path, device: str) -> dict:
    import laya
    import torch

    questions = {"preset": laya.router_questions()["difficulty"], **QUESTIONS}
    out: dict = {"device": device, "torch": torch.__version__, "laya": getattr(laya, "__version__", "?"),
                 "checkpoints": {}}
    for name, path in {"english": models, "multilingual": models / "multilingual"}.items():
        agent = laya.load(str(path), device=device)
        for item in items[: base.WARMUP]:
            agent.predict({"request": item["text"]}, questions)
        features, times = [], []
        for item in items:
            runs = []
            for _ in range(base.REPEATS):
                tick = time.perf_counter()
                answers = agent.predict({"request": item["text"]}, questions)["answers"]
                runs.append((time.perf_counter() - tick) * 1000)
            times.append(statistics.median(runs))
            probs = answers["tiers_choice"]["probabilities"]
            features.append({
                "preset": float(answers["preset"]["score"]),
                "tiers_score": float(answers["tiers_score"]["score"]),
                **{key: float(probs[key]) for key in CHOICE_KEYS},
                "short": float(answers["short"]["noul"]),
                "steps": float(answers["steps"]["noul"]),
                "deep": float(answers["deep"]["noul"]),
            })
        ordered = sorted(times)
        out["checkpoints"][name] = {
            "ms_p50_all_six_questions": round(statistics.median(ordered), 1),
            "ms_p95_all_six_questions": round(ordered[int(0.95 * (len(ordered) - 1))], 1),
            "features": features,
        }
        del agent
    return out


def evaluate(items: list[dict], feats: list[dict]) -> dict:
    cued = lambda levels: [2 if base.explicit_ultra(i["text"]) else lv for i, lv in zip(items, levels)]  # noqa: E731
    laya_cols = ["preset", "tiers_score", *CHOICE_KEYS, "short", "steps", "deep"]
    laya_rows = [[f[c] for c in laya_cols] for f in feats]
    heur_rows = [heuristic_features(i["text"]) for i in items]
    variants = {
        "preset_cv_thresholds": cued(base.cross_validated(items, [f["preset"] for f in feats])),
        "tiers_score_cv_thresholds": cued(base.cross_validated(items, [f["tiers_score"] for f in feats])),
        "tiers_choice_argmax_unfitted": cued([max(range(3), key=lambda k: f[CHOICE_KEYS[k]]) for f in feats]),
        "tiers_choice_expected_cv_thresholds": cued(base.cross_validated(
            items, [f["several_steps"] + 2 * f["careful_reasoning"] for f in feats])),
        "nouls_logistic": cued(cv_softmax(items, [[f["short"], f["steps"], f["deep"]] for f in feats])),
        "all_laya_logistic": cued(cv_softmax(items, laya_rows)),
        "heuristic_signals_logistic_control": cued(cv_softmax(items, heur_rows)),
        "all_laya_plus_heuristic_logistic": cued(cv_softmax(items, [a + b for a, b in zip(laya_rows, heur_rows)])),
    }
    fitted = {"preset_cv_thresholds": 2, "tiers_score_cv_thresholds": 2, "tiers_choice_argmax_unfitted": 0,
              "tiers_choice_expected_cv_thresholds": 2, "nouls_logistic": 12, "all_laya_logistic": 27,
              "heuristic_signals_logistic_control": 15, "all_laya_plus_heuristic_logistic": 39}
    labels = [TIERS.index(i["tier"]) for i in items]
    report = {name: {**base.summarise(items, levels), "fitted_parameters": fitted[name]}
              for name, levels in variants.items()}
    report["_spearman_vs_label"] = {c: round(base.spearman([f[c] for f in feats], labels), 3)
                                    for c in ("preset", "tiers_score", "short", "steps", "deep")}
    eligible = [n for n in variants if report[n]["fast_sent_to_ultra"] <= 3]
    best = max(eligible, key=lambda n: (report[n]["accuracy"], -fitted[n])) if eligible else None
    report["_selected_by_frozen_rule"] = best
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", default=str(ROOT / "docs/evals/tier_choice_set.json"))
    parser.add_argument("--models", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--out", default=str(ROOT / "docs/evals/tier_choice_variants_results.json"))
    args = parser.parse_args()

    items = json.loads(Path(args.set).read_text(encoding="utf-8"))["items"]
    run = score_with_laya(items, Path(args.models), args.device)
    from laya import is_english

    english = run["checkpoints"]["english"]["features"]
    multi = run["checkpoints"]["multilingual"]["features"]
    routed = [e if is_english(i["text"]) else m for i, e, m in zip(items, english, multi)]
    results = {
        "items": len(items), "device": run["device"], "torch": run["torch"], "laya": run["laya"],
        "questions": QUESTIONS,
        "timing": {name: {k: v for k, v in data.items() if k != "features"}
                   for name, data in run["checkpoints"].items()},
        "heuristic": base.summarise(items, [AgentOrchestrator._complexity(i["text"]) for i in items]),
        "english": evaluate(items, english),
        "routed": evaluate(items, routed),
        "per_item": [{"id": i["id"], "tier": i["tier"],
                      "english": {k: round(v, 4) for k, v in e.items()},
                      "multilingual": {k: round(v, 4) for k, v in m.items()}}
                     for i, e, m in zip(items, english, multi)],
    }
    Path(args.out).write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for key in ("timing", "heuristic", "english", "routed"):
        print(key, json.dumps(results[key], ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Offline comparison of the chat-tier choice: `_complexity` against Laya's difficulty score.

Never changes the production code; it only calls `AgentOrchestrator._complexity`, a pure
function. Without `laya` installed it reports the heuristic alone.

Run, from the repository root, in an environment that has `laya` (docs/laya-decision-model.md):
    python -I -B docs/evals/tier_choice_experiment.py --models <dir with model.safetensors> --device cpu
"""
from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from laptop_agent.agents.orchestrator import AgentOrchestrator  # noqa: E402

TIERS = ("fast", "smart", "ultra")
# Frozen before the first run (docs/laya-decision-model.md): an explicit request for depth or
# the big model keeps the final say; topic words such as "architecture" are not requests.
EXPLICIT_CUES = (
    "use the big model", "ultra model", "think hard", "think carefully", "in depth", "in-depth",
    "step by step", "step-by-step", "show your work", "show your reasoning", "deep dive",
    "thorough", "comprehensive", "rigorous",
)
# Pre-registered mapping from Laya's expected difficulty (0-3) to a tier.
THRESHOLDS = (1.0, 2.2)
REPEATS = 3
WARMUP = 5


def explicit_ultra(text: str) -> bool:
    lowered = text.lower()
    return any(cue in lowered for cue in EXPLICIT_CUES)


def to_level(score: float, thresholds: tuple[float, float]) -> int:
    low, high = thresholds
    return 0 if score < low else (1 if score <= high else 2)


def spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=values.__getitem__)
        result = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                result[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return result

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    var = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return cov / var if var else 0.0


def summarise(items: list[dict], predicted: list[int]) -> dict:
    labels = [TIERS.index(item["tier"]) for item in items]
    confusion = [[0] * 3 for _ in range(3)]
    for truth, guess in zip(labels, predicted):
        confusion[truth][guess] += 1
    correct = sum(t == g for t, g in zip(labels, predicted))
    foreign = [(t, g) for item, t, g in zip(items, labels, predicted) if item["lang"] != "en"]
    return {
        "accuracy": round(correct / len(items), 3),
        "recall": {tier: round(confusion[i][i] / max(1, sum(confusion[i])), 3) for i, tier in enumerate(TIERS)},
        "confusion_rows_truth_cols_predicted": {tier: confusion[i] for i, tier in enumerate(TIERS)},
        "fast_sent_to_ultra": confusion[0][2],
        "ultra_sent_to_fast": confusion[2][0],
        "non_english_accuracy": round(sum(t == g for t, g in foreign) / max(1, len(foreign)), 3),
    }


def best_thresholds(items: list[dict], scores: list[float]) -> tuple[tuple[float, float], float]:
    """The thresholds that fit THIS set best: an optimistic ceiling, not a held-out result."""
    labels = [TIERS.index(item["tier"]) for item in items]
    grid = [round(0.05 * i, 2) for i in range(61)]
    best, best_acc = THRESHOLDS, -1.0
    for low in grid:
        for high in grid:
            if high < low:
                continue
            acc = sum(to_level(s, (low, high)) == t for s, t in zip(scores, labels)) / len(items)
            if acc > best_acc:
                best, best_acc = (low, high), acc
    return best, round(best_acc, 3)


def cross_validated(items: list[dict], scores: list[float], folds: int = 5) -> list[int]:
    """Held-out levels: each request's thresholds are fitted on the other folds only.

    Folds are assigned round-robin within each tier, so every fold keeps the set's mix."""
    seen: dict[str, int] = {}
    fold_of = []
    for item in items:
        fold_of.append(seen.get(item["tier"], 0) % folds)
        seen[item["tier"]] = seen.get(item["tier"], 0) + 1
    levels = [0] * len(items)
    for fold in range(folds):
        train = [i for i in range(len(items)) if fold_of[i] != fold]
        fitted, _ = best_thresholds([items[i] for i in train], [scores[i] for i in train])
        for i in range(len(items)):
            if fold_of[i] == fold:
                levels[i] = to_level(scores[i], fitted)
    return levels


def peak_working_set_mb() -> float | None:
    if sys.platform != "win32":
        return None

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    kernel32 = ctypes.windll.kernel32
    # Without these the pseudo-handle is truncated to a 32-bit int and the call fails.
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return round(counters.PeakWorkingSetSize / 2**20, 1)


def run_laya(items: list[dict], models: Path, device: str) -> dict:
    import laya
    import torch

    question = {"difficulty": laya.router_questions()["difficulty"]}
    checkpoints = {"english": models, "multilingual": models / "multilingual"}
    out: dict = {"device": device, "torch": torch.__version__, "threads": torch.get_num_threads(),
                 "laya": getattr(laya, "__version__", "?"), "checkpoints": {}}
    for name, path in checkpoints.items():
        if device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        start = time.perf_counter()
        agent = laya.load(str(path), device=device)
        load_s = time.perf_counter() - start
        for item in items[:WARMUP]:
            agent.predict({"request": item["text"]}, question)
        scores, times = [], []
        for item in items:
            runs = []
            for _ in range(REPEATS):
                tick = time.perf_counter()
                answer = agent.predict({"request": item["text"]}, question)
                runs.append((time.perf_counter() - tick) * 1000)
            scores.append(float(answer["answers"]["difficulty"]["score"]))
            times.append(statistics.median(runs))
        ordered = sorted(times)
        out["checkpoints"][name] = {
            "load_seconds": round(load_s, 2),
            "ms_p50": round(statistics.median(ordered), 1),
            "ms_p95": round(ordered[int(0.95 * (len(ordered) - 1))], 1),
            "peak_cuda_mb": round(torch.cuda.max_memory_allocated() / 2**20, 1) if device == "cuda" else None,
            "scores": scores,
        }
        del agent
    out["peak_working_set_mb"] = peak_working_set_mb()
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", default=str(ROOT / "docs/evals/tier_choice_set.json"))
    parser.add_argument("--models", help="directory holding the English checkpoint and multilingual/")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--out", default=str(ROOT / "docs/evals/tier_choice_results.json"))
    args = parser.parse_args()

    items = json.loads(Path(args.set).read_text(encoding="utf-8"))["items"]
    heuristic = [AgentOrchestrator._complexity(item["text"]) for item in items]
    results: dict = {"items": len(items), "thresholds_preregistered": THRESHOLDS,
                     "heuristic": summarise(items, heuristic), "per_item": []}
    laya_run = run_laya(items, Path(args.models), args.device) if args.models else None
    if laya_run:
        from laya import is_english

        english = laya_run["checkpoints"]["english"]["scores"]
        multi = laya_run["checkpoints"]["multilingual"]["scores"]
        routed = [e if is_english(item["text"]) else m for item, e, m in zip(items, english, multi)]
        labels = [TIERS.index(item["tier"]) for item in items]
        variants = {"english": english, "multilingual": multi, "routed": routed}
        results["laya"] = {key: value for key, value in laya_run.items() if key != "checkpoints"}
        results["laya"]["timing"] = {name: {k: v for k, v in data.items() if k != "scores"}
                                     for name, data in laya_run["checkpoints"].items()}
        for name, scores in variants.items():
            alone = [to_level(s, THRESHOLDS) for s in scores]
            combined = [2 if explicit_ultra(item["text"]) else level for item, level in zip(items, alone)]
            fitted, ceiling = best_thresholds(items, scores)
            held_out = cross_validated(items, scores)
            held_out_cued = [2 if explicit_ultra(item["text"]) else level for item, level in zip(items, held_out)]
            results["laya"][name] = {
                "spearman_vs_label": round(spearman(scores, labels), 3),
                "mean_score_by_tier": {tier: round(statistics.fmean(s for s, t in zip(scores, labels) if t == i), 3)
                                       for i, tier in enumerate(TIERS)},
                "alone": summarise(items, alone),
                "with_explicit_cues": summarise(items, combined),
                "best_thresholds_on_this_set": {"thresholds": fitted, "accuracy": ceiling},
                "cross_validated_thresholds": summarise(items, held_out),
                "cross_validated_with_explicit_cues": summarise(items, held_out_cued),
            }
        for item, h, e, m in zip(items, heuristic, english, multi):
            results["per_item"].append({"id": item["id"], "tier": item["tier"], "heuristic": TIERS[h],
                                        "laya_english": round(e, 3), "laya_multilingual": round(m, 3)})
    else:
        for item, h in zip(items, heuristic):
            results["per_item"].append({"id": item["id"], "tier": item["tier"], "heuristic": TIERS[h]})
    Path(args.out).write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {key: value for key, value in results.items() if key != "per_item"}
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

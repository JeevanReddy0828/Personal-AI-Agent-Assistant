# Tier choice: Laya against `_complexity` (2026-11-09)

**Decision: not adopted. The test whose rule was fixed in advance failed; a re-fit made afterwards
looks promising, but it is not evidence yet.**

- **The fixed rule failed.** With the thresholds fixed before the run (1.0 and 2.2), Laya plus the
  explicit cues chose the right tier for 52.3% (English checkpoint) or 54.2% (routed by language)
  of 155 requests, against 52.3% for the keyword heuristic. The proposal required +10 points.
- **Why it failed.** Laya's scores do rank the tiers sensibly (Spearman 0.66 for English), but they
  crowd into a narrow band: the tier means are 1.25, 1.62 and 1.95 on a 0–3 scale. The guessed
  cut-offs therefore sent 95% of the smart requests, and most of everything else, to smart.
- **The re-fit is promising.** Thresholds fitted by 5-fold cross-validation, chosen *after* seeing
  the first run and so exploratory, reached 69.0% with the English checkpoint plus cues. That fit
  sent 1 fast request to ultra, against the heuristic's 3.
- **It is too slow on this laptop's CPU.** The English checkpoint takes 486 ms at the median and
  635 ms at P95 per decision, over the 250 ms bar. The GPU has not been measured: it had no NVIDIA
  driver at the time.
- **The labels are not independent.** Claude wrote both the prompts and the labels. The next step
  is a fresh set labelled by Jeevan, tested with the fitted thresholds frozen in advance (below).

## Setup

- **Set.** [`tier_choice_set.json`](tier_choice_set.json): 155 requests (65 fast, 58 smart, 32
  ultra; 17 not English). It was frozen before either method ran, and its labels are provisional.
- **Heuristic.** `AgentOrchestrator._complexity` on `main` at `e251f50`.
- **Laya.**
  - Upstream `laya` 0.4.30, with torch 2.11.0 (a CUDA 12.8 build, run on the CPU in fp32 with 8
    threads, because no NVIDIA driver was installed).
  - Checkpoints `convaiinnovations/laya` (English) and its `multilingual/` folder.
  - The question is `router_questions()["difficulty"]`, with the state `{"request": text}`.
  - "Routed" uses `laya.is_english` to pick the checkpoint for each request.
- **Explicit cues.** `EXPLICIT_CUES` in the script, frozen before the run: a request for depth or
  for the big model forces ultra.
- **Timing.** Each request was timed 3 times after 5 warm-up calls. Each request contributes its
  median, and P50/P95 are taken across requests.

## Results

The thresholds row says which kind of result it is: fixed before the run, or fitted afterwards.

| Method | Thresholds | Accuracy | fast→ultra | ultra→fast | Recall fast / smart / ultra | Non-English (17) |
|---|---|---:|---:|---:|---|---:|
| `_complexity` (today) | n/a | **52.3%** | 3 | 7 | 85 / 22 / 41% | 41% |
| Laya English + cues | fixed in advance | 52.3% | 0 | 0 | 22 / 95 / 38% | 41% |
| Laya routed + cues | fixed in advance | 54.2% | 1 | 0 | 26 / 95 / 38% | 59% |
| Laya multilingual + cues | fixed in advance | 50.3% | 3 | 1 | 31 / 90 / 19% | 59% |
| Laya English + cues | cross-validated (after the fact) | **69.0%** | 1 | 2 | 77 / 69 / 53% | 65% |
| Laya routed + cues | cross-validated (after the fact) | 68.4% | 2 | 2 | 82 / 60 / 56% | 53% |
| Laya multilingual + cues | cross-validated (after the fact) | 57.4% | 8 | 7 | 66 / 53 / 47% | 59% |

The confusion matrices below have the true tier in rows and the predicted fast / smart / ultra
in columns:

- `_complexity`: fast 55 / 7 / 3, smart **44** / 13 / 1, ultra 7 / 12 / 13.
- Laya English + cues, cross-validated: fast 50 / 14 / 1, smart 17 / 40 / 1, ultra 2 / 13 / 17.

Latency and memory, on this laptop's CPU (8 threads, fp32):

| Checkpoint | Load | P50 per decision | P95 per decision |
|---|---:|---:|---:|
| English (421M) | 7.8 s (15.9 s on the first, cold load) | 486 ms | 635 ms |
| Multilingual (322M) | 2.3 s | 142 ms | 189 ms |

The process that loaded both checkpoints in turn peaked at a 3.5 GB working set.

## What it shows

1. **The heuristic's main error is sending smart requests to fast.** 44 of the 58 smart requests
   contain none of its words ("how does a refrigerator keep things cold", "should I learn rust or
   go first"). Laya catches most of them.
2. **Laya is not fooled by keyword traps.** "tell me a riddle" scores 1.25, "is ultra a prefix or a
   word" 1.16, and "what style of architecture is the eiffel tower" 1.48. All of these land in fast
   or smart, where the heuristic sends all three to ultra.
3. **It is noisy on single requests.** "how do you pronounce quinoa" scores 2.13, while "derive the
   formula for the sum of a geometric series" scores 1.43 and the twelve-coin puzzle 1.46. No
   threshold catches an ultra request that scores that low, which is why ultra recall stays near
   half even after fitting.
4. **The multilingual checkpoint is faster but weaker.** Its Spearman correlation is 0.44, and once
   fitted it sends 8 fast requests to ultra. That fails the bar of "no more than the heuristic".
5. **There are too few non-English requests to conclude anything.** With 17 of them, the English
   checkpoint scoring better than language routing, which goes against upstream's warning, is
   within noise.

## What would change the decision

- **Speed on the GPU.** The English checkpoint needs a P95 under 250 ms on the 3050 Ti. To measure
  it, re-run with `--device cuda` once `nvidia-smi` lists the GPU, writing
  `tier_choice_results_cuda.json`.
- **A confirmation run on a fresh set.** Write new requests and have **Jeevan** label them, then
  test the English checkpoint with these thresholds, **frozen now**: `(1.55, 2.05)`, the best fit on
  this whole set, plus `EXPLICIT_CUES`. Laya must reach at least +10 points over the heuristic and
  send no more fast requests to ultra.
- **Otherwise, fine-tuning.** Training on our own labels is a separate and larger decision
  (`../laya-decision-model.md`).

## Reproduce

These commands assume a throwaway venv with `laya` and torch, and the two checkpoints downloaded
to `<dir>` (weights, configs and tokenizers only):

```powershell
python -I -B docs/evals/tier_choice_experiment.py                                   # heuristic only, no dependencies
<venv>\Scripts\python.exe -I -B docs/evals/tier_choice_experiment.py --models <dir> --device cpu
<venv>\Scripts\python.exe -I -B docs/evals/tier_choice_experiment.py --models <dir> --device cuda --out docs/evals/tier_choice_results_cuda.json
```

The scores are deterministic on the CPU, so a re-run reproduces every accuracy figure above. The
timings vary from run to run: the first run gave a P50 of 523 ms for the English checkpoint and
150 ms for the multilingual one.

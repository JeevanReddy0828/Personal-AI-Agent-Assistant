# Tier choice: better questions and combined signals (2026-11-10)

**Decision: no variant is adopted yet.**

- **Better wording helps.** The best combination reaches 79.4% (cross-validated).
- **But a cheap control nearly matches it.** Re-weighting the keyword heuristic's own signals,
  with no Laya at all, reaches 76.8%.
- **And this set is biased by length.** Prompt length nearly gives the label away, which flatters
  both numbers.

A fresh set, written to break the link between length and tier, decides between them.

Follows [`tier_choice_experiment.md`](tier_choice_experiment.md), on the same set and the GPU.

## Findings

- **Questions worded for our tiers beat the generic preset.** Three findings:
  - A score whose levels describe our three tiers ranks requests better: Spearman 0.77, against
    0.66 for the preset.
  - The same descriptions as a `choice`, read as an expected level (`P(several steps) +
    2·P(careful reasoning)`) with two cross-validated thresholds, reach **74.8%** and send no
    ultra request to fast. The preset reached 67.7% in the same run.
  - Taken with nothing fitted at all, the choice's top answer gives 69.0%, but it sends 4 fast
    requests to ultra, over the bar of 3.
- **Combining everything gives 79.4%.** A logistic regression on all six Laya answers plus the
  heuristic's signals reaches 79.4% (English checkpoint, explicit cues, cross-validated), with 1
  fast request sent to ultra. The rule frozen before the run selects it.
- **The control is the most important row.** A logistic regression on the heuristic's own signals
  (word count, its level and the cue flag) reaches **76.8% without Laya**. Laya adds 2.6 points on
  top of that, which is within the noise of 155 requests: one request is 0.65 points, and the
  standard error of an accuracy near 78% here is about 3 points.
- **Why the control is so strong.** On this set, length alone with two cut-offs (10 and 17
  words) scores 72.3% (in-sample). Claude's fast prompts have a median of 6 words, smart ones 12
  and ultra ones 19. Real requests will not separate that cleanly, so the control and every Laya
  number are probably optimistic. Laya is not only measuring length: with length held fixed, its
  rank correlation with the label is about 0.5. The tier-worded choice leans on length a little
  more than the preset (Spearman 0.61 with word count, against 0.49).
- **"Can it be answered in one or two sentences?" carries no signal** (Spearman −0.02): Laya gives
  every request about the same answer.
- **Speed is fine.** On the GPU, all six questions in one pass take 94.7 ms at the median and
  127 ms at P95 (English). One question alone costs about what the preset did, 54 ms.

## Results (English checkpoint, explicit cues applied, cross-validated unless noted)

| Variant | Fitted values | Accuracy | fast→ultra | ultra→fast | Recall fast / smart / ultra |
|---|---:|---:|---:|---:|---|
| `_complexity` today | 0 | 52.3% | 3 | 7 | 85 / 22 / 41% |
| Preset difficulty score, thresholds | 2 | 67.7% | 1 | 2 | 75 / 67 / 53% |
| Tier-worded score, thresholds | 2 | 66.5% | 0 | 0 | 78 / 53 / 66% |
| Tier-worded choice, top answer, nothing fitted | 0 | 69.0% | 4 | 3 | 77 / 72 / 47% |
| **Tier-worded choice, expected level, thresholds** | 2 | **74.8%** | 3 | 0 | 80 / 64 / 84% |
| Three yes/no questions, logistic | 12 | 68.4% | 1 | 3 | 85 / 62 / 47% |
| All six Laya answers, logistic | 27 | 77.4% | 0 | 0 | 88 / 69 / 72% |
| **Heuristic signals only, logistic (control, no Laya)** | 15 | **76.8%** | 2 | 1 | 85 / 74 / 66% |
| All Laya answers + heuristic signals, logistic (selected) | 39 | 79.4% | 1 | 0 | 85 / 74 / 78% |

Routing by language is lower than or equal to the English checkpoint for every variant; 17
non-English requests are too few to separate the two. Every figure is in
[`tier_choice_variants_results.json`](tier_choice_variants_results.json).

## How it was run

- **Questions.** Six questions were asked in one forward pass per request: the preset; a
  tier-worded `score` and `choice`; and three yes/no questions (`short`, `steps`, `deep`). Their
  wording is in [`tier_choice_variants.py`](tier_choice_variants.py).
- **Fitting.** Anything fitted used 5-fold cross-validation, with the folds assigned round-robin
  within each tier, the same scheme as before. The logistic regressions have an L2 penalty of 1.0,
  standardised features, and fixed steps and learning rate.
- **Order of commits.** The variants, the fitting and the selection rule were committed in
  `702a7aa` before the first Laya run. The control uses no Laya output and was sanity-run once
  before that commit, to check the fitting code; nothing changed afterwards.
- **What has been looked at.** The set has now been looked at twice, so these results are
  exploratory.

## Next: a confirmation run on a fresh set

The fresh set must break the length link. It needs long requests that are easy (pasted text to
tidy, a long story ending in a simple question) and short requests that are hard ("is P equal to
NP", "prove there are infinitely many primes"). Jeevan labels it before any candidate is scored on
it.

Each candidate below is fitted on all 155 requests here by the committed code, which is
deterministic, so it is fixed before any fresh label exists:

1. **Heuristic signals, logistic (no Laya).** If this holds, the app gets the gain with no model
   and no GPU.
2. **Tier-worded choice, expected level,** with thresholds `(0.85, 1.05)` plus the explicit cues:
   one question and two numbers.
3. **All Laya answers + heuristic signals, logistic.**
4. **Baseline: the preset,** with `(1.55, 2.05)` from `tier_choice_experiment.md`.

The winner is chosen by the same rule: the highest accuracy with no more fast→ultra than
`_complexity`. It is adopted only if it beats `_complexity` by 10 points or more.

The fresh set is [`tier_choice_fresh_set.json`](tier_choice_fresh_set.json): 120 requests, shuffled
and unlabelled. By the drafter's intended tiers, word count correlates with the tier at 0.14,
against 0.74 here, and the old length cut-offs would score 38%. The scorer is
[`tier_choice_confirm.py`](tier_choice_confirm.py). It refuses to run until every `tier` and
`labelled_by` are filled in, and it reports the length–label correlation on the real labels.

## Reproduce

```powershell
<venv>\Scripts\python.exe -I -B docs/evals/tier_choice_variants.py --models <dir> --device cuda
```

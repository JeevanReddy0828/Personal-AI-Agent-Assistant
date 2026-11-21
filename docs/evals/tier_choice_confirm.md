# Tier choice: the confirmation run (2026-11-11)

**Decision: the rule fixed in advance passes Laya's preset difficulty question, at the thresholds
frozen in advance, `(1.55, 2.05)`, with the explicit cues.** Laya is worth wiring in behind a
switch, as `docs/laya-decision-model.md` describes. The details:

- **Result.** On 120 fresh requests it chose the right tier for **62.5%**, against **50.8%** for
  `_complexity`, an 11.7-point gain. It sent no fast request to ultra (`_complexity` sent none
  either).
- **The margin is real, but only just.** It is right on 29 requests where `_complexity` is wrong,
  and wrong on 15 where `_complexity` is right: exact McNemar p = 0.049.
- **The variants that looked best before did not hold up.** Their gains were the first set's
  length shortcut:
  - Re-weighting the heuristic's own signals, without Laya, fell from 76.8% (cross-validated on
    the first set) to **37.5%** here, worse than today's heuristic.
  - The tier-worded question (60.8%) and the full combination (60.0%) both sent fast requests to
    ultra, so the rule excluded them.

## The fresh set

- **[`tier_choice_fresh_set.json`](tier_choice_fresh_set.json):** 120 requests written so that
  length does not give away the tier. On the final labels, word count correlates with the tier
  at 0.18, against 0.74 on the first set, and the first set's length cut-offs score 35.8% here.
- **Labels.** Jeevan labelled q001–q024 on the labelling page. At his request, Claude labelled
  q025–q120 by the same rule, without seeing any candidate's answer on this set. The result is
  45 fast, 50 smart and 25 ultra.
- **The two labellers do not always agree.** On Jeevan's 24, Claude's own labels would agree on
  18 (75%). Jeevan put three proofs or explanations of known results in smart, where Claude
  would put them in ultra (q008 infinitely many primes, q010 no formula past degree four, q018
  the Tower of Hanoi minimum). He also put three simple suggestions or facts in smart, where
  Claude would put them in fast (q004 a comedy to watch, q022 a gift idea, q023 how many
  planets). Two people agreeing three times in four is a rough ceiling for any classifier
  judged against one of them.
- **No candidate was tuned on these requests.** Every candidate was fitted on the first set only,
  by code committed before these labels existed: `702a7aa` for the variants, `bc22b3c` for the
  scorer.

## Results

| | Fitted values | Accuracy | fast→ultra | ultra→fast | Recall fast / smart / ultra | Non-English (14) |
|---|---:|---:|---:|---:|---|---:|
| `_complexity` today | 0 | 50.8% | 0 | 4 | 87 / 24 / 40% | 43% |
| Length alone, the first set's cut-offs (10 and 17 words) | 2 | 35.8% | 12 | 9 | 44 / 26 / 40% | 36% |
| 1. Heuristic signals, logistic (no Laya) | 15 | 37.5% | 12 | 7 | 40 / 28 / 52% | 50% |
| 2. Tier-worded choice, expected level, `(0.85, 1.05)` | 2 | 60.8% | 4 | 2 | 76 / 42 / 72% | 50% |
| 3. All Laya answers + heuristic signals, logistic | 39 | 60.0% | 1 | 3 | 80 / 46 / 52% | 50% |
| **4. Preset difficulty, `(1.55, 2.05)` (selected)** | 2 | **62.5%** | **0** | 5 | 80 / 56 / 44% | 36% |

The confusion matrices below have the true tier in rows and the predicted fast / smart / ultra
in columns:

- `_complexity`: fast 39 / 6 / 0, smart **36** / 12 / 2, ultra 4 / 11 / 10.
- Selected: fast 36 / 9 / 0, smart 17 / 28 / 5, ultra 5 / 9 / 11.

Split by labeller:

| Labels by | `_complexity` | Selected | Tier-worded choice |
|---|---:|---:|---:|
| Jeevan (24) | 37.5% | 54.2% | 54.2% |
| Claude (96) | 54.2% | 64.6% | 62.5% |

Speed: all six questions in one pass took 83.5 ms at the median and 97.4 ms at P95 on the GPU.
The selected candidate needs only its one question, which earlier measured 54 ms.

## What it shows

1. **The gain is in smart requests.** `_complexity` sends 36 of 50 smart requests to fast. The
   selected candidate sends 17, raising smart recall from 24% to 56%.
2. **Ultra is still the weak spot.** The selected candidate sends 5 ultra requests to fast, one
   more than `_complexity`:
   - two proofs that are not in English (q064 in Hindi, q101 in Telugu);
   - three short maths or puzzle requests (q074 the snail, q092 integer solutions, q110 domino
     tilings).

   The English checkpoint reading non-Latin scripts is the likely cause of the first two, which
   matches upstream's warning. Routing by script was not a frozen candidate, so it is a hypothesis
   for the next set, not a result.
3. **The first set's leaderboard did not survive.** The candidates that learned length (1 and 3)
   or used the tier wording (2) lost ground; the plainest one held up. The more a candidate was
   fitted to the first set, the less of its gain carried over.

## Limits

- **The labels are not independent.** 80% of them are Claude's, and Claude also wrote every
  request. The candidates were frozen before labelling, and the result points the same way on
  Jeevan's 24, but those 24 are too few to stand alone.
- **This is chat requests only.** The tier choice runs only after routing has made a turn chat;
  commands never reach it.
- **p = 0.049 on 120 requests.** It is a pass, not a wide margin.

## Reproduce

```powershell
<venv>\Scripts\python.exe -I -B docs/evals/tier_choice_confirm.py --models <dir> --device cuda
```

The results are in [`tier_choice_confirm_results.json`](tier_choice_confirm_results.json).

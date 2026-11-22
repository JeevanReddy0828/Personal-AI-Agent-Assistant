# Tier choice: outcome labels (2026-11-21)

**Decision: Laya stays off.** On labels that record which model's answer was actually good
enough, Laya's preset difficulty score picks the right tier for **47.6%** of the 275 requests,
`_complexity` for **66.2%**, and always choosing fast for **93.5%**. Laya sends 131 of the 257
requests the fast model answers well to a bigger model. There are also too few requests that
need a bigger model (18) to fine-tune it.

## How the labels were made

- **The same 275 requests** as before: [`tier_choice_set.json`](tier_choice_set.json) (155) and
  [`tier_choice_fresh_set.json`](tier_choice_fresh_set.json) (120). Their `tier` labels were
  opinions about how hard a request seemed; these labels are about what happened.
- **The three tiers were three different models:** fast `nemotron-3.5-lightning-30b-a3b`, smart
  `nemotron-3.5-super-vl-120b-a12b`, ultra `nemotron-3-ultra-550b-a55b`, all on NVIDIA's hosted
  API. Fast and smart answer with the app's own chat system prompt, reasoning off, temperature
  0.6 and up to 2048 tokens, as the app calls them.
- **The fast model answers three times, the smart model once.** The ultra model, with
  reasoning on, judges all four answers at once, shuffled, each as sufficient or not, on its own
  and not against the others.
- **The label** is fast when at least 2 of the 3 fast answers are sufficient, else smart when the
  smart answer is, else ultra.
- [`tier_outcome_labels.py`](tier_outcome_labels.py) makes the labels (calls start 12 s apart, a
  rate limit waits 60 s and retries up to three times). [`tier_outcome_labels.json`](tier_outcome_labels.json)
  holds each request's label, its count of sufficient fast answers and the smart verdict. The
  answers themselves are not committed.

## Why three fast answers

A first run took one answer from each model. A re-run of 54 of those requests showed that one
answer is close to a coin flip on a hard request:

- 16 of the 26 requests labelled smart or ultra changed label, 15 of them to fast; 22 of the 26
  labelled fast kept it.
- The smart model's verdict was the same both times on 49 of 52 requests (2 were rate-limited);
  the fast model's on only 33 of 52.

With three answers, the fast model's results were 3 of 3 sufficient on 233 requests, 2 of 3 on
24, 1 of 3 on 9 and 0 of 3 on 9. The labels are **257 fast, 16 smart and 2 ultra**.

## Results

From [`tier_outcome_score.py`](tier_outcome_score.py), which reads only committed files and calls
no model. Laya's choice is its preset score at the app's thresholds `(1.55, 2.05)` with the
explicit cues, from the recorded GPU runs; `_complexity` is as recorded in the same runs.

| | Right tier | Needed a bigger model, sent up | Fast was enough, sent up anyway |
|---|---:|---:|---:|
| Laya, preset `(1.55, 2.05)` | 47.6% | 10 of 18 | 131 of 257 |
| `_complexity` today | 66.2% | 9 of 18 | 81 of 257 |
| Always fast | 93.5% | 0 of 18 | 0 of 257 |

The confusion matrices have the label in rows and the choice fast / smart / ultra in columns:

- Laya: fast 126 / 103 / 28, smart 8 / 3 / 5, ultra 0 / 0 / 2.
- `_complexity`: fast 176 / 54 / 27, smart 8 / 6 / 2, ultra 1 / 1 / 0.

## What needed a bigger model

- **Puzzles and exact arithmetic:** the river crossing, the snail in the well, the clock hands,
  the twelve coins, x² − y² = 45, the two trains.
- **Long code:** a thread-safe LRU cache, a lock-free queue, a tiny compiler (ultra), an
  async/await conversion; and a full plan with a code skeleton (ultra).
- **Short requests the fast model kept getting slightly wrong:** how long cooked rice keeps (it
  said 4–6 days), a spelling-only fix that changed more than spelling, the plural of mouse, a VS
  Code shortcut, a two-line note written as one line, whether "ultra" is a word. The judge's
  reasons name a real error in each, but nothing in the request says it is hard, so no router
  that reads only the request can be expected to catch these.

## Limits

- One judge, the largest model, graded every answer; it was not checked against a person.
- The models are hosted and change; a different fast model would give different labels.
- 18 requests that needed more than fast is a small sample, so the two routers' recall on them
  (10 and 9 of 18) is not a meaningful difference.

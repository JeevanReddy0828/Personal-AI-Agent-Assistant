# `docs/evals/` — offline evaluations of knowledge ranking and the tier choice

## Purpose

Reproducible experiments that decide whether a change to how `knowledge.py` picks a
passage, or how the chat tier is chosen, is worth shipping. Each one freezes its questions and pass rules **before** it is
run, measures the production scorer against a variant, and records the result, so the
decision can be checked later instead of re-argued. They never edit production code.

## How it connects

```
 fixture (.json, committed before the run) ──┐
                                             ├─> experiment script (.py) ──> results (.json)
 src/laptop_agent/knowledge.py (imported) ───┘                                   │
                                                          write-up (.md) <────────┘
```

The scripts import `laptop_agent.knowledge` read-only and try a variant scorer beside it.
The write-ups state the decision; the JSON files hold the exact corpus hashes and answers
so a rerun can be compared line by line.

## Usage

From the repository root, with `src` importable (the scripts add it themselves where
needed):

```powershell
python -B docs/evals/knowledge_windows.py        # fixed-window comparison
python -B docs/evals/definition_experiment.py    # definition-passage prior
```

`knowledge_windows.py` writes `knowledge_windows_results.json` next to itself; commit it
only when the run is meant to be the recorded result.

To add an experiment: commit the fixture and its pass rules first, then the script, then
the results and a short write-up whose first paragraph is the decision.

## Contents

| File | What it is |
|---|---|
| `definition_experiment.md` | Write-up (2026-10-05): a bonus for a document's opening definition was **not** put into production; it helped the original README questions but regressed a held-out corpus. |
| `definition_experiment.py` | The experiment behind it, run against the README alone, an illustrative user mix and a developer-history stress corpus; it reuses `knowledge_windows.py` as the frozen baseline. |
| `definition_heldout.json` | The frozen held-out fixture: eight synthetic subjects, each with a question and a pass rule. |
| `definition_semantic_judgments.json` | The graded judgments of each answer against those rules. |
| `definition_results.json` | Recorded runs (revision, fixture hash, answers). |
| `user_mix.json` | A synthetic mix of notes and generated research/advice/transcript records, used as an illustrative corpus. Not real user data. |
| `knowledge_windows.py` | Fixed-window evaluation of passage choice; never changes the ranking module. |
| `knowledge_after_note_move.md` | Write-up: rerun after moving one analytics note between README, CLAUDE and MEMORY; ranking unchanged. |
| `knowledge_after_note_move.json` | That rerun's recorded corpus hashes and answers. |
| `tier_choice_experiment.md` | Write-up (2026-11-09): the Laya decision model against `_complexity` for choosing the chat tier, on provisional labels ([proposal](../laya-decision-model.md)). |
| `tier_choice_set.json` | The frozen fixture: 155 hand-written chat requests labelled fast / smart / ultra, with the labelling rule. Labels are provisional until Jeevan reviews them. |
| `tier_choice_experiment.py` | The experiment: imports `AgentOrchestrator._complexity` read-only; with `laya` installed it also scores each request with both checkpoints. Not part of the test suite and needs no dependency to run the heuristic half. |
| `tier_choice_results_cpu.json` | The recorded CPU run: accuracy, confusion matrices, latency, memory and per-request scores. |
| `tier_choice_results_cuda.json` | The same run on the RTX 3050 Ti Laptop GPU (2026-11-10): the same scores to within 0.033, 54 ms median per decision. |
| `tier_choice_variants.md` | Write-up (2026-11-10): tier-worded questions and combined signals. The best reaches 79.4% cross-validated, but the heuristic's own signals re-fitted reach 76.8% and prompt length biases the set, so a fresh set decides. |
| `tier_choice_variants.py` | The variants experiment: six Laya questions in one pass, cross-validated thresholds and logistic regressions, a no-Laya control and a frozen selection rule. |
| `tier_choice_variants_results.json` | Its recorded GPU run: every variant's accuracy and errors, timings and per-request answers. |

## Read next

- `docs/design/architecture-map.md` → the `knowledge.py` entry (which ranking changes were
  measured and rejected, and why).
- `src/laptop_agent/README.md` for where `knowledge.py` sits.

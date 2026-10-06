# `docs/evals/` — offline evaluations of knowledge ranking

## Purpose

Reproducible experiments that decide whether a change to how `knowledge.py` picks a
passage is worth shipping. Each one freezes its questions and pass rules **before** it is
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

## Read next

- `CLAUDE.md` → the `knowledge.py` entry in "Architecture (map)" (which ranking changes were
  measured and rejected, and why).
- `src/laptop_agent/README.md` for where `knowledge.py` sits.

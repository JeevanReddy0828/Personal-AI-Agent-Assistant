# `docs/` — design contracts, evaluations and images

## Purpose

Documents that do not belong next to one module: the written contracts the analytics code
must keep, the offline evaluations behind decisions that changed (or deliberately did not
change) the knowledge ranking, the review evidence for UI changes, and the screenshots in
the repository's front page.

## How it connects

- `analytics.md` and `forecasting.md` are the contracts for `src/laptop_agent/analytics/`;
  the CSV tools in `src/laptop_agent/tools/` (`forecast.py`, `diagnostics.py`) are written
  to them, and their tests check the promises they make (no causal claims, no interval
  without bounds, accuracy only from data the method never saw).
- `evals/` holds reproducible experiments on `knowledge.py`'s passage ranking. They import
  the production code but never change it.
- `review/` is written by the browser regression tests and by review notes.
- `screenshots/` is referenced by the root `README.md`.

## Usage

Read `analytics.md` / `forecasting.md` **before** changing anything in `analytics/` or the
CSV tools. Re-run an evaluation from the repository root, for example
`python -B docs/evals/knowledge_windows.py`.

## Contents

| Path | What it is |
|---|---|
| `design/` | The detailed design notes that used to fill `CLAUDE.md`, one file per subsystem; see its README. |
| `analytics.md` | Contract for `analytics/diagnostics.py`: drivers fitted on a prefix and scored on an untouched tail, associations not causes, robust anomalies with unscored zero-MAD values. |
| `forecasting.md` | Contract for `analytics/forecast.py`: season and tuning from an early prefix, method choice against two baselines, calibrated intervals, and what to say when data is insufficient. |
| `evals/` | Offline knowledge-ranking experiments, their fixtures and recorded results. |
| `review/` | Screenshots and notes produced while reviewing UI changes. |
| `screenshots/` | Images used in the root README. |

## Read next

- `design/analytics.md` for the wiring notes, and `design/README.md` for every design note.
- Each sub-folder's README.

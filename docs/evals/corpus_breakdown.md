# Corpus breakdown — 2026-10-02

The original `single_gold_document` score combined 12 README questions with three analytics
questions, each queried against its target document alone. It was **not** a README-only
15-question score. This report gives each corpus its own denominator.

Run `python -B docs/evals/corpus_breakdown.py` in this repository. It uses the existing
frozen questions/anchors and production scorer, reading documentation from Git at main
0ce6847 and the note-move commit 6ebd8e0. No scoring variant is enabled.

| Corpus | Questions | Correct target document | Exact top window | Gold anchors within up to four windows |
| --- | ---: | ---: | ---: | ---: |
| README alone | 12 | 12/12 | 3/12 | 7/12 |
| Illustrative user mix | 15 | 15/15 | 4/15 | 9/15 |
| Repository developer-history stress | 15 | 0/15 | 0/15 | 0/15 |

**All counts are identical before and after the note move.** Complete answers, scores,
document lengths/hashes, corpus membership and the fixture hash are recorded in
`corpus_breakdown_results.json`.

## What each corpus means

README alone is the original reported setup; analytics-only questions are excluded rather
than scored against a document that cannot answer them. The user-mix fixture has seven
records: README, the indexed analytics API document, an ordinary project checklist, two
generated research overviews (vector retrieval and home-network access), generated job-search
advice, and a Python-testing transcript. It exercises ordinary file/research/advice/youtube
source kinds and topical overlap without including developer bug discussions. All five
additional texts were written and frozen before the first run; `user_mix.json` preserves them.

This is a **synthetic illustrative mix**, not private user data, a sample of real production
stores, or proof of performance at scale. In particular, its short generated notes do not
reproduce a store with dozens of long research dumps. Its score must not be called typical
user accuracy. A broader representative corpus needs agreed samples and independent labels.

The developer-history stress corpus contains README, CLAUDE, MEMORY and analytics docs.
It answers all questions from CLAUDE.md, including text discussing the identity-query bug.
This failure is specific to that corpus; it must not be presented as the original user's
README-only problem.

## Interpretation

In this user-mix fixture, document choice is correct for all questions, but the desired
passage often is not. Test passage selection next; these measurements do not justify a
broad document-ranking change. Both identity queries still select the README firewall
passage. The note relocation remains useful for readers but is insufficient by itself.

These scores are strict frozen-anchor retrieval scores, not semantic correctness ratings.
Alternative valid voice commands and prose explanations can miss a gold anchor. Keep the
unchanged anchors for reproducibility and add independently labelled semantic judgments as
a separate measure, rather than adjusting this test after observing answers. Neither a
ranking bonus nor any production behavior was changed for this report.

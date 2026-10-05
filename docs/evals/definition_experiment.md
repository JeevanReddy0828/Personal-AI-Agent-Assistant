# Definition passage experiment — 2026-10-05

Decision: do not put the tested first-passage definition bonus into production. It raises
exact-window recall on the original README questions, but the precommitted semantic holdout
shows no gain on the single-subject or illustrative user corpus and creates clear regressions.
The developer-history corpus still fails to select the correct repository document for the
original questions. No production scorer changed.

The [fixture](definition_heldout.json) was committed at `7be1e80` before this measurement.
It names eight synthetic subjects, each with a question and a human-readable pass rule.
Atlas, Orchid and Nacre have opening definitions; Helios has release notes first; Kite has
administrative notes first; Morrow and Vega ask for a specific price or retention policy
rather than a generic definition; Cedar's notes contain no definition. We assessed the
first returned passage against those rules after inference and kept the [per-case answers]
(definition_results.json) and [judgments](definition_semantic_judgments.json). A result
passes only if it states the requested fact, or for Cedar acknowledges that its notes do
not define the subject. These labels are a manual reading of a small synthetic fixture,
not a calibrated production accuracy estimate.

| Corpus | Baseline semantic pass | Fixed 3× definition prior | Change |
|---|---:|---:|---:|
| One subject README at a time | 3/8 | 3/8 | 0 |
| Illustrative user mix | 2/8 | 2/8 | 0 |
| Developer history plus subjects | 1/8 | 2/8 | +1 |

In the single-subject panel, the prior fixes Atlas, Orchid and Nacre. It loses Kite's
later route-planner definition, Vega's 14-day retention answer, and Cedar's explicit
statement that the notes do not define Cedar. In the user mix it fixes Morrow's monthly
price and Nacre's definition, but loses Kite and Vega. The query `what is Morrow
subscription price` is a deliberate near miss: rewarding a generic opening definition
would answer the wrong part of the request.

The earlier frozen exact-window questions give a different, narrower signal:

| Corpus | Baseline first window | Fixed prior first window | Baseline within four | Prior within four |
|---|---:|---:|---:|---:|
| README alone (12 questions) | 3/12 | 5/12 | 7/12 | 9/12 |
| Illustrative user mix (15 questions) | 4/15 | 6/15 | 9/15 | 11/15 |
| Developer-history stress (15 questions) | 0/15 | 0/15 | 0/15 | 0/15 |

Those exact-window hits require the preselected text fragments, so they do not by
themselves judge whether an answer is useful. All 15 developer-history misses select
another document before passage ranking; this bonus cannot repair that document choice.
The held-out subject names are unique synthetic names, so that panel measures passage
selection more than document selection. Neither panel samples private user data.

Run `python -B docs/evals/definition_experiment.py` from the checkout. The script reads
repository documents at `1c6b263`, loads the frozen user-mix fixture and held-out subjects,
then evaluates the unchanged scorer and the one fixed in-memory variant from
`knowledge_windows.py`. It writes compact raw answers and counts. The committed semantic
judgments are separate because choosing whether a sentence satisfies a natural-language
rubric is human assessment. A later idea needs a new frozen fixture or untouched split;
retuning on these eight cases would turn them into training examples.

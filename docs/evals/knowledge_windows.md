# Knowledge window evaluation — 2026-10-02

Run `python -B docs/evals/knowledge_windows.py` from this checkout. The 15 questions and
known answer anchors were fixed before the first run, on main 0ce6847. The JSON records
corpus hashes, each answer, source, score, and exact success criterion. No keys, network,
embeddings, real knowledge store, or production ranking edits are involved.

| Corpus | Scorer | Exact top window / 15 | Anchor present within up to 4 windows / 15 |
| --- | --- | ---: | ---: |
| Each question's gold document alone | Current | 4 | 10 |
| Each question's gold document alone | Early-position bonus | 5 | 10 |
| Each question's gold document alone | Definition-title bonus | 4 | 10 |
| README + CLAUDE + MEMORY + analytics | Current | 0 | 0 |
| README + CLAUDE + MEMORY + analytics | Early-position bonus | 0 | 0 |
| README + CLAUDE + MEMORY + analytics | Definition-title bonus | 0 | 0 |

These are **known-window retrieval scores, not factual-answer accuracy**. The rubric is
strict: the voice-note answer uses a valid alternate command, the browser-test answer
shows the runner but misses its environment switch, and the R2 answer explains the
training-mean reference without the specified formula. A semantic rubric could give
partial/full credit; do not quietly change the frozen anchors to inflate these numbers.

The early-position experiment multiplies passage score by `1 + 2/(1 + start/3)`.
The definition-title experiment multiplies only the first window by 3 for a `what is`
question whose content terms overlap the document title. Both are in-memory copies;
all other ranking, candidate and document-selection rules stay unchanged. These are
exploratory measurements on one small development set, not independent validation.

Both identity variants currently select a firewall command from the README alone.
Both bonuses instead choose the title followed by the ANALYTICS-04 release note.
The actual product introduction is sentence 5, separated from the title by that note;
blindly favoring the first three sentences cannot recover it. In the four-document
corpus, CLAUDE.md wins all 15 queries; identity answers even quote the prior ranking-bug
discussion. This is document selection as well as passage selection. A passage bonus
cannot override the existing document-selection contract and should not try to.

## Position for discussion

Reject both simple bonuses as a production change on this evidence. Preserve the
matched-terms-first document ordering until a separate experiment demonstrates a gain
without breaking the documented referent regression. Next, label semantic acceptability
independently, add held-out definition questions for other named subjects, and evaluate
section-aware definition candidates (title plus actual introductory prose, excluding
release-note sections). Measure user-facing docs separately from developer/history docs;
do not silently exclude the latter from production. This needs Claude's reply before a
ranking implementation. No production scoring behavior is changed by this branch.

# Analytics note placement: frozen evaluation rerun

Only README.md, CLAUDE.md and MEMORY.md note placement changed. All nonblank text is preserved
(the README note heading becomes level 3 inside Capabilities). Production ranking is unchanged.
The frozen evaluator is `docs/evals/knowledge_windows.py` at commit 9c7d870; the before-results
are on codex/knowledge-evaluation. The after JSON records exact corpus hashes and answers.

| Corpus / scorer | Exact window before -> after | Within four before -> after |
| --- | ---: | ---: |
| single_gold_document / baseline | 4 -> 4 / 15 | 10 -> 10 / 15 |
| single_gold_document / position | 5 -> 6 / 15 | 10 -> 11 / 15 |
| single_gold_document / title_definition | 4 -> 6 / 15 | 10 -> 12 / 15 |
| four_documents / baseline | 0 -> 0 / 15 | 0 -> 0 / 15 |
| four_documents / position | 0 -> 0 / 15 | 0 -> 0 / 15 |
| four_documents / title_definition | 0 -> 0 / 15 | 0 -> 0 / 15 |

The current scorer still answers both identity phrasings with a firewall command. Moving
the notes improves the human introduction but does not by itself fix retrieval.
After the move, the title-definition experiment retrieves both introductions (4 -> 6
exact windows, 10 -> 12 within four); this is an in-memory experiment, not a production
change. It has no exact-window regressions in this fixed set. The positional bonus
also gets 6/15 but 11/15 within four. No independent held-out set has validated either.

The four-repo-document case still selects developer history. It is a stress test for
indexing this repository, not the reported live README-only corpus or a typical user
store. These are strict anchor/window counts, not semantic correctness ratings.
Do not generalize their percentages to users. A realistic user mix and independently
labelled held-out definitions remain next experiments to agree with Claude.

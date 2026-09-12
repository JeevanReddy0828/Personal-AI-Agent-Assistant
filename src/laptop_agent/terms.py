"""The one word splitter the retrieval paths share.

Four modules ranked text with their own near-identical tokenizer — `knowledge` (TF-IDF
index), `context` (session BM25), `tools.obsidian` (vault search) and `tools.files`
(sentence ranking) — and a fix applied to one never reached the other three. The dotted
acronym below is exactly that: `knowledge` learned to keep "J.A.R.V.I.S" as a word, while
a vault search for the same name still matched nothing.

What is shared is the *splitting*. What is not, deliberately:

- Each caller keeps its own stopword list and minimum length. They are tuned differently
  on purpose (session context drops 120 common words so BM25 is not swamped; vault search
  wants 3+ characters; the file summarizer's list is prose-shaped), and unifying them
  would be a ranking change nobody asked for.
- `tools.files` keeps its own `[A-Za-z']+` pattern and only borrows `collapse_acronyms`.
  Measured over the repo's own prose, moving it onto `words()` changed 53 of 134
  paragraphs: it gained 130 kinds of version number ("2026", "120b", "404") and lost 52
  contractions, because "can't" and "user's" split at the apostrophe. That is a real
  change to sentence-frequency scoring with no defect behind it.
- `copilot.extract_keywords` is not part of this family at all. It is an ATS keyword
  extractor that must *keep* the punctuation this module removes — "node.js", "c++",
  "c#" are the tokens it exists to find.
"""

from __future__ import annotations

import re
from collections.abc import Container

# A dotted acronym is one word. Without this the app could not find itself: the README is
# titled "J.A.R.V.I.S", which split into six single letters and then into nothing at all
# (every token is dropped below two characters), so "what is JARVIS" had zero overlap with
# the document that answers it and the query landed on an unrelated research scrape.
_DOTTED_ACRONYM = re.compile(r"\b(?:[A-Za-z]\.){2,}[A-Za-z]?")
_WORD = re.compile(r"[a-z0-9]+")


def collapse_acronyms(text: str) -> str:
    """"J.A.R.V.I.S" -> "JARVIS", so a dotted name survives tokenizing.

    This also turns "e.g." into "eg", which is meaningless but harmless: it is dropped by
    every caller that asks for 3+ characters, and as a term appearing in a handful of
    paragraphs it carries no ranking weight. Measured across the repo's prose, "J.A.R.V.I.S"
    and "e.g." are the only two shapes that match.
    """
    return _DOTTED_ACRONYM.sub(lambda match: match.group(0).replace(".", ""), text or "")


def words(text: str) -> list[str]:
    """Lowercased alphanumeric runs, with dotted acronyms kept whole."""
    return _WORD.findall(collapse_acronyms(text).lower())


def content_terms(text: str, stopwords: Container[str], min_length: int = 2) -> list[str]:
    """`words()` minus the caller's stopwords and anything shorter than `min_length`."""
    return [token for token in words(text) if len(token) >= min_length and token not in stopwords]

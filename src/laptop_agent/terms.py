"""The one word splitter the retrieval paths share, and the one sentence splitter.

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


# Sentences, for the file summarizer and the knowledge base. Each split its documents at full
# stops after flattening every line break, and Markdown has none in its badges, tables and
# code: "summarize the readme" came back as 15 KB of one paragraph, and "how do i start the
# app" was answered from the knowledge base with 16,000 characters of image links and a table
# of contents. A fix to one splitter would not have reached the other.
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Real HTML only: a placeholder such as `127.0.0.1:<port>` is text and must survive.
_TAG = re.compile(r"</?(?:a|b|i|u|s|em|strong|br|hr|p|div|span|img|sub|sup|small|kbd|code|pre|table|thead|"
                  r"tbody|tr|td|th|details|summary|picture|source|video|ul|ol|li|h[1-6]|center|del|ins)\b[^<>]*>",
                  re.IGNORECASE)
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_QUOTE = re.compile(r"^\s*>\s?")
_HEADING_OR_RULE = re.compile(r"#{1,6}\s|(?:-{3,}|={3,}|\*{3,}|_{3,})$")
_TABLE_RULE = re.compile(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?$")
_LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
SENTENCE_LIMIT = 400


def sentences(text: str, structure: bool = False, limit: int = SENTENCE_LIMIT) -> list[str]:
    """Plain text or Markdown as the sentences a reader sees.

    Images, link targets and real HTML tags go; a heading, a rule or a blank line ends a
    paragraph; wrapped lines join up; a list item is a sentence of its own; a run-on is cut
    into pieces of at most `limit` characters, none of it lost. A table row and a line of
    code are sentences only with `structure`: a summary wants prose, but the answer to a
    question is often a command or a row of a table.
    """
    units: list[str] = []
    paragraph: list[str] = []
    fence, keep_code = "", False

    def close() -> None:
        if paragraph:
            units.append(" ".join(paragraph))
            paragraph.clear()

    for raw in (text or "").splitlines():
        opened = _FENCE.match(raw)
        if fence:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                fence = ""
            elif keep_code and raw.strip():
                units.append(raw.strip())
            continue
        if opened:
            close()
            fence = opened.group(1)
            # A diagram's source is not an answer: "what does the approval gate do" was
            # answered with `TOOLS --> GATE{{"Approval gate"}}`.
            keep_code = structure and not raw.strip()[len(fence):].strip().lower().startswith("mermaid")
            continue
        line = _QUOTE.sub("", _TAG.sub(" ", _LINK.sub(r"\1", _IMAGE.sub(" ", raw)))).strip()
        # A row of links and nothing else is navigation: a table of contents names every
        # section, so it matched almost any question put to the knowledge base.
        navigation = len(_LINK.findall(raw)) >= 3 and not re.search(r"[A-Za-z]{2,}", _LINK.sub("", raw))
        if not line or navigation:
            close()
        elif _HEADING_OR_RULE.match(line):
            close()
            if structure and line.startswith("#"):
                units.append(line.lstrip("#").strip())
        elif line.startswith("|"):
            close()
            if structure and not _TABLE_RULE.match(line):
                units.append(": ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip()))
        elif _LIST_ITEM.match(line):
            close()
            units.append(_LIST_ITEM.sub("", line))
        else:
            paragraph.append(line)
    close()
    found: list[str] = []
    for unit in units:
        for part in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", unit).strip()):
            while len(part) > limit:
                piece = part[:limit].rsplit(" ", 1)[0] or part[:limit]
                found.append(piece + "…")
                part = part[len(piece):].strip()
            if len(part) > 1:
                found.append(part)
    return found

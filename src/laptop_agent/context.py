"""Session context for the model: contextual chunking of the chat transcript.

Every model-facing path (router, chat tiers, autonomous agent, advisor) used to see at
most the last eight turns clipped to 300 characters, so a long answer such as a schema
design vanished and a follow-up like "build an ERD for this" had nothing to refer to.

``build_context`` treats the whole session as the source instead. It splits each turn
into Markdown-aware chunks (headings, paragraphs, fenced code kept whole), ranks them
against the new message (term overlap with IDF, recency, and a boost for the most recent
assistant turn when the message refers back with "this"/"that"/"it"), and assembles a
budgeted block: a one-line outline of every turn, the most recent turns verbatim, the
best earlier chunks, and an explicit note of what "this" most likely refers to.
Everything is local and dependency-free.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

_ROLE_LABEL = {"user": "User", "assistant": "J.A.R.V.I.S"}

# Character budgets per consumer (roughly chars/4 tokens). Routing stays lean so it
# answers fast and emits clean JSON; a chat answer can afford the full recent exchange.
ROUTE_BUDGET = 2400
CHAT_BUDGET = 9000
AGENT_BUDGET = 6000
ADVISOR_BUDGET = 5000

_STOPWORDS = frozenset(
    """
    a an the and or but if then else for of to in on at by with from as is are was were be been
    being this that these those it its they them their there here what which who whom whose when
    where why how i me my we our you your he she his her do does did done have has had having can
    could should would will shall may might must not no yes so than too very just also into out
    up down over under again further about above below between through during before after off
    own same other some such only both each few more most all any now please make build create
    give show write tell let get put use using want need like
    """.split()
)

# A follow-up that points back at the conversation rather than naming its subject.
_REFERS_BACK = re.compile(
    r"\b(?:this|that|these|those|it|its|them|above|previous(?:ly)?|earlier|same|again|instead|"
    r"last (?:answer|reply|response|message|one|thing)|"
    r"the (?:schema|design|plan|code|answer|list|table|diagram|erd|flow ?chart|chart|result|output|"
    r"summary|doc(?:ument)?|report|analysis|options?|recommendation|proposal|draft|essay|email|"
    r"message|function|script|query|snippet|approach|idea|version|one))\b",
    re.IGNORECASE,
)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(?:```|~~~)")
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Chunk:
    turn: int
    role: str
    heading: str
    text: str
    position: int


@dataclass
class SessionContext:
    text: str
    turns: int = 0
    chunks_used: list[Chunk] = field(default_factory=list)
    referent: str = ""
    refers_back: bool = False


def normalize_history(history) -> list[tuple[str, str]]:
    """(role, text) pairs with empty turns dropped; 'bot' and friends count as assistant."""
    turns: list[tuple[str, str]] = []
    for turn in history or []:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role", "user")).lower()
        role = "assistant" if role in {"assistant", "bot", "jarvis", "j.a.r.v.i.s"} else "user"
        text = str(turn.get("text") or turn.get("content") or "").strip()
        if text:
            turns.append((role, text))
    return turns


def terms(text: str) -> list[str]:
    return [token for token in _WORD.findall(text.lower()) if len(token) > 1 and token not in _STOPWORDS]


def refers_back(query: str) -> bool:
    """True when the message leans on the conversation for its subject."""
    query = (query or "").strip()
    if not query:
        return False
    return bool(_REFERS_BACK.search(query)) or len(query.split()) <= 3


def _split_point(text: str, limit: int) -> int:
    window = text[:limit]
    for separator in ("\n\n", "\n", ". ", " "):
        index = window.rfind(separator)
        if index >= limit // 2:
            return index + len(separator)
    return limit


def chunk_text(text: str, target: int = 700, hard: int = 1400) -> list[tuple[str, str]]:
    """Split Markdown into (heading, chunk) pieces.

    Fenced code stays whole, a heading starts a new chunk and labels everything under it,
    paragraphs under one heading merge up to ``target`` characters, and anything longer
    than ``hard`` is cut at the nearest paragraph, line or sentence boundary."""
    blocks: list[tuple[str, str]] = []
    heading = ""
    paragraph: list[str] = []
    fence: list[str] = []
    in_fence = False

    def flush() -> None:
        if paragraph:
            blocks.append((heading, "\n".join(paragraph).strip()))
            paragraph.clear()

    for line in text.splitlines():
        if in_fence:
            fence.append(line)
            if _FENCE.match(line):
                in_fence = False
                blocks.append((heading, "\n".join(fence)))
                fence = []
            continue
        if _FENCE.match(line):
            flush()
            in_fence = True
            fence = [line]
            continue
        match = _HEADING.match(line)
        if match:
            flush()
            heading = match.group(2).strip()
            blocks.append((heading, line.strip()))
            continue
        if not line.strip():
            flush()
            continue
        paragraph.append(line)
    flush()
    if fence:
        blocks.append((heading, "\n".join(fence)))

    merged: list[tuple[str, str]] = []
    for block_heading, block in blocks:
        if merged and merged[-1][0] == block_heading and len(merged[-1][1]) + len(block) + 1 <= target:
            merged[-1] = (block_heading, merged[-1][1] + "\n" + block)
        else:
            merged.append((block_heading, block))

    pieces: list[tuple[str, str]] = []
    for block_heading, block in merged:
        while len(block) > hard:
            cut = _split_point(block, hard)
            pieces.append((block_heading, block[:cut].rstrip()))
            block = block[cut:].lstrip()
        if block:
            pieces.append((block_heading, block))
    return pieces


def chunk_history(turns: list[tuple[str, str]]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for index, (role, text) in enumerate(turns):
        for position, (heading, piece) in enumerate(chunk_text(text)):
            chunks.append(Chunk(turn=index, role=role, heading=heading, text=piece, position=position))
    return chunks


def rank_chunks(
    chunks: list[Chunk], query: str, turn_count: int, referent_turn: int | None = None
) -> list[tuple[float, Chunk]]:
    """Score chunks for a query: TF-IDF overlap, a heading hit, recency, and a flat boost
    for the turn the query refers back to. Sorted best first."""
    query_terms = set(terms(query))
    counts_per_chunk = [Counter(terms(chunk.text + " " + chunk.heading)) for chunk in chunks]
    document_frequency: Counter[str] = Counter()
    for counts in counts_per_chunk:
        for term in query_terms:
            if counts.get(term):
                document_frequency[term] += 1
    total = max(1, len(chunks))
    ranked: list[tuple[float, Chunk]] = []
    for chunk, counts in zip(chunks, counts_per_chunk):
        score = 0.0
        for term in query_terms:
            frequency = counts.get(term, 0)
            if frequency:
                score += (1 + math.log(frequency)) * math.log(1 + total / (1 + document_frequency[term]))
        if query_terms:
            score /= math.sqrt(len(query_terms))
        if score and query_terms & set(terms(chunk.heading)):
            score += 0.5
        score *= 0.6 + 0.4 * (chunk.turn + 1) / max(1, turn_count)
        if referent_turn is not None and chunk.turn == referent_turn:
            score += 1.0
        ranked.append((score, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1].turn, item[1].position))
    return ranked


def _summary_line(text: str, limit: int = 140) -> str:
    first = ""
    sections: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _HEADING.match(line)
        if match:
            title = match.group(2).strip()
            if title not in sections:
                sections.append(title)
            if not first:
                first = title
            continue
        if not first:
            first = stripped
    first = " ".join(first.split())
    if len(first) > limit:
        first = first[: limit - 1].rstrip() + "…"
    if len(text) > 600:
        detail = f"{len(text):,} chars"
        if sections:
            detail += "; sections: " + ", ".join(sections[:7])
        first += f" ({detail})"
    return first


def build_context(
    history,
    query: str = "",
    *,
    budget: int = 9000,
    recent_turns: int = 4,
    title: str = "Recent conversation",
) -> SessionContext:
    """Assemble the model-facing context block for this turn within ``budget`` characters."""
    turns = normalize_history(history)
    if not turns:
        return SessionContext(text="")
    turn_count = len(turns)
    chunks = chunk_history(turns)
    by_turn: dict[int, list[Chunk]] = {}
    for chunk in chunks:
        by_turn.setdefault(chunk.turn, []).append(chunk)
    anaphoric = refers_back(query)
    last_assistant = max((i for i, (role, _) in enumerate(turns) if role == "assistant"), default=None)
    referent_turn = last_assistant if anaphoric else None

    lines: list[str] = []
    used: set[tuple[int, int]] = set()
    size = 0

    def add(line: str) -> None:
        nonlocal size
        lines.append(line)
        size += len(line) + 1

    add(f"{title} (this session, oldest first):")

    # 1. A one-line outline of everything before the recent window.
    recent_start = max(0, turn_count - recent_turns)
    if recent_start > 0:
        outline = [
            f"{index + 1}. {_ROLE_LABEL[role]}: {_summary_line(text)}"
            for index, (role, text) in enumerate(turns[:recent_start])
        ]
        outline_budget = int(budget * 0.15)
        while outline and sum(len(line) + 1 for line in outline) > outline_budget:
            outline.pop(0)
        omitted = recent_start - len(outline)
        if omitted:
            add(f"({omitted} earlier turn(s) omitted)")
        for line in outline:
            add(line)
        add("Most recent turns:")

    # 2. The recent window verbatim, the referent turn getting the largest share.
    recent = list(range(recent_start, turn_count))
    star = referent_turn if referent_turn in recent else (last_assistant if last_assistant in recent else None)
    recent_budget = int(budget * (0.6 if recent_start > 0 else 0.7))
    need = {index: len(turns[index][1]) + 14 for index in recent}
    share: dict[int, int] = {}
    if sum(need.values()) <= recent_budget:
        share = dict(need)
    else:
        others = [index for index in recent if index != star]
        pool = recent_budget
        if star is not None:
            share[star] = min(need[star], int(pool * (0.6 if others else 1.0)))
            pool -= share[star]
        for index in others:
            share[index] = min(need[index], pool // max(1, len(others)))
        leftover = pool - sum(share[index] for index in others)
        if star is not None and leftover > 0:
            share[star] = min(need[star], share[star] + leftover)

    def render(index: int, cap: int) -> str:
        role, text = turns[index]
        label = _ROLE_LABEL[role]
        if len(text) <= cap:
            used.update((index, chunk.position) for chunk in by_turn.get(index, []))
            return f"{label}: {text}"
        kept: list[Chunk] = []
        total = 0
        for chunk in by_turn.get(index, []):
            if total + len(chunk.text) > cap:
                break
            kept.append(chunk)
            total += len(chunk.text) + 2
            used.add((index, chunk.position))
        if not kept:
            cut = _split_point(text, max(200, cap))
            return f"{label}: {text[:cut].rstrip()} …"
        rest = [chunk for chunk in by_turn[index] if (index, chunk.position) not in used]
        sections: list[str] = []
        for chunk in rest:
            if chunk.heading and chunk.heading not in sections:
                sections.append(chunk.heading)
        note = f"… [{sum(len(chunk.text) for chunk in rest):,} more chars omitted here"
        if sections:
            note += "; remaining sections: " + ", ".join(sections[:6])
        return f"{label}: " + "\n\n".join(chunk.text for chunk in kept) + "\n" + note + "]"

    for index in recent:
        add(render(index, max(0, share.get(index, 0) - 14)))

    # 3. The best earlier chunks not already shown, within what is left of the budget.
    left = budget - size
    if left > 300:
        candidates = [chunk for chunk in chunks if (chunk.turn, chunk.position) not in used]
        picked: list[Chunk] = []
        for score, chunk in rank_chunks(candidates, query, turn_count, referent_turn):
            if score <= 0 or len(picked) >= 8:
                break
            if len(chunk.text) + 40 > left:
                continue
            picked.append(chunk)
            left -= len(chunk.text) + 40
        if picked:
            add("Relevant earlier context for this request:")
            for chunk in sorted(picked, key=lambda item: (item.turn, item.position)):
                where = f"turn {chunk.turn + 1}, {_ROLE_LABEL[chunk.role]}"
                if chunk.heading:
                    where += f', "{chunk.heading}"'
                add(f"— [{where}] {chunk.text}")
                used.add((chunk.turn, chunk.position))

    # 4. Say what "this" points at, so a follow-up is answered from the text, not by hunting.
    referent = ""
    if referent_turn is not None:
        referent = f"J.A.R.V.I.S's reply in turn {referent_turn + 1} ({_summary_line(turns[referent_turn][1], 90)})"
        add(
            f'Note: the user\'s "this"/"that"/"it" most likely refers to {referent}. '
            "Answer follow-ups about it from that text — do not search files or the web for it."
        )

    chunks_used = [chunk for chunk in chunks if (chunk.turn, chunk.position) in used]
    return SessionContext(
        text="\n".join(lines) + "\n",
        turns=turn_count,
        chunks_used=chunks_used,
        referent=referent,
        refers_back=anaphoric,
    )


def context_block(history, query: str = "", budget: int = 9000) -> str:
    """The context text alone, or '' when there is no usable history."""
    return build_context(history, query, budget=budget).text

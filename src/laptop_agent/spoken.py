"""Talking, not reading out a document: how a spoken turn is answered, and how much is said aloud.

Jeevan, 2026-10-15: talking to it should feel like talking to J.A.R.V.I.S, not a chatbot - a
real back-and-forth, short answers, not long replies read out word for word, nothing said twice.
A voice turn was answered with the chat prompt ("Answer directly and helpfully in Markdown") and
every sentence of a long reply or tool result was then spoken. A spoken turn now gets its own
style, and what is said aloud stops after about SPOKEN_WORDS, with the rest left on the screen.
"""
from __future__ import annotations

import random
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime

SPOKEN: ContextVar[bool] = ContextVar("spoken_turn", default=False)
SPOKEN_WORDS = 45
MORE_ON_SCREEN = "The rest is on screen."

VOICE_STYLE = (
    "You are talking with the user out loud, face to face, so speak the way a person does: one to "
    "three short sentences, under about 40 words unless they ask for more, plain spoken words, no "
    "lists, headings, tables, code or Markdown. Do not mention the app, its port or your setup "
    "unless they ask about it. Never "
    "restate their question and never repeat what you said earlier in the conversation. When a topic "
    "needs detail, give the gist and offer to go deeper. Be warm and quick, with a light touch of dry "
    "wit; a short question back is fine when it moves the conversation along."
)


@contextmanager
def spoken_turn(on: bool):
    """Mark the turn running inside as one that will be spoken."""
    token = SPOKEN.set(bool(on))
    try:
        yield
    finally:
        SPOKEN.reset(token)


def reply_style() -> str:
    """The closing instruction for a chat reply: spoken or written."""
    return VOICE_STYLE if SPOKEN.get() else "Answer directly and helpfully in Markdown."


class SpokenBudget:
    """What of a reply is said aloud: sentences until about SPOKEN_WORDS, then one line pointing
    at the screen, said once, at the end."""

    def __init__(self, words: int = SPOKEN_WORDS) -> None:
        self._left = words
        self.held_back = False

    def take(self, sentence: str) -> str | None:
        sentence = (sentence or "").strip()
        if not sentence:
            return None
        if self._left <= 0:
            self.held_back = True
            return None
        self._left -= len(sentence.split())
        return sentence

    def take_last(self, sentences: list[str]) -> list[str]:
        """The closing sentences that fit what is left, in order; the last one always."""
        kept: list[str] = []
        for sentence in reversed([s.strip() for s in sentences if s and s.strip()]):
            words = len(sentence.split())
            if kept and words > self._left:
                self.held_back = True
                break
            self._left -= words
            kept.insert(0, sentence)
        return kept

    def closing(self) -> str | None:
        return MORE_ON_SCREEN if self.held_back else None


def greeting(name: str | None, now: datetime, pick=random.choice) -> str:
    """What J.A.R.V.I.S says when the app opens: by the time of day, by name when it knows one."""
    who = f", {name}" if name else ""
    hour = now.hour
    if 5 <= hour < 12:
        part = "Good morning"
    elif 12 <= hour < 17:
        part = "Good afternoon"
    elif 17 <= hour < 23:
        part = "Good evening"
    else:
        return pick([f"Still up{who}? What can I do for you?", f"Late one{who}. What are we working on?"])
    return pick([
        f"{part}{who}. What can I do for you?",
        f"Hey{who}. What are you up to?",
        f"Welcome back{who}. What's on your mind?",
        f"{part}{who}. What are we working on today?",
    ])

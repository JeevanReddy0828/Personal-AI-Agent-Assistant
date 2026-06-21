from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# The LLM brain: prompt -> reply (same shape used by reasoning.py). Injected so the
# advisor is unit-tested offline. Returns "" when no model is available.
Decide = Callable[[str], str]
# Optional grounding: a query -> (context_text, sources) lookup (the research tool).
Research = Callable[[str], "tuple[str, list]"]


@dataclass
class AdviceResult:
    problem: str
    analysis: str  # Markdown
    ok: bool
    used_research: bool = False
    sources: list = field(default_factory=list)


_SYSTEM = (
    "You are J.A.R.V.I.S, acting as a sharp, candid advisor and problem-solver for Jeevan. "
    "Think the problem through rigorously and commit to a decision — don't just list options. "
    "Answer in GitHub-flavored Markdown with EXACTLY these sections, in order:\n"
    "## Problem\n"
    "Restate the real problem in one or two sentences. Note any assumption you're making or key info that's missing.\n"
    "## Key considerations\n"
    "The factors that actually matter: constraints, trade-offs, and what a good outcome looks like.\n"
    "## Options\n"
    "2–4 distinct, concrete approaches. For each: a bolded short name, then **Pros**, **Cons**, **Risks**, and a rough **Effort/cost**.\n"
    "## Recommendation\n"
    "Pick one approach (or a sequenced combination) and justify it briefly and honestly.\n"
    "## Action plan\n"
    "A numbered list of concrete, do-it-now next steps to execute the recommendation.\n"
    "## Watch-outs\n"
    "What could go wrong and the early signals to watch for.\n\n"
    "Be specific and practical over generic. If the problem is underspecified, state the assumption you're making and proceed anyway."
)


class ProblemSolver:
    """Turns a problem or decision into a structured, researched recommendation:
    framing, options with trade-offs, a committed recommendation, and an action plan.

    The reasoning model is injected as ``decide`` (prompt -> reply) and optional web
    grounding as ``research`` (query -> (context, sources)), so the success path is
    unit-tested offline and degrades gracefully when neither is available."""

    def __init__(self, decide: Decide, research: Research | None = None, max_context_chars: int = 2400) -> None:
        self._decide = decide
        self._research = research
        self._max_context_chars = max_context_chars

    def solve(self, problem: str, do_research: bool = True) -> AdviceResult:
        problem = (problem or "").strip()
        if not problem:
            return AdviceResult(problem="", analysis="Tell me the problem or decision you'd like help with.", ok=False)

        context, sources, used_research = "", [], False
        if do_research and self._research is not None:
            try:
                context, sources = self._research(problem)
            except Exception:  # grounding is best-effort; never block the advice on it
                context, sources = "", []
            context = (context or "").strip()[: self._max_context_chars]
            used_research = bool(context)

        prompt = self._build_prompt(problem, context)
        try:
            reply = (self._decide(prompt) or "").strip()
        except Exception as exc:  # the brain is injected; surface, don't crash
            return AdviceResult(problem=problem, analysis=f"My reasoning model errored: {exc}", ok=False,
                                used_research=used_research, sources=sources)
        if not reply:
            return AdviceResult(
                problem=problem,
                analysis="I need a language model configured to think this through (set OPENAI_* in your .env).",
                ok=False, used_research=used_research, sources=sources,
            )
        return AdviceResult(problem=problem, analysis=reply, ok=True, used_research=used_research, sources=sources)

    def _build_prompt(self, problem: str, context: str) -> str:
        parts = [_SYSTEM]
        if context:
            parts += ["", "Recent web context you may use (cite it only where relevant):", context]
        parts += ["", f"PROBLEM: {problem}", "", "Your structured analysis:"]
        return "\n".join(parts)

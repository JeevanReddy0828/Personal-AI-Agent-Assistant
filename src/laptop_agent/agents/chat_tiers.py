"""Model tiers, how hard a question is, and answers grounded in a live search.

One part of `AgentOrchestrator` (agents/orchestrator.py), split out by area; it reads the
rest of the orchestrator through `self`."""
from __future__ import annotations

from laptop_agent.cancellation import check_cancelled
import re
from datetime import datetime
from laptop_agent.context import accepts_context_query, accepts_keyword
from laptop_agent.planner import Planner
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.clock import asks_the_time
from laptop_agent.tracing import TurnTrace


class ChatTiers:

    # The page's estimateTier keeps the same two lists to colour the wait;
    # test_page_integrity fails when they differ.
    _ULTRA_CUES = (
        "in depth", "in-depth", "step by step", "step-by-step", "comprehensive", "thorough", "rigorous",
        "deep dive", "detailed analysis", "prove", "derive", "full implementation", "design a system",
        "architecture", "from scratch", "think hard", "deeply", "use the big model", "ultra",
        # Logic / puzzle / multi-step reasoning cues -> the reasoning tier, which
        # thinks before answering (the non-reasoning tiers ramble on these).
        "puzzle", "riddle", "brain teaser", "logic puzzle", "logic problem",
        "measure exactly", "show your reasoning", "show your work", "show the steps",
        "find the flaw", "fallacy", "how many ways", "solve for",
    )
    _SMART_CUES = (
        "explain", "why", "analy", "compare", "design", "reason", "debug", "refactor", "optimi",
        "trade-off", "tradeoff", "write code", "implement", "algorithm", "strategy", "pros and cons",
        "evaluate", "critique", "plan ",
    )

    @staticmethod
    def _complexity(text: str) -> int:
        """0 = simple (fast model), 1 = complex (smart), 2 = very complex (ultra)."""
        lowered = text.lower()
        words = len(text.split())
        if words > 70 or any(trigger in lowered for trigger in ChatTiers._ULTRA_CUES):
            return 2
        if words > 35 or any(trigger in lowered for trigger in ChatTiers._SMART_CUES):
            return 1
        return 0

    def _pick_chat_model(self, level: int):
        """Return (planner, label) for the given complexity, falling back down the tiers."""
        if level >= 2 and self.ultra_planner is not None:
            return self.ultra_planner, "ultra"
        if level >= 1 and self.smart_planner is not None:
            return self.smart_planner, "smart"
        if level >= 2 and self.smart_planner is not None:
            return self.smart_planner, "smart"
        return None, "fast"

    def _higher_chat_tiers(self, level: int) -> list[tuple[Planner, str]]:
        """The above-fast tiers to attempt for this complexity, strongest first.

        Lets a congested top tier degrade gracefully: try ultra, then smart, then
        the caller falls back to the fast tier. Empty for simple (level 0) chat."""
        ladder: list[tuple[Planner, str]] = []
        if level >= 2 and self.ultra_planner is not None:
            ladder.append((self.ultra_planner, "ultra"))
        if level >= 1 and self.smart_planner is not None:
            ladder.append((self.smart_planner, "smart"))
        return ladder

    def _recovery_chat_tiers(self, attempted: set[str]) -> list[tuple[Planner, str]]:
        """Usable primary tiers not already chosen by the complexity ladder.

        This handles a bad fast endpoint on simple chat: try smart once, then
        ultra, rather than repeatedly failing the same endpoint per message.
        """
        candidates = ((self.smart_planner, "smart"), (self.ultra_planner, "ultra"))
        return [
            (planner, label)
            for planner, label in candidates
            if planner is not None and label not in attempted and self.model_status.should_attempt(label)
        ]

    @staticmethod
    def _call_with_query(fn, command, profile, history, query, on_failure=None):
        """Call a provider's answer/stream_answer, passing the optional keywords only when
        the provider accepts them (test doubles and older providers do not).

        ``on_failure`` is how the tier says *why* it produced nothing. Without it every
        cause - a retired model, a rejected key, an unprovisioned id, an overloaded
        endpoint - arrives as the same empty string, and the ladder cannot tell a tier
        that needs a minute from one that needs a human."""
        extra: dict[str, object] = {}
        if query is not None and accepts_context_query(fn):
            extra["context_query"] = query
        if on_failure is not None and accepts_keyword(fn, "on_failure"):
            extra["on_failure"] = on_failure
        return fn(command, profile, None, history, **extra)

    def _record_tier(self, tier: str, reply: str, why: list[tuple[str, str]]) -> None:
        """Record how a tier behaved, keeping the reason it gave.

        A tier that answered is fine. A tier that did not is *busy* unless it said
        otherwise - an unexplained failure is the transient assumption, because guessing
        "broken" would stop trying a tier that was only having a bad minute.
        """
        if reply:
            self.model_status.record(tier, True)
            return
        kind, detail = why[-1] if why else ("", "")
        self.model_status.record(tier, False, reason=kind, detail=detail)

    @classmethod
    def _tier_reply(cls, provider, command, profile, history, on_token, query=None,
                    failures: list[tuple[str, str]] | None = None) -> str:
        """One tier's conversational reply: stream when a sink is given (and stream
        is supported), else a plain answer. Returns '' if the tier produced nothing
        (e.g. it was unreachable/congested), which signals the caller to fall back.
        ``query`` ranks the session context when ``command`` is a synthesized prompt."""
        check_cancelled()
        if provider is None:
            return ""
        # A local sink, so one provider shared by every request thread stays safe.
        sink = None if failures is None else (lambda kind, detail: failures.append((kind, detail)))
        streamer = getattr(provider, "stream_answer", None)
        if on_token is not None and streamer is not None:
            chunks: list[str] = []
            try:
                for token in cls._call_with_query(streamer, command, profile, history, query, sink):
                    check_cancelled()
                    chunks.append(token)
                    on_token(token)
            except (OSError, TimeoutError):
                check_cancelled()
                reset = getattr(on_token, "reset", None)
                if reset:
                    reset()
                return ""
            check_cancelled()
            return "".join(chunks).strip()
        answer_fn = getattr(provider, "answer", None)
        if answer_fn is not None:
            reply = (cls._call_with_query(answer_fn, command, profile, history, query, sink) or "").strip()
            check_cancelled()
            return reply
        return ""

    @staticmethod
    def _traced_tokens(on_token, trace: TurnTrace):
        """Wrap the stream callback to capture time-to-first-token, preserving .reset."""
        if on_token is None:
            return None

        def traced(text):
            trace.first_token()
            return on_token(text)

        reset = getattr(on_token, "reset", None)
        if reset is not None:
            traced.reset = reset
        return traced

    def _needs_fresh_info(self, text: str) -> bool:
        if self.context.websearch is None:
            return False
        # The time is on the clock, not on the web. Asked "what is the current date and
        # time in EST" this searched, scraped a stale page, and answered 1:00 PM while
        # the machine's own clock read 6:26 PM.
        if asks_the_time(text) or self._NOT_FRESH.search(text):
            return False
        lowered = " " + text.lower()
        if any(keyword in lowered for keyword in self._FRESH_KEYWORDS):
            return True
        return any(re.search(pattern, lowered) for pattern in self._FRESH_PATTERNS)

    def _grounded_news_answer(self, command: str, history_turns, on_token) -> ToolResult | None:
        """Search the web and synthesize a cited answer. Returns None (so the caller falls
        back to normal chat) when there is no LLM or the search yields nothing."""
        provider = (self.smart_planner or self.planner).provider if (self.smart_planner or self.planner) else None
        answer = getattr(provider, "answer", None)
        if answer is None:
            return None  # no LLM to synthesize — let normal chat handle it
        # The free DuckDuckGo endpoint is occasionally rate-limited; one retry turns most
        # transient empty results into a usable answer instead of a silent stale fallback.
        results = None
        for _attempt in range(2):
            search = self.context.websearch.search(command, limit=5)
            results = search.data.get("results") if search.ok else None
            if results:
                break
        if not results:
            return None

        today = datetime.now().astimezone().strftime("%A, %B %d, %Y")
        sources = []
        lines = []
        for i, item in enumerate(results, 1):
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            url = str(item.get("url", "")).strip()
            lines.append(f"[{i}] {title}\n{snippet}\n({url})")
            sources.append({"n": i, "title": title, "url": url})
        context = "\n\n".join(lines)
        prompt = (
            f"Today is {today}. Answer the user's question using the web search results below, "
            f"which are current. Prefer this live information over any prior knowledge, lead with the "
            f"most up-to-date facts, and cite sources inline like [1]. If the results don't clearly "
            f"answer it, say what is and isn't known.\n\n"
            f"QUESTION: {command}\n\nWEB SEARCH RESULTS:\n{context}"
        )
        profile = self.context.memory.get_profile()
        streamer = getattr(provider, "stream_answer", None)
        streamed_live = on_token is not None and streamer is not None
        text = ""
        # The prompt is synthesized; the session context is ranked on the user's words.
        if streamed_live:
            text = self._tier_reply(provider, prompt, profile, history_turns, on_token, query=command)
        if not text:
            text = (self._call_with_query(answer, prompt, profile, history_turns, command) or "").strip()
        if not text:
            return None

        footer = "\n\n**Sources**\n" + "\n".join(
            f"{s['n']}. [{s['title'] or s['url']}]({s['url']})" for s in sources if s["url"]
        )
        if streamed_live and on_token is not None:
            on_token(footer)  # stream the footer so the live view matches the final message
        return ToolResult.success(
            text + footer,
            grounded=True,
            sources=sources,
            query=search.data.get("query", command),
            planner={"source_text": command, "model": "web+llm", "explanation": "Answered from live web search."},
        )

    def _has_language_model(self) -> bool:
        return any(
            planner is not None and type(getattr(planner, "provider", None)).__name__ != "HeuristicPlannerProvider"
            for planner in (self.planner, self.smart_planner, self.ultra_planner, self.fallback_planner)
        )

    @staticmethod
    def _conversation_fallback(text: str) -> ToolResult:
        # Reached by a command no tool recognised, from a router or an agent step. It used
        # to claim no language model was connected, which was usually untrue.
        shown = " ".join((text or "").split())[:80]
        return ToolResult.failure(f"I don't know how to do that yet: “{shown}”. Say 'help' to see what I can do.",
                                  heard=text, unrecognised=True)

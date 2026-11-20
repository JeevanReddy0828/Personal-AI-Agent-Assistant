from __future__ import annotations

import errno
import re
import threading
from collections.abc import Callable
from pathlib import Path

from laptop_agent import supervisor as supervision
from laptop_agent.access import ensure_signed_in
from laptop_agent.advisor import ProblemSolver
from laptop_agent.agents.agent_mode import AgentMode
from laptop_agent.agents.briefing_jobs import BriefingJobs
from laptop_agent.agents.chat_tiers import ChatTiers
from laptop_agent.agents.common import (
    _DECLINED,
    _FILE_VERBS,
    _INVENTED,
    _NO_MODEL_REPLY,
    _UNUSABLE_PATH,
    _VERBATIM,
    MAX_COMMAND_CHARS,
    AgentContext,
    _clean_paths,
    _fold_apostrophes,
)
from laptop_agent.agents.control_room import AgentControlRoom
from laptop_agent.agents.dispatch_core import CoreDispatch
from laptop_agent.agents.dispatch_outside import OutsideDispatch
from laptop_agent.agents.everyday import Everyday
from laptop_agent.agents.knowledge_actions import KnowledgeActions
from laptop_agent.agents.presentation import Presentation
from laptop_agent.agents.reminders import Reminders
from laptop_agent.agents.routing import Routing
from laptop_agent.agents.tool_access import ToolAccess
from laptop_agent.autopilot import AutopilotPlanner
from laptop_agent.cancellation import check_cancelled
from laptop_agent.config import load_config
from laptop_agent.context import register_summarizer
from laptop_agent.copilot import JobCopilot
from laptop_agent.failures import FAILURES, record_failure
from laptop_agent.model_status import ModelStatus
from laptop_agent.planner import HeuristicPlannerProvider, Planner
from laptop_agent.planner.core import PlanDecision
from laptop_agent.planner.heuristic import SMALL_TALK
from laptop_agent.safety import ApprovalDenied
from laptop_agent.tools import clipboard as clipboard_tools
from laptop_agent.tools import fileops
from laptop_agent.tools import keys as keys_tools
from laptop_agent.tools import system as system_tools
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.calculator import CalculatorTool
from laptop_agent.tools.clock import ClockTool
from laptop_agent.tools.codefix import CodeFixTool
from laptop_agent.tools.document import DocumentTool
from laptop_agent.tools.file_processor import FileProcessor
from laptop_agent.tools.imagegen import ImageTool
from laptop_agent.tools.learn import LearnTool
from laptop_agent.tools.mcp import McpTool
from laptop_agent.tools.messaging import MessagingTool
from laptop_agent.tools.news import NewsTool
from laptop_agent.tools.plugins import PluginTool
from laptop_agent.tools.translate import TranslateTool
from laptop_agent.tools.travel import TravelTool
from laptop_agent.tools.watch import Watches
from laptop_agent.tools.weather import WeatherTool
from laptop_agent.tools.workspace import WorkspaceTool
from laptop_agent.tools.youtube import YouTubeTool
from laptop_agent.tracing import TraceStore, TurnTrace, begin_trace, current_trace, end_trace


class AgentOrchestrator(
    ChatTiers,
    Routing,
    CoreDispatch,
    OutsideDispatch,
    Everyday,
    Presentation,
    Reminders,
    BriefingJobs,
    AgentMode,
    ToolAccess,
    KnowledgeActions,
):
    # Dispatch order, as data. A group returns a ToolResult or None; the first
    # non-None wins, exactly as the original if/elif chain did.
    _DISPATCH = (
        CoreDispatch._dispatch_meta,
        CoreDispatch._dispatch_jobs,
        CoreDispatch._dispatch_automation,
        CoreDispatch._dispatch_files,
        CoreDispatch._dispatch_knowledge,
        CoreDispatch._dispatch_vault,
        CoreDispatch._dispatch_file_intelligence,
        CoreDispatch._dispatch_vision,
        CoreDispatch._dispatch_tasks,
        CoreDispatch._dispatch_research,
        CoreDispatch._dispatch_utilities,
        OutsideDispatch._dispatch_generate,
        OutsideDispatch._dispatch_travel,
        OutsideDispatch._dispatch_web,
        OutsideDispatch._dispatch_desktop,
        OutsideDispatch._dispatch_email,
        OutsideDispatch._dispatch_batch,
        Everyday._dispatch_personal,
    )

    def __init__(
        self,
        context: AgentContext,
        planner: Planner | None = None,
        smart_planner: Planner | None = None,
        vision_planner: Planner | None = None,
        ultra_planner: Planner | None = None,
        fallback_planner: Planner | None = None,
        video_planner: Planner | None = None,
        data_dir: Path | None = None,
        recording_enabled: bool = False,
    ) -> None:
        self.context = context
        self.recording_enabled = recording_enabled
        self.planner = planner
        # Optional higher-capability model for moderately complex questions.
        self.smart_planner = smart_planner
        # Optional top-tier model for the hardest questions (slow, large).
        self.ultra_planner = ultra_planner
        # Optional vision model used to look at images and the screen.
        self.vision_planner = vision_planner
        # Optional model that takes a clip, for `describe video`.
        self.video_planner = video_planner
        # Optional cross-provider fallback (e.g. OpenRouter) tried when every
        # primary tier is congested/unreachable, so chat keeps working.
        self.fallback_planner = fallback_planner
        self.control_room = AgentControlRoom.standard(obsidian_available=self.context.obsidian.available())
        # Tracks per-tier model reachability so chat can fall back fast<-smart<-ultra
        # when a tier is congested, and health can show "the advanced model is busy".
        # Where everything this orchestrator persists lives: traces, tier health,
        # generated images and documents.
        #
        # A parameter rather than `load_config()` at each use, because that reads the
        # PROCESS-WIDE config. Under the test runner every orchestrator then shared one
        # directory: a tier one test recorded as broken was still broken for the next, and
        # generated files landed in whatever directory the app itself uses. State that
        # ignores its caller's own config is shared state. One handle here means the next
        # store to be added needs no parameter of its own.
        self.data_dir = data_dir or load_config().data_dir
        # Persisted so a retired model id or a rejected key is still known after a
        # restart, rather than being rediscovered by failing a real chat turn. Only the
        # broken tiers are written; "busy" is ephemeral by nature.
        self.model_status = ModelStatus(self.data_dir / "model_status.json")
        # Per-turn latency traces (timings only, never prompts or replies), so a slow
        # turn can be explained instead of guessed at.
        self.traces = TraceStore(self.data_dir / "traces.json")
        # Runs and steps today across every agent run, and the switch that turns agents off; one run at a time.
        self.agent_ledger = supervision.AgentLedger(self.data_dir / "agent_ledger.json")
        self._agent_running = threading.Lock()
        # The failure log is a process-wide singleton (every tool, provider and request
        # handler writes to it), so it cannot take its path in a constructor like the two
        # above — it is pointed at this orchestrator's own directory instead. It used to
        # keep nothing: the traces here recorded four `image` turns failing after ~61s
        # each, and every reason had died with the process that caught it.
        FAILURES.attach(self.data_dir / "failures.json")
        # The Setup rows `diagnose` reads. The web server sets this, since only it knows
        # sign-in, phone access and its cached engines; without it they are looked up here.
        self.setup_source: Callable[[], list[dict[str, object]]] | None = None
        self.autopilot_planner = AutopilotPlanner()
        # Fast deterministic router tried before any LLM, so common requests
        # route instantly and reliably with zero network latency.
        self.router = Planner(HeuristicPlannerProvider())
        self._file_processor_cache: FileProcessor | None = None
        self._weather_tool_cache: WeatherTool | None = None
        self._translate_tool_cache: TranslateTool | None = None
        self._system_tool_cache: system_tools.SystemTool | None = None
        self._image_tool_cache: ImageTool | None = None
        self._clipboard_tool_cache: clipboard_tools.ClipboardTool | None = None
        self._news_tool_cache: NewsTool | None = None
        self._watches_cache: Watches | None = None
        self._document_tool_cache: DocumentTool | None = None
        self._workspace_tool_cache: WorkspaceTool | None = None
        self._calculator_cache: CalculatorTool | None = None
        self._learn_cache: LearnTool | None = None
        self._clock_cache: ClockTool | None = None
        self._keys_cache: keys_tools.KeysTool | None = None
        self._command_verbs_cache: frozenset[str] | None = None
        self._plugins_cache: PluginTool | None = None
        self._plugins_lock = threading.Lock()
        self._youtube_tool_cache: YouTubeTool | None = None
        self._messaging_cache: MessagingTool | None = None
        self._travel_tool_cache: TravelTool | None = None
        self._file_ops_cache: fileops.FileOps | None = None
        self._problem_solver_cache: ProblemSolver | None = None
        self._mcp_cache: McpTool | None = None
        self._copilot_cache: JobCopilot | None = None
        self._resume_copilot_cache: JobCopilot | None = None
        self._repo_cache: dict[str, list[dict]] | None = None
        self._code_fix_cache: CodeFixTool | None = None
        # The rolling summary of older turns is written by the fast tier in the background
        # (see context.py); with no model configured it simply never appears.
        register_summarizer(
            self._build_agent_brain((self.planner, self.smart_planner, self.fallback_planner), answer_max_tokens=400)
        )

    async def handle(
        self,
        text: str,
        _allow_planner: bool = True,
        history: list[dict[str, str]] | None = None,
        on_token=None,
    ) -> ToolResult:
        """Route one turn, timing it. The inner leg of a planned command runs under the
        same trace, so a tool's own time is not counted as a second turn."""
        if current_trace() is not None:
            return await self._handle(text, _allow_planner, history, on_token)
        if not _allow_planner:
            # A top-level command with no routing: an agent step, an autopilot step, a
            # scheduled job. Untraced, but held to the same promise as a user's turn.
            try:
                return await self._handle(text, _allow_planner, history, on_token)
            except ApprovalDenied:
                raise
            except Exception as exc:
                return self._unexpected_failure(exc, text.strip().split(" ", 1)[0].lower())
        trace = TurnTrace()
        token = begin_trace(trace)
        try:
            result = await self._handle(text, _allow_planner, history, self._traced_tokens(on_token, trace))
            trace.finish(result.ok)
            return result
        except ApprovalDenied:
            trace.finish(False)
            raise
        except Exception as exc:
            trace.finish(False)
            return self._unexpected_failure(exc, trace.verb or "")
        finally:
            end_trace(token)
            if not trace.verb and trace.route_source in ("", "direct"):
                # A direct command prefix never reached the router. Record the tool name
                # only when the first word is a known command, so free-text never lands
                # in the trace file.
                trace.route_source = "direct"
                verb = (text or "").strip().split(" ", 1)[0].lower()
                if verb in self._command_verbs():
                    trace.kind, trace.verb = "command", verb
            try:
                self.traces.add(trace)
            except OSError:
                pass  # a trace is diagnostics; never fail a turn over one

    def _unexpected_failure(self, exc: Exception, verb: str) -> ToolResult:
        """The last line of defence: whatever a tool raised, the user gets an answer.

        Fuzzing every command prefix with hostile arguments found 21 ways to raise out of
        handle() - a NUL byte or a 3000-character name reaching pathlib, a sum too large to
        print - and each one ended the CLI session outright and showed the web page a raw
        "Error: ...". A tool bug still has to be fixed where it lives; this only guarantees
        an answer, and that the reason is written down instead of lost. Only the command
        word is recorded, never the user's text.
        """
        record_failure("orchestrator.handle", exc, verb=verb if verb in self._command_verbs() else "")
        # The commonest cause by far: a path the operating system refuses outright.
        unusable = (isinstance(exc, ValueError) and "null" in str(exc)) or (
            isinstance(exc, OSError) and (exc.errno == errno.ENAMETOOLONG or getattr(exc, "winerror", None) == 206)
        )
        if unusable:
            return ToolResult.failure(_UNUSABLE_PATH, error=type(exc).__name__)
        detail = " ".join(str(exc).split())[:200] or type(exc).__name__
        return ToolResult.failure(
            f"Sorry — that failed with an unexpected error ({type(exc).__name__}: {detail}). "
            "I've logged it; ask me for `failures` to see the details.",
            error=type(exc).__name__,
        )

    async def _handle(
        self,
        text: str,
        _allow_planner: bool = True,
        history: list[dict[str, str]] | None = None,
        on_token=None,
        _whole: bool = True,
    ) -> ToolResult:
        check_cancelled()
        ensure_signed_in()
        # An `mcp` call's arguments are JSON data, not a sentence: folded and path-cleaned, a value
        # such as "C:/r.csv" lost its quotes and "it’s" changed (review of #293).
        command = (
            text.strip() if text.strip().lower().startswith("mcp ") else _clean_paths(_fold_apostrophes(text.strip()))
        )
        lowered = command.lower()
        history_turns = history or []
        if not command:
            return ToolResult.success("Say something and I will route it.")
        # A 300,000-character message was accepted and spent 41.5s in the advisor before
        # answering. Nothing a person types is this long; a paste this big belongs in a
        # file, where `ask file` and `summarize file` handle it properly and cheaply.
        if len(command) > MAX_COMMAND_CHARS:
            return ToolResult.failure(
                f"That message is {len(command):,} characters, past the "
                f"{MAX_COMMAND_CHARS:,} I take in one turn. Save it to a file and ask me "
                f"about that — try `summarize file <path>` or `ask file <path> about …`.",
                length=len(command),
                limit=MAX_COMMAND_CHARS,
            )

        # A NUL byte, or a path segment longer than any filesystem allows. Linux refuses
        # these with an error (explained by the last line of defence); Windows on 3.13 just
        # reports them missing, and the reply echoed the whole name back.
        if lowered.startswith(_FILE_VERBS) and ("\x00" in command or re.search(r"[^\s/\\]{256,}", command)):
            return ToolResult.failure(_UNUSABLE_PATH)

        if _allow_planner and _whole:
            answered = self._follow_up(command, history_turns)
            if answered is not None:
                command, lowered = answered, answered.lower()
            groups = self._split_requests(command)
            if groups:
                return await self._run_each(groups, history_turns)

        # The dispatch is a table, not a 500-line chain. Each group returns a result or
        # None to mean 'not mine'; order is preserved exactly as it was, and the
        # shadowing test in tests/test_command_dispatch.py still reads every prefix.
        if not (_allow_planner and self._reads_as_prose(command, lowered)):
            refusal, allowed = self._account_limits(command, lowered, _allow_planner)
            if refusal is not None:
                return refusal
            for dispatch in self._DISPATCH if allowed else ():
                handled = await dispatch(self, command, lowered, history_turns)
                if handled is not None:
                    return handled

        if _allow_planner:
            planned = self._route(command, self.context.memory.get_profile(), history_turns)
            if planned.is_chat and planned.response and planned.explanation == _VERBATIM:
                return ToolResult.success(planned.response)
            if planned.is_command and planned.command and planned.command.strip().lower() != lowered:
                result = await self._run_routed(planned.command, history_turns)
                if not result.data.get("unrecognised"):
                    return self._routed_reply(result, command, planned)
                # An invented command no tool runs ("currency convert 100 usd eur") is answered as
                # the user's own sentence, the way a command handed back unchanged is (#167).
                planned = PlanDecision(action="chat", confidence=planned.confidence, explanation=_INVENTED)
            if planned.is_command:  # handed back unchanged: the dispatch above already declined it
                planned = PlanDecision(action="chat", confidence=planned.confidence, explanation=_DECLINED)
            if planned.is_chat:
                # Time-sensitive questions ("latest", "did X end", a recent year, …) must
                # not be answered from stale model knowledge — search the web first and
                # answer grounded in the results. Falls back to normal chat if there is no
                # LLM or the search returns nothing.
                needs_fresh = planned.explanation != SMALL_TALK and self._needs_fresh_info(command)
                if needs_fresh:
                    grounded = self._grounded_news_answer(command, history_turns, on_token)
                    if grounded is not None:
                        return grounded
                response = planned.response or ""
                profile = self._facts()
                level = self._complexity(command)
                _, requested_label = self._pick_chat_model(level)
                model_used = "fast"
                # Escalate by complexity (fast -> smart -> ultra), but degrade
                # gracefully: if a higher tier is congested/unreachable it returns
                # nothing, so we try the next tier down rather than failing.
                answered = False
                attempted: set[str] = set()
                for tier_planner, tier_label in self._higher_chat_tiers(level):
                    if not self.model_status.should_attempt(tier_label):
                        continue
                    attempted.add(tier_label)
                    why: list[tuple[str, str]] = []
                    reply = self._tier_reply(
                        tier_planner.provider, command, profile, history_turns, on_token, failures=why
                    )
                    self._record_tier(tier_label, reply, why)
                    if reply:
                        response, model_used, answered = reply, tier_label, True
                        break
                if not answered:
                    # Fall back to fast when it is not in its brief failure cooldown.
                    # A planned non-streaming reply already came from that tier.
                    fast_provider = self.planner.provider if self.planner else None
                    real_fast = fast_provider is not None and type(fast_provider).__name__ != "HeuristicPlannerProvider"
                    fast_available = self.model_status.should_attempt("fast")
                    if real_fast and fast_available:
                        attempted.add("fast")
                    # A router that chose chat but left the text to the answerer (a
                    # follow-up on the conversation) is asked for the reply too, rather
                    # than being counted as a dead endpoint. So is the instant router's
                    # canned small talk: it is the fallback for when no model answers, never
                    # the model's answer. Taking it as one is how "translate hello to
                    # spanish" was answered "I am here and ready..." on every client that
                    # does not stream.
                    canned = planned.explanation == SMALL_TALK
                    deferred = real_fast and (not planned.response or canned) and planned.confidence > 0
                    if fast_provider is not None and fast_available and (on_token is not None or deferred):
                        why = []
                        reply = self._tier_reply(fast_provider, command, profile, history_turns, on_token, failures=why)
                        if real_fast:
                            self._record_tier("fast", reply, why)
                        if reply:
                            response, model_used, answered = reply, "fast", True
                    elif real_fast and fast_available and planned.response and planned.confidence > 0 and not canned:
                        model_used, answered = "fast", True
                        self.model_status.record("fast", True)
                    elif real_fast and fast_available:
                        # The failed routing call is all this turn learned about the fast
                        # tier. Recorded without its reason it reads as busy, which demoted
                        # a retired model id or a rejected key from broken.
                        reason, detail = planned.failure or ("", "")
                        self.model_status.record("fast", False, reason=reason, detail=detail)
                if not answered:
                    # A failed fast tier should promote this turn to any healthy
                    # primary tier that has not already been tried.
                    for tier_planner, tier_label in self._recovery_chat_tiers(attempted):
                        attempted.add(tier_label)
                        why = []
                        reply = self._tier_reply(
                            tier_planner.provider, command, profile, history_turns, on_token, failures=why
                        )
                        self._record_tier(tier_label, reply, why)
                        if reply:
                            response, model_used, answered = reply, tier_label, True
                            break
                if (
                    not answered
                    and self.fallback_planner is not None
                    and self.model_status.should_attempt("openrouter")
                ):
                    # Cross-provider safety net is also cooled down after an error.
                    why = []
                    reply = self._tier_reply(
                        self.fallback_planner.provider, command, profile, history_turns, on_token, failures=why
                    )
                    self._record_tier("openrouter", reply, why)
                    if reply:
                        response, model_used, answered = reply, "openrouter", True
                if not answered and not response:
                    response = (
                        "I couldn't reach any configured language model just now. Please try again in a minute."
                        if self._has_language_model()
                        else _NO_MODEL_REPLY
                    )
                    model_used = "unavailable"
                degraded = model_used != requested_label
                trace = current_trace()
                if trace is not None:
                    trace.kind = "chat"
                    trace.model, trace.requested_model, trace.degraded = model_used, requested_label, degraded
                if degraded:
                    # Be honest about the fallback rather than passing off a lesser
                    # model's answer as the requested one's.
                    if model_used == "openrouter":
                        note = "\n\n_(My usual models were busy, so I answered with a backup model.)_"
                    elif model_used == "ultra":
                        note = f"\n\n_(My {requested_label} model was busy, so I answered with my deep model.)_"
                    elif model_used == "smart":
                        note = f"\n\n_(My {requested_label} model was busy, so I answered with my balanced model.)_"
                    elif model_used == "unavailable":
                        note = ""
                    else:
                        note = f"\n\n_(My {requested_label} model was busy, so I answered with my faster model.)_"
                    response = response + note
                    if note and on_token is not None:
                        on_token(note)
                if needs_fresh:
                    # We wanted live data but couldn't get it (no results / search down) — be
                    # honest that this answer may be stale rather than sounding confident.
                    note = "\n\n_Note: I couldn't reach live web search just now, so this may be out of date. Try again, or ask me to “search the web for …”._"
                    response = response + note
                    if on_token is not None:
                        on_token(note)
                return ToolResult.success(
                    response,
                    stale_warning=needs_fresh,
                    degraded=degraded,
                    planner={
                        "source_text": command,
                        "confidence": planned.confidence,
                        "explanation": planned.explanation,
                        "model": model_used,
                        "requested_model": requested_label,
                    },
                )

        return self._conversation_fallback(command)

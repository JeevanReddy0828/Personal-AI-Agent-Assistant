"""What is wrong with me right now, in one answer.

Finding out used to mean asking four places, and none of them asked a model whether it
answers: `selfcheck` runs the routing contract, `failures` lists what was caught, `latency`
reads the traces and the Setup panel says what is configured. `diagnose` ran only the first,
so it said "all checks pass" while the chat tier could be broken.

This gathers all four, then makes one live call to each configured model tier and one web
search, and ranks what it found as problems (something is broken) or warnings (something
is slow, busy or not installed), each with what to do. Like the Setup report it never shows
a model id, a key or a path: the reason a tier failed names its model id, so the wording
here is rebuilt from the HTTP status alone.
"""

from __future__ import annotations

import contextvars
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from laptop_agent.failures import FAILURES, record_failure
from laptop_agent.health import setup_report
from laptop_agent.model_status import BROKEN, TIERS
from laptop_agent.planner.openai_compatible import _PERMANENT_ADVICE
from laptop_agent.safety import ApprovalDenied
from laptop_agent.selfcheck import run_selfcheck
from laptop_agent.storage import storage_warnings

# A tier still silent after this is reported slow and left running: the ultra tier's own
# timeout is 420 s, and one hung call must not hold the whole report for that long.
PROBE_DEADLINE = 30.0
PROBE_QUERY = "weather"
# The failure log keeps 200 records across restarts; a diagnosis is about now.
FAILURE_WINDOW = 3600.0
# Measured medians are 0.8-1.8 s to the first token and 8% of turns routed by a model
# (docs/design/conventions.md, item 5); past these, something has changed.
SLOW_FIRST_TOKEN_MS = 4000
MODEL_ROUTED_PERCENT = 25.0
MIN_TURNS = 5

# key, name, orchestrator attribute, model variable, key variable, base URL variable
_TIERS = (
    ("fast", "Chat model", "planner", "OPENAI_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
    ("smart", "Balanced model", "smart_planner", "OPENAI_SMART_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
    ("ultra", "Deep model", "ultra_planner", "OPENAI_ULTRA_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
    ("vision", "Vision model", "vision_planner", "OPENAI_VISION_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
    ("openrouter", "Backup model", "fallback_planner", "OPENROUTER_MODEL", "OPENROUTER_API_KEY",
     "OPENROUTER_BASE_URL"),
)
# Setup rows a live probe answers more precisely, by the probes that cover them.
_COVERED = {"chat": {"fast"}, "deeper": {"smart", "ultra"}, "vision": {"vision"}, "backup": {"openrouter"}}
_HTTP = re.compile(r"HTTP (\d{3})\b")


@dataclass(frozen=True)
class Finding:
    severity: str  # "problem" (broken) or "warning" (slow, busy, missing)
    area: str
    detail: str
    fix: str | None = None


@dataclass(frozen=True)
class Probe:
    key: str
    name: str
    state: str  # "ok", "busy", "broken", "slow" or "skipped"
    seconds: float | None
    detail: str
    fix: str | None = None


@dataclass(frozen=True)
class Diagnosis:
    findings: list[Finding]
    probes: list[Probe]
    working: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def problems(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "problem"]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "warning"]

    @property
    def status(self) -> str:
        return "fail" if self.problems else "warn" if self.warnings else "ok"

    @property
    def verdict(self) -> str:
        problems, warnings = len(self.problems), len(self.warnings)
        if problems:
            tail = f" and {warnings} warning{'s' * (warnings != 1)}" if warnings else ""
            return f"{problems} problem{'s' * (problems != 1)}{tail}."
        if warnings:
            return f"No problems; {warnings} warning{'s' * (warnings != 1)}."
        return "Everything I checked is working."

    def markdown(self) -> str:
        lines = [f"**Self-diagnosis: {self.verdict}** Checked in {self.seconds:.1f} s."]
        for title, findings in (("Problems", self.problems), ("Warnings", self.warnings)):
            if findings:
                lines += ["", f"**{title}**"]
                lines += [f"- **{f.area}:** {f.detail}" + (f" {f.fix}" if f.fix else "") for f in findings]
        if self.working:
            lines += ["", "**Working:** " + ", ".join(self.working) + "."]
        if self.optional:
            lines += ["", "**Not set up (optional):** " + ", ".join(self.optional) + "."]
        return "\n".join(lines)

    def as_data(self) -> dict[str, object]:
        return {
            "status": self.status,
            "verdict": self.verdict,
            "findings": [asdict(finding) for finding in self.findings],
            "probes": [asdict(probe) for probe in self.probes],
            "working": list(self.working),
            "optional": list(self.optional),
            "seconds": round(self.seconds, 1),
        }


def _tier_failure(key: str, name: str, seconds: float, reason: str, detail: str,
                  model_var: str, key_var: str, base_var: str) -> Probe:
    found = _HTTP.search(detail)
    code = int(found[1]) if found else None
    if reason == BROKEN and code in _PERMANENT_ADVICE:
        fix = {401: key_var, 403: f"{key_var} and {model_var}"}.get(code, model_var)
        advice = _PERMANENT_ADVICE[code]
        return Probe(key, name, "broken", seconds, f"{advice[0].upper()}{advice[1:]} (HTTP {code}).",
                     f"Check {fix}.")
    # Busy means wait, so it carries no set-up step, as in the Setup panel.
    if code is not None:
        return Probe(key, name, "busy", seconds, f"Busy or rate-limited (HTTP {code}); replies fall back.")
    if "did not answer in time" in detail:
        return Probe(key, name, "busy", seconds, "Did not answer in time; replies fall back.")
    return Probe(key, name, "busy", seconds, "Could not be reached.", f"Check {base_var} and the network.")


def _ping_tier(status: Any, key: str, name: str, ping: Callable[..., bool],
               model_var: str, key_var: str, base_var: str) -> Probe:
    why: list[tuple[str, str]] = []
    started = time.perf_counter()
    try:
        ok = bool(ping(on_failure=lambda reason, detail: why.append((reason, detail))))
    except Exception as exc:  # a provider that raises instead of reporting
        record_failure(f"diagnosis/{key}", exc)
        ok = False
    seconds = round(time.perf_counter() - started, 1)
    reason, detail = why[-1] if why and not ok else ("", "")
    # Recorded like the keep-warm ping, with the reason: a success clears a tier marked
    # broken at once, and a failure recorded without one would read as busy.
    if key in TIERS and status is not None:
        status.record(key, ok, reason=reason, detail=detail)
    if ok:
        return Probe(key, name, "ok", seconds, "Answered.")
    return _tier_failure(key, name, seconds, reason, detail, model_var, key_var, base_var)


def _search(websearch: Any) -> Probe:
    started = time.perf_counter()
    try:
        result = websearch.search(PROBE_QUERY, limit=3)
    except ApprovalDenied:
        return Probe("search", "Web search", "skipped", None, "Not approved, so not tried.")
    except Exception as exc:  # a backend that raises instead of returning a failure
        record_failure("diagnosis/search", exc)
        result = None
    seconds = round(time.perf_counter() - started, 1)
    if result is not None and result.ok:
        return Probe("search", "Web search", "ok", seconds, "Answered.")
    return Probe("search", "Web search", "busy", seconds,
                 "Returned no results: the search endpoint may be rate-limited or unreachable.",
                 "If it keeps failing, set SEARCH_PROVIDER and SEARCH_API_KEY for a search API.")


def run_probes(orchestrator: Any, *, deadline: float = PROBE_DEADLINE) -> list[Probe]:
    """One 1-token call to each configured tier, at once, and one web search.

    The tiers run on daemon threads, so a call still out at the deadline is reported slow and
    left to finish (and record) on its own. The search runs on this thread, because it asks
    the approval gate, which reads the request's principal and cancellation."""
    status = getattr(orchestrator, "model_status", None)
    results: dict[str, Probe] = {}
    lock = threading.Lock()
    threads: list[tuple[str, str, threading.Thread]] = []
    for key, name, attr, model_var, key_var, base_var in _TIERS:
        ping = getattr(getattr(getattr(orchestrator, attr, None), "provider", None), "ping", None)
        if not callable(ping):
            continue  # not configured, or the built-in rules, which have nothing to call

        def probe(key: str = key, name: str = name, ping: Callable[..., bool] = ping,
                  model_var: str = model_var, key_var: str = key_var, base_var: str = base_var) -> None:
            outcome = _ping_tier(status, key, name, ping, model_var, key_var, base_var)
            with lock:
                results[key] = outcome

        context = contextvars.copy_context()
        thread = threading.Thread(target=context.run, args=(probe,), daemon=True, name=f"diagnose-{key}")
        thread.start()
        threads.append((key, name, thread))

    started = time.monotonic()
    websearch = getattr(getattr(orchestrator, "context", None), "websearch", None)
    searched = _search(websearch) if websearch is not None else None

    probes: list[Probe] = []
    for key, name, thread in threads:
        thread.join(max(0.0, deadline - (time.monotonic() - started)))
        with lock:
            outcome = results.get(key)
        probes.append(outcome or Probe(key, name, "slow", None, f"No answer within {deadline:.0f} s."))
    if searched is not None:
        probes.append(searched)
    return probes


def _setup(rows: list[dict[str, object]], probed: set[str]) -> tuple[list[Finding], list[str], list[str]]:
    findings: list[Finding] = []
    working: list[str] = []
    optional: list[str] = []
    for row in rows:
        key, name, state = str(row.get("key")), str(row.get("name")), str(row.get("state"))
        detail, fix = str(row.get("detail") or ""), (str(row["next"]) if row.get("next") else None)
        if _COVERED.get(key, set()) & probed:
            continue
        if state == "broken":
            findings.append(Finding("problem", name, detail, fix))
        elif state in ("missing", "busy") or (state == "ready" and fix):
            findings.append(Finding("warning", name, detail, fix))
        elif state == "off" and key == "chat":
            findings.append(Finding("warning", name, "No chat model is connected: " + detail[:1].lower()
                                    + detail[1:], fix))
        elif state == "off":
            optional.append(name)
        else:
            working.append(name)
    return findings, working, optional


def _failures() -> list[Finding]:
    counts: dict[str, int] = {}
    for entry in FAILURES.recent(200):
        age = entry.get("age_seconds")
        if isinstance(age, (int, float)) and age <= FAILURE_WINDOW:
            what = f"{entry.get('where')} ({entry.get('kind')})"
            counts[what] = counts.get(what, 0) + 1
    if not counts:
        return []
    total = sum(counts.values())
    top = sorted(counts.items(), key=lambda item: -item[1])[:3]
    named = ", ".join(f"{what} ×{count}" for what, count in top)
    return [Finding("warning", "Caught failures", f"{total} in the last hour, most often {named}.",
                    "Say `failures` for the details.")]


def _latency(traces: Any) -> tuple[list[Finding], bool]:
    stats = traces.summary() if traces is not None else {}
    turns = int(stats.get("turns") or 0)
    if turns < MIN_TURNS:
        return [], False
    findings: list[Finding] = []
    first_token = stats.get("median_ttft_ms")
    if int(stats.get("chat_turns") or 0) >= MIN_TURNS and isinstance(first_token, int) \
            and first_token > SLOW_FIRST_TOKEN_MS:
        findings.append(Finding("warning", "Slow replies",
                                f"Replies take {first_token / 1000:.1f} s to start (median of recent chat turns).",
                                "Say `latency` for where the time goes."))
    routed = float(stats.get("llm_routed_pct") or 0.0)
    if routed > MODEL_ROUTED_PERCENT:
        findings.append(Finding("warning", "Model routing",
                                f"{routed:g}% of recent turns waited for a model just to work out what was asked.",
                                "Say `latency` for the breakdown."))
    failed = int(stats.get("failed") or 0)
    if failed:
        findings.append(Finding("warning", "Failed turns", f"{failed} of the last {turns} turns failed.",
                                "Say `failures` for why."))
    return findings, not findings


def offline_checks(orchestrator: Any, setup_rows: list[dict[str, object]],
                   probed: set[str]) -> tuple[list[Finding], list[str], list[str]]:
    """Setup, storage, routing, caught failures and response times. No network."""
    findings, working, optional = _setup(setup_rows, probed)
    stored = storage_warnings()
    findings += [Finding("problem", "Storage", warning) for warning in stored]
    if not stored:
        working.append("storage")
    checks, _ = run_selfcheck(getattr(orchestrator, "router", None))
    failed = [check for check in checks if not check.ok]
    if failed:
        findings.append(Finding("problem", "Routing",
                                f"{len(failed)} of {len(checks)} phrasings no longer reach the right tool, "
                                f"such as {failed[0].name.split(': ', 1)[-1]!r}.",
                                "Say `selfcheck` for the list."))
    else:
        working.append(f"routing ({len(checks)} checks)")
    findings += _failures()
    slow, fine = _latency(getattr(orchestrator, "traces", None))
    findings += slow
    if fine:
        working.append("response times")
    return findings, working, optional


def local_setup_rows(orchestrator: Any) -> list[dict[str, object]]:
    """The Setup rows without the web server: from the CLI, sign-in and phone access are off."""
    from laptop_agent.config import load_config
    from laptop_agent.tools.transcribe import ocr_engine_name, stt_engine_name
    from laptop_agent.voice import tts_engine_name

    engines: dict[str, str | None] = {}
    for name, find in (("stt", stt_engine_name), ("tts", tts_engine_name), ("ocr", ocr_engine_name)):
        try:
            engines[name] = find()
        except Exception as exc:  # a broken engine must not break the diagnosis
            record_failure(f"diagnosis/{name}", exc)
            engines[name] = None
    return setup_report(orchestrator, load_config(), llm_reachable=None, stt_engine=engines["stt"],
                        tts_engine=engines["tts"], ocr_engine=engines["ocr"], sign_in=False, lan_mode=False)


def diagnose(orchestrator: Any, setup_rows: list[dict[str, object]]) -> Diagnosis:
    """Every offline check first, so the failures it reads are not the probes' own, then the
    live probes, problems before warnings."""
    started = time.perf_counter()
    configured = {key for key, _name, attr, *_ in _TIERS
                  if callable(getattr(getattr(getattr(orchestrator, attr, None), "provider", None), "ping", None))}
    offline, working, optional = offline_checks(orchestrator, setup_rows, configured)
    live = run_probes(orchestrator)
    answered: list[str] = []
    from_probes: list[Finding] = []
    for probe in live:
        if probe.state == "ok":
            answered.append(f"{probe.name} ({probe.seconds or 0.0:.1f} s)")
        elif probe.state == "broken":
            from_probes.append(Finding("problem", probe.name, probe.detail, probe.fix))
        elif probe.state != "skipped":
            from_probes.append(Finding("warning", probe.name, probe.detail, probe.fix))
    ranked = sorted(from_probes + offline, key=lambda finding: finding.severity != "problem")
    return Diagnosis(ranked, live, answered + working, optional, time.perf_counter() - started)

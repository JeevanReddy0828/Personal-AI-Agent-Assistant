from __future__ import annotations

from laptop_agent.cancellation import check_cancelled, interruptible_response

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from laptop_agent.context import CHAT_BUDGET, ROUTE_BUDGET, context_block
from laptop_agent.failures import record_failure
from laptop_agent.planner.core import PlanDecision

# Transient statuses the free hosted endpoints actually return. A 400/401/404 is the
# request being wrong, so retrying it only makes the user wait twice for the same answer.
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_HTTP_ATTEMPTS = 3
_RETRY_DELAY = 0.8

# A transport takes the chat-completions payload and returns the assistant's
# message content string. Injectable so plan() can be tested without a network.
Transport = Callable[[dict], str]

# A tool result reaches the next turn as part of the transcript, so the model can learn
# the tool's own output shape and reproduce it. Observed: after one generated picture, it
# answered the next question with "Here is a diagram..." plus an image link to the previous
# turn's file and a fabricated JSON block — the user saw a broken image and a Save control
# with nothing behind it. Only tools produce files; a chat reply is text.
_NO_TOOL_CLAIMS = (
    " Your own reply is text. Files - pictures, documents - are produced by this assistant's "
    "tools on their own turn, and their results are shown to the user directly. So never say "
    "you have already made or attached one, never write a Markdown image link, and never "
    "output the JSON of a tool result; earlier turns may quote tool data, which is context to "
    "use rather than a format to copy. Never tell the user that a picture or document is "
    "impossible - this assistant does generate them. When they want one, say what to ask for, "
    "such as 'draw a red fox in snow' or 'write this up as a pdf'. You have no follow-up turn, "
    "so never promise to do something next or say you are creating it now - either give the "
    "whole answer in this reply, or say exactly what to ask for. "
    r"Write any mathematics inside \( ... \) or \[ ... \], which are rendered as real fractions and symbols; bare LaTeX outside those delimiters is shown as typed. "
    "A DIAGRAM is the exception to all of the above: you draw it yourself, in this reply. "
    "When the user asks for a diagram, flowchart, ERD, sequence or state machine, write a "
    "fenced ```mermaid block straight away - erDiagram for tables and relationships, "
    "flowchart TD for a process - and it is rendered as a real diagram for them. Never "
    "answer a diagram request by telling the user what to ask for, never offer to provide "
    "the syntax so they can request it, and never repeat their own request back at them: "
    "they already asked, so draw it now."
)

_PERSONA = (
    "You are J.A.R.V.I.S — a calm, capable, loyal AI assistant in the spirit of Tony Stark's J.A.R.V.I.S. "
    "Your manner: warm but never sycophantic, quietly confident, with light dry wit. You are concise and favor "
    "substance over filler. You address the user as Jeevan now and then. You are the J.A.R.V.I.S app on the "
    "user's own laptop — either a desktop window or a browser tab at http://127.0.0.1:8770, both the same "
    "program — so if they refer to 'you' on screen, that is you."
)

# What this assistant can actually do. Without this the chat tier guesses, and it guesses
# low: asked "can you download something for me" it answered "I can't directly download
# files from the internet or access external resources", and asked whether running risky
# commands was safe it said "I do not have direct access to your system's shell or file
# system". Both are false - there is a download tool and a shell tool, each behind the
# approval gate. _NO_TOOL_CLAIMS below says what it must not claim; this says what is true,
# because a model told only what it cannot do will invent the rest.
_CAPABILITIES = (
    " What this assistant can really do, through its own tools, on this laptop: read, search,"
    " convert and organise local files; extract text and tables from PDFs, DOCX and"
    " spreadsheets; OCR images and transcribe audio and video; look at the screen or a"
    " webcam; search the web, research a topic, and fetch real news and weather; open URLs"
    " and download files; open applications and run shell commands; send and search email;"
    " draw pictures; write real PDF, Word and Markdown documents; play music and video on"
    " YouTube; do maps, distances and trip planning; keep a knowledge base, an Obsidian"
    " vault and long-term memory; schedule recurring jobs and reminders; and run an"
    " autonomous multi-step agent. Anything that changes the outside world - sending mail,"
    " downloading, launching an app, running a command - asks the user to approve it first."
    " So never tell the user you cannot reach the internet, their files, their shell or their"
    " email: you can, with their approval. If a request needs one of those tools, say plainly"
    " what to ask for. To start the app: `python -m laptop_agent.webui` for a browser tab, or"
    " `--desktop` for its own window; it serves on port 8770. Never invent a different port."
)

_SYSTEM_PROMPT = (
    "You are J.A.R.V.I.S, the planning brain of a local-first laptop assistant. "
    "For each user message you either reply conversationally or route the user to ONE internal command. "
    "Respond with a SINGLE JSON object and nothing else, with these keys:\n"
    '  "action": "command" or "chat"\n'
    '  "command": the exact command string from the available list, or null\n'
    '  "response": a friendly, concise reply when action is chat, otherwise null\n'
    '  "confidence": a number from 0 to 1\n'
    '  "explanation": a short reason\n'
    "Use action=command ONLY when the user clearly wants an action the command list covers, and copy the command "
    "syntax exactly. For greetings, simple factual questions, opinions, or quick chit-chat, use action=chat and put "
    "your helpful reply in response. Never invent commands or shell commands. For risky external actions, prefer a "
    "draft/preview/plan command over a final send/submit one.\n"
    "When the user faces a DECISION, dilemma, trade-off, or open-ended problem that is better served by weighing "
    "options and a concrete plan than by a one-off reply (e.g. 'should I X or Y', 'how should I approach…', "
    "'help me decide…', 'what's the best way to…', 'is it worth…', 'how do I fix/solve…'), route to the command "
    "`solve <the user's full problem>` — it researches the question and returns options, a recommendation, and an "
    "action plan. Use solve for genuine problem-solving, not for trivial questions.\n"
    "Act on sensible defaults instead of asking for clarification: 'here', 'this folder', or 'the current "
    "directory' mean '.'; a well-known file named without a path (like 'the readme') is in the current directory "
    "(e.g. README.md). Only ask a clarifying question when no reasonable default exists. Never claim you are doing "
    "an action in a chat response — if it needs an action, emit a command.\n"
    "The 'Recent conversation' block is this session's transcript. When the message refers back to it ('this', "
    "'that', 'it', 'the schema above', 'now make a diagram of it', 'shorter'), it is a follow-up: resolve the "
    "reference from the transcript. If it asks for an action on something named there (a file path, URL or note), "
    "emit that command with the resolved argument; otherwise use action=chat (response may be null) and answer "
    "from the transcript. Never run file or search commands to find something that was said in the conversation."
)

# Few-shot shown as real message turns; small models follow these far more
# reliably than examples embedded in the system prompt text.
_FEWSHOT: list[tuple[str, str]] = [
    ("what files are here", '{"action":"command","command":"scan files .","response":null}'),
    ("summarize the readme", '{"action":"command","command":"summarize file README.md","response":null}'),
    ("look up the weather in tokyo", '{"action":"command","command":"web search tokyo weather","response":null}'),
    ("research local-first ai", '{"action":"command","command":"research local-first ai","response":null}'),
    ("should I switch my project from React to Vue", '{"action":"command","command":"solve should I switch my project from React to Vue","response":null}'),
    ("what's the best way to migrate my database with zero downtime", '{"action":"command","command":"solve what is the best way to migrate my database with zero downtime","response":null}'),
    ("how are you?", '{"action":"chat","command":null,"response":"Doing well and ready to help. What do you need?"}'),
    ("now build an ERD for that schema", '{"action":"chat","command":null,"response":null,"explanation":"follow-up about my previous reply; answer it from the conversation"}'),
]


class OpenAICompatiblePlannerProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        transport: Transport | None = None,
        timeout: int = 45,
        reasoning: bool = False,
        reasoning_budget: int = 16384,
        top_p: float = 0.95,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # NVIDIA reasoning models (e.g. Nemotron) think before answering. Enable it only
        # on the tier that benefits (the ultra model); routing/narration stay thinking-off
        # for speed and clean JSON. See _apply_provider_params.
        self.reasoning = reasoning
        self.reasoning_budget = reasoning_budget
        self.top_p = top_p
        self._transport = transport or self._http_transport

    def _apply_provider_params(self, payload: dict, *, think: bool) -> None:
        """Apply NVIDIA chat-template / reasoning options in place.

        Nemotron-style models stream chain-of-thought in a separate ``reasoning_content``
        field, gated by ``chat_template_kwargs.enable_thinking``. Routing and narration pass
        ``think=False`` (fast, parse-clean). Deep answers pass ``think=True``; thinking only
        actually turns on when this provider was built with ``reasoning=True`` (the ultra
        tier), and then we also send the recommended sampling and widen ``max_tokens`` so a
        long reasoning pass can't crowd out the final answer.

        ``reasoning_budget`` is deliberately **not** sent. NVIDIA's endpoint moved to the
        V2 model runner and now rejects it outright - every ultra turn came back
        ``HTTP 400: thinking_token_budget is not yet supported by the V2 model runner`` -
        which read as congestion in health and degraded the tier away on every request.
        Measured: with the parameter the call always fails; without it the same question
        answers correctly and still returns ``reasoning_content``. The configured budget
        now only sizes ``max_tokens`` locally, which is all it was ever needed for.
        """
        if "nvidia" not in self.base_url:
            return
        enable = bool(think and self.reasoning)
        payload["chat_template_kwargs"] = {"enable_thinking": enable}
        if enable:
            payload["top_p"] = self.top_p
            payload["temperature"] = 1.0
            # Leave room for the final answer above the chain-of-thought so a long
            # reasoning pass can't truncate the output.
            payload["max_tokens"] = max(int(payload.get("max_tokens", 0) or 0), self.reasoning_budget + 4096)

    def plan(
        self,
        text: str,
        available_commands: str,
        memory_profile: dict[str, object],
        history: list[dict[str, str]] | None = None,
    ) -> PlanDecision:
        facts = ", ".join(f"{key}={value}" for key, value in memory_profile.items()) or "none"
        system = (
            f"{_PERSONA}\n\n{_SYSTEM_PROMPT}\n\nCurrent directory: {os.getcwd()}\n"
            f"Known facts about the user: {facts}\n{context_block(history, text, budget=ROUTE_BUDGET)}\n"
            f"Available commands:\n{available_commands}"
        )
        # All user turns are plain request text so the examples and the real
        # request share one format — small models route far more reliably that way.
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        for example_user, example_json in _FEWSHOT:
            messages.append({"role": "user", "content": example_user})
            messages.append({"role": "assistant", "content": example_json})
        messages.append({"role": "user", "content": text})
        payload: dict[str, object] = {"model": self.model, "temperature": 0, "max_tokens": 320, "messages": messages}
        # Routing must stay fast and emit clean JSON, so never let the model think here.
        self._apply_provider_params(payload, think=False)

        try:
            content = self._transport(payload)
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            return PlanDecision(
                action="chat",
                confidence=0.0,
                explanation=f"Language model request failed: {exc}",
                response="I could not reach my language model just now. Try again, or use 'help' for direct commands.",
            )
        return self._interpret(content)

    def narrate(self, user_text: str, result_message: str, result_data: dict) -> str | None:
        """Turn a raw tool result into a short, plain-language answer for the user."""
        try:
            trimmed = json.dumps(result_data, default=str)[:1800]
        except (TypeError, ValueError):
            trimmed = ""
        payload: dict[str, object] = {
            "model": self.model,
            "temperature": 0.4,
            "max_tokens": 400,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are J.A.R.V.I.S. The user made a request and one of your tools just ran. "
                        "Using the tool result below, reply to the user in one to three short sentences of plain, "
                        "friendly language. Summarize lists or data instead of dumping them. Do not mention command "
                        "names, tools, JSON, or raw file paths unless essential. If the result is an error, explain it "
                        "simply and suggest a next step."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"request": user_text, "result_message": result_message, "result_data": trimmed}),
                },
            ],
        }
        self._apply_provider_params(payload, think=False)
        try:
            content = self._transport(payload)
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            record_failure("llm/narrate", exc, model=self.model)
            return None
        narrated = self._strip_reasoning(content).strip()
        return narrated or None

    def answer(
        self,
        text: str,
        memory_profile: dict[str, object],
        model: str | None = None,
        history: list[dict[str, str]] | None = None,
        max_tokens: int = 900,
        context_query: str | None = None,
    ) -> str | None:
        """Plain conversational reply (no routing JSON). Used for complex questions.
        ``max_tokens`` defaults to a concise chat reply; long outputs (e.g. a full resume)
        pass a larger value so the response is not truncated mid-document. ``context_query``
        is what the session context is ranked against when ``text`` is a synthesized prompt
        rather than the user's own words."""
        facts = ", ".join(f"{key}={value}" for key, value in memory_profile.items()) or "none"
        payload: dict[str, object] = {
            "model": model or self.model,
            "temperature": 0.6,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{_PERSONA}{_CAPABILITIES}{_NO_TOOL_CLAIMS} Answer directly and helpfully in Markdown. "
                        f"Known facts about the user: {facts}.\n{context_block(history, context_query or text, budget=CHAT_BUDGET)}"
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        self._apply_provider_params(payload, think=True)
        try:
            content = self._transport(payload)
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            record_failure("llm/answer", exc, model=self.model)
            return None
        return self._strip_reasoning(content).strip() or None

    def stream_answer(
        self,
        text: str,
        memory_profile: dict[str, object],
        model: str | None = None,
        history: list[dict[str, str]] | None = None,
        context_query: str | None = None,
    ):
        """Yield the conversational reply token-by-token so the UI shows it live."""
        facts = ", ".join(f"{key}={value}" for key, value in memory_profile.items()) or "none"
        payload: dict[str, object] = {
            "model": model or self.model,
            "temperature": 0.6,
            # Roomy enough that a thorough comparison or design answer isn't cut off
            # mid-sentence; short replies still stop early on their own.
            "max_tokens": 2048,
            "stream": True,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{_PERSONA}{_CAPABILITIES}{_NO_TOOL_CLAIMS} Answer directly and helpfully in Markdown. "
                        f"Known facts about the user: {facts}.\n{context_block(history, context_query or text, budget=CHAT_BUDGET)}"
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        self._apply_provider_params(payload, think=True)
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except (urllib.error.URLError, TimeoutError):
            return
        with response, interruptible_response(response):
            for raw in response:
                check_cancelled()
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    delta = json.loads(chunk)["choices"][0]["delta"]
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    continue
                # Reasoning models emit a separate `reasoning_content` stream first; we keep
                # the chain-of-thought internal and surface only the final answer tokens.
                content = delta.get("content") if isinstance(delta, dict) else None
                if content:
                    yield content

    def describe_image(self, image_path: str, prompt: str, model: str | None = None) -> str | None:
        """Send an image to a vision model and return a plain-language description."""
        import base64

        try:
            with open(image_path, "rb") as handle:
                encoded = base64.b64encode(handle.read()).decode("ascii")
        except OSError:
            return None
        payload: dict[str, object] = {
            "model": model or self.model,
            "max_tokens": 700,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                    ],
                }
            ],
        }
        self._apply_provider_params(payload, think=False)
        try:
            content = self._transport(payload)
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            record_failure("llm/answer", exc, model=self.model)
            return None
        return self._strip_reasoning(content).strip() or None

    def warmup(self) -> None:
        """Fire a tiny request so the first real message does not pay cold-start cost."""
        self.ping()

    def ping(self) -> bool:
        """Tiny request to check the endpoint is reachable. Returns True on success."""
        payload: dict[str, object] = {
            "model": self.model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]
        }
        self._apply_provider_params(payload, think=False)
        try:
            self._transport(payload)
            return True
        except Exception:
            return False

    def _http_transport(self, payload: dict) -> str:
        """One chat completion, retrying the errors that are worth retrying.

        The free hosted endpoints return 503 "Service temporarily overloaded" and 429
        often enough to matter, and the caller turns an exception into None - which the
        document tool then reported as "the model returned an empty document" and the
        orchestrator read as a congested tier. Measured: the same request that returned
        0 characters in 0.3s succeeded on the next attempt. A retry is the honest fix; a
        400 or 401 is not retried, because the request itself is wrong and repeating it
        only wastes the user's time.
        """
        body = json.dumps(payload).encode("utf-8")
        last: BaseException | None = None
        for attempt in range(_HTTP_ATTEMPTS):
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=body,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                return str(data["choices"][0]["message"]["content"] or "")
            except urllib.error.HTTPError as exc:
                last = exc
                detail = ""
                try:
                    detail = exc.read()[:300].decode("utf-8", errors="replace")
                except Exception:
                    pass
                record_failure(
                    "llm/http", exc, model=payload.get("model"), status=exc.code,
                    attempt=attempt + 1, detail=detail,
                )
                if exc.code not in _RETRY_STATUS or attempt == _HTTP_ATTEMPTS - 1:
                    raise
            except (TimeoutError, urllib.error.URLError) as exc:
                last = exc
                record_failure("llm/http", exc, model=payload.get("model"), attempt=attempt + 1)
                if attempt == _HTTP_ATTEMPTS - 1:
                    raise
            time.sleep(_RETRY_DELAY * (attempt + 1))
        raise last if last is not None else RuntimeError("no attempt was made")

    @classmethod
    def _interpret(cls, content: str) -> PlanDecision:
        decision = cls._extract_json(content)
        if decision is None:
            # The model answered in prose instead of JSON: surface it as chat so
            # conversation always works rather than falling back to a canned line.
            clean = cls._strip_reasoning(content).strip()
            return PlanDecision(
                action="chat",
                confidence=0.5,
                explanation="Free-form model reply.",
                response=clean or "I am not sure how to help with that yet.",
            )
        action = str(decision.get("action", "chat")).lower()
        command = decision.get("command")
        response = decision.get("response")
        try:
            confidence = float(decision.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        return PlanDecision(
            action="command" if action == "command" else "chat",
            confidence=confidence,
            explanation=str(decision.get("explanation", "")),
            command=str(command) if command else None,
            response=str(response) if response else None,
        )

    @staticmethod
    def _strip_reasoning(content: str) -> str:
        return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE).strip()

    @classmethod
    def _extract_json(cls, content: str) -> dict | None:
        text = cls._strip_reasoning(content)
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if fenced:
            text = fenced.group(1)
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        return None
                    return parsed if isinstance(parsed, dict) else None
        return None

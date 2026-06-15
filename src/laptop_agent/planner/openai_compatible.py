from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable

from laptop_agent.planner.core import PlanDecision

# A transport takes the chat-completions payload and returns the assistant's
# message content string. Injectable so plan() can be tested without a network.
Transport = Callable[[dict], str]

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
    "syntax exactly. For greetings, questions, opinions, or anything conversational, use action=chat and put your "
    "helpful reply in response. Never invent commands or shell commands. For risky external actions, prefer a "
    "draft/preview/plan command over a final send/submit one.\n"
    "Act on sensible defaults instead of asking for clarification: 'here', 'this folder', or 'the current "
    "directory' mean '.'; a well-known file named without a path (like 'the readme') is in the current directory "
    "(e.g. README.md). Only ask a clarifying question when no reasonable default exists. Never claim you are doing "
    "an action in a chat response — if it needs an action, emit a command."
)

# Few-shot shown as real message turns; small models follow these far more
# reliably than examples embedded in the system prompt text.
_FEWSHOT: list[tuple[str, str]] = [
    ("what files are here", '{"action":"command","command":"scan files .","response":null}'),
    ("summarize the readme", '{"action":"command","command":"summarize file README.md","response":null}'),
    ("look up the weather in tokyo", '{"action":"command","command":"web search tokyo weather","response":null}'),
    ("research local-first ai", '{"action":"command","command":"research local-first ai","response":null}'),
    ("how are you?", '{"action":"chat","command":null,"response":"Doing well and ready to help. What do you need?"}'),
]


class OpenAICompatiblePlannerProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        transport: Transport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._transport = transport or self._http_transport

    def plan(self, text: str, available_commands: str, memory_profile: dict[str, object]) -> PlanDecision:
        facts = ", ".join(f"{key}={value}" for key, value in memory_profile.items()) or "none"
        system = (
            f"{_SYSTEM_PROMPT}\n\nCurrent directory: {os.getcwd()}\n"
            f"Known facts about the user: {facts}\n\nAvailable commands:\n{available_commands}"
        )
        # All user turns are plain request text so the examples and the real
        # request share one format — small models route far more reliably that way.
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        for example_user, example_json in _FEWSHOT:
            messages.append({"role": "user", "content": example_user})
            messages.append({"role": "assistant", "content": example_json})
        messages.append({"role": "user", "content": text})
        payload: dict[str, object] = {"model": self.model, "temperature": 0, "max_tokens": 320, "messages": messages}
        # Nemotron and other reasoning models stream chain-of-thought separately;
        # disabling it keeps responses fast and the content clean for parsing.
        if "nvidia" in self.base_url:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

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
        if "nvidia" in self.base_url:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            content = self._transport(payload)
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError, TypeError):
            return None
        narrated = self._strip_reasoning(content).strip()
        return narrated or None

    def answer(self, text: str, memory_profile: dict[str, object], model: str | None = None) -> str | None:
        """Plain conversational reply (no routing JSON). Used for complex questions."""
        facts = ", ".join(f"{key}={value}" for key, value in memory_profile.items()) or "none"
        payload: dict[str, object] = {
            "model": model or self.model,
            "temperature": 0.6,
            "max_tokens": 900,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are J.A.R.V.I.S, a sharp, concise local-first assistant. You run as a desktop app window "
                        "titled 'J.A.R.V.I.S' on the user's screen, so if they refer to 'you' on screen, that window is "
                        f"you. Answer directly and helpfully in Markdown. Known facts about the user: {facts}."
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        if "nvidia" in self.base_url:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            content = self._transport(payload)
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError, TypeError):
            return None
        return self._strip_reasoning(content).strip() or None

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
        try:
            content = self._transport(payload)
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError, TypeError):
            return None
        return self._strip_reasoning(content).strip() or None

    def warmup(self) -> None:
        """Fire a tiny request so the first real message does not pay cold-start cost."""
        try:
            self._transport(
                {"model": self.model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}
            )
        except Exception:
            pass

    def _http_transport(self, payload: dict) -> str:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"] or "")

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

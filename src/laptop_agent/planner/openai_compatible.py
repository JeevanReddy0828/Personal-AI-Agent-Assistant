from __future__ import annotations

import json
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
    "draft/preview/plan command over a final send/submit one."
)


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
        payload: dict[str, object] = {
            "model": self.model,
            "temperature": 0.3,
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": text,
                            "available_commands": available_commands,
                            "memory_profile": {str(key): str(value) for key, value in memory_profile.items()},
                        },
                        sort_keys=True,
                    ),
                },
            ],
        }
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

from __future__ import annotations

import json
import urllib.error
import urllib.request

from laptop_agent.planner.core import PlanDecision


class OpenAICompatiblePlannerProvider:
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1") -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def plan(self, text: str, available_commands: str, memory_profile: dict[str, object]) -> PlanDecision:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You route a local personal assistant request. Return only compact JSON with keys: "
                        "action, confidence, explanation, command, response. action must be command or chat. "
                        "Only produce a command from the available command list. Never invent shell commands. "
                        "For risky external actions, choose a preparation/draft command, not final submission."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": text,
                            "available_commands": available_commands,
                            "memory_profile_keys": sorted(memory_profile.keys()),
                        },
                        sort_keys=True,
                    ),
                },
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            return PlanDecision(action="chat", confidence=0, explanation=f"Planner API request failed: {exc}", response="The remote planner is unavailable.")

        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            decision = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            return PlanDecision(action="chat", confidence=0, explanation=f"Planner API returned an unreadable response: {exc}", response="The remote planner response was not usable.")

        return PlanDecision(
            action=str(decision.get("action", "chat")),
            confidence=float(decision.get("confidence", 0)),
            explanation=str(decision.get("explanation", "")),
            command=decision.get("command"),
            response=decision.get("response"),
        )

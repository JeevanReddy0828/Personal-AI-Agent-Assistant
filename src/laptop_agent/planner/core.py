from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PlanDecision:
    action: str
    confidence: float
    explanation: str
    command: str | None = None
    response: str | None = None

    @property
    def is_command(self) -> bool:
        return self.action == "command" and bool(self.command)

    @property
    def is_chat(self) -> bool:
        return self.action == "chat" and bool(self.response)


class PlannerProvider(Protocol):
    def plan(self, text: str, available_commands: str, memory_profile: dict[str, object]) -> PlanDecision:
        pass


class Planner:
    def __init__(self, provider: PlannerProvider) -> None:
        self.provider = provider

    def plan(self, text: str, available_commands: str, memory_profile: dict[str, object]) -> PlanDecision:
        return self.provider.plan(text, available_commands, memory_profile)

    def narrate(self, user_text: str, result_message: str, result_data: dict[str, object]) -> str | None:
        narrate = getattr(self.provider, "narrate", None)
        if narrate is None:
            return None
        return narrate(user_text, result_message, result_data)

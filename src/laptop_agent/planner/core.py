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

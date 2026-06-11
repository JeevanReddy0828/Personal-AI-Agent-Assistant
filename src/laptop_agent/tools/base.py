from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, message: str, **data: Any) -> "ToolResult":
        return cls(ok=True, message=message, data=data)

    @classmethod
    def failure(cls, message: str, **data: Any) -> "ToolResult":
        return cls(ok=False, message=message, data=data)

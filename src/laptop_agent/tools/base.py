from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def reserve_new_path(directory: Path, stem: str, suffix: str, limit: int = 500) -> Path:
    """Claim a free path under ``directory``, adding ``-2``, ``-3`` … if the name is taken.

    Generated files are named after their subject plus a second-resolution timestamp, which
    is not unique: two pictures of the same thing in the same second produced the same name
    and the second silently overwrote the first. The name is claimed by creating the file
    exclusively, so the check and the claim cannot race. The caller writes to the path it
    gets back.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, limit + 1):
        name = f"{stem}{suffix}" if attempt == 1 else f"{stem}-{attempt}{suffix}"
        path = directory / name
        try:
            path.touch(exist_ok=False)
        except FileExistsError:
            continue
        return path
    raise OSError(f"No free filename for {stem}{suffix} in {directory} after {limit} tries.")


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

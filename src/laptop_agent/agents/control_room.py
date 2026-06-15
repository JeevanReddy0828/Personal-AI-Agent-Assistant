from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
from time import time


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    name: str
    role: str
    commands: tuple[str, ...]
    available: bool = True
    detail: str = ""


@dataclass
class AgentState:
    definition: AgentDefinition
    status: str = "idle"  # "working" | "idle" | "unavailable"
    current_task: str | None = None
    last_message: str = "Ready."
    last_updated: float = 0.0

    def to_dict(self) -> dict[str, object]:
        data = asdict(self.definition)
        data.update(
            {
                "status": self.status if self.definition.available else "unavailable",
                "current_task": self.current_task,
                "last_message": self.last_message,
                "last_updated": self.last_updated,
            }
        )
        return data


class AgentControlRoom:
    """Live roster for the agent dashboard.

    The control room is intentionally local and small: it maps commands to
    specialist slots, marks slots working while subtasks run, and returns a
    dashboard-friendly snapshot. It does not start processes or grant new
    permissions; it only describes and tracks the orchestrator's existing tools.
    """

    def __init__(self, definitions: list[AgentDefinition]) -> None:
        self._lock = RLock()
        self._states = {
            item.id: AgentState(
                definition=item,
                status="idle" if item.available else "unavailable",
                last_updated=time(),
            )
            for item in definitions
        }

    @classmethod
    def standard(cls, *, obsidian_available: bool, browser_available: bool = True) -> AgentControlRoom:
        return cls(
            [
                AgentDefinition(
                    "planner",
                    "Planner",
                    "Routes natural language and chooses model tiers.",
                    ("help", "memory", "remember"),
                ),
                AgentDefinition(
                    "files",
                    "File Analyst",
                    "Reads, searches, summarizes, converts, and organizes local files.",
                    ("scan files", "read file", "summarize file", "extract text", "search files", "organize folder"),
                ),
                AgentDefinition(
                    "knowledge",
                    "Knowledge Librarian",
                    "Indexes documents and recalls saved knowledge.",
                    ("index file", "recall", "knowledge"),
                ),
                AgentDefinition(
                    "research",
                    "Research Scout",
                    "Searches the web, fetches pages, and builds research material.",
                    ("web search", "search web", "research", "research report", "save research report"),
                ),
                AgentDefinition(
                    "browser",
                    "Browser Operator",
                    "Inspects pages, detects forms, previews fills, and fills approved fields.",
                    ("inspect page", "inspect forms", "preview form fill", "fill form", "plan apply job"),
                    available=browser_available,
                ),
                AgentDefinition(
                    "vision",
                    "Vision Desk",
                    "Reads screenshots and describes images.",
                    ("read screen", "look at screen", "describe image", "look at image", "ocr image"),
                ),
                AgentDefinition(
                    "email",
                    "Mail Room",
                    "Searches inboxes, creates drafts, sends approved mail, and manages OAuth.",
                    ("email", "send email"),
                ),
                AgentDefinition(
                    "music",
                    "Media Console",
                    "Plays local/remote music and sends media-key commands.",
                    ("play music", "media"),
                ),
                AgentDefinition(
                    "notes",
                    "Obsidian Archivist",
                    "Reads, searches, and writes human-readable vault notes.",
                    ("notes", "vault", "obsidian", "read note", "save note", "remember note"),
                    available=obsidian_available,
                    detail="Set OBSIDIAN_VAULT to enable." if not obsidian_available else "",
                ),
                AgentDefinition(
                    "tasks",
                    "Task Foreman",
                    "Runs parallel subtasks, records dashboards, and retries failures.",
                    ("multi", "tasks", "task dashboard"),
                ),
            ]
        )

    def start(self, command: str, agent_id: str | None = None) -> str:
        selected = agent_id or self.agent_for(command)
        with self._lock:
            state = self._states[selected]
            if not state.definition.available:
                return selected
            state.status = "working"
            state.current_task = command
            state.last_message = "Working."
            state.last_updated = time()
        return selected

    def finish(self, agent_id: str, message: str, ok: bool) -> None:
        with self._lock:
            state = self._states.get(agent_id)
            if state is None or not state.definition.available:
                return
            state.status = "idle"
            state.current_task = None
            state.last_message = message if ok else f"Failed: {message}"
            state.last_updated = time()

    def agent_for(self, command: str) -> str:
        lowered = command.strip().lower()
        with self._lock:
            for agent_id, state in self._states.items():
                if any(lowered.startswith(prefix) for prefix in state.definition.commands):
                    return agent_id
        return "planner"

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            agents = [state.to_dict() for state in self._states.values()]
        working = sum(1 for agent in agents if agent["status"] == "working")
        idle = sum(1 for agent in agents if agent["status"] == "idle")
        unavailable = sum(1 for agent in agents if agent["status"] == "unavailable")
        return {
            "summary": {
                "total": len(agents),
                "working": working,
                "idle": idle,
                "available": working + idle,
                "unavailable": unavailable,
            },
            "agents": agents,
        }

    def detail(self, agent_id: str) -> dict[str, object] | None:
        with self._lock:
            state = self._states.get(agent_id)
            return state.to_dict() if state else None

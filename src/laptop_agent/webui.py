"""Local chat app for the laptop agent.

A stdlib-only HTTP server that serves a workstation-style chat interface and
wires it to the same AgentOrchestrator the CLI uses. It binds to localhost only.
Read/search/research actions run straight through. High-risk actions (sending email,
moving/overwriting files, downloads, launching apps, shell, browser form fills) put an
approval card in front of the user and wait for an answer; no answer means no.

Panes: chat sessions, a Markdown-rendered conversation with file upload and
voice, live system metrics (CPU/GPU/RAM), and the Obsidian memory vault.

Run:  python -m laptop_agent.webui                 (browser tab)
      python -m laptop_agent.webui --desktop       (frameless desktop app window)
      laptop-agent-deck                            (same desktop window, installed command)
"""

from __future__ import annotations

from laptop_agent.cancellation import operation, cancel, check_cancelled, OperationCancelled

import asyncio
import base64
import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from laptop_agent.app import build_orchestrator
from laptop_agent.cli import _json_safe
from laptop_agent.config import load_config
from laptop_agent.health import system_health
from laptop_agent.metrics import system_metrics
from laptop_agent.retention import sweep, sweep_uploads
from laptop_agent.approvals import ApprovalBroker
from laptop_agent.failures import FAILURES, record_failure
from laptop_agent.safety import ApprovalDenied, ApprovalRequest, RiskLevel
from laptop_agent.voice import SpeechChunker, clean_for_speech, synthesize_wav
from laptop_agent.webui_page import PAGE
from laptop_agent.window_fx import apply_window_effects

# Local single-user interface: origin checks and a per-process token protect mutations.
_CONFIG = load_config()
HOST = os.environ.get("LAPTOP_AGENT_HOST", "127.0.0.1")
if HOST not in {"127.0.0.1", "localhost"}:
    raise ValueError("J.A.R.V.I.S is a local single-user app. Set LAPTOP_AGENT_HOST to 127.0.0.1 or localhost.")
try:
    PORT = int(os.environ.get("LAPTOP_AGENT_PORT", "8770"))
except ValueError:
    PORT = 8770
UPLOAD_DIR = Path(tempfile.gettempdir()) / "laptop_agent_uploads"
MAX_UPLOAD_BYTES = 35 * 1024 * 1024
MAX_REQUEST_BYTES = ((MAX_UPLOAD_BYTES + 2) // 3) * 4 + 65536
_API_TOKEN = secrets.token_urlsafe(32)
_SCRIPT_NONCE = secrets.token_urlsafe(24)
_LLM_STATUS: dict[str, object] = {"reachable": None}  # cached, updated by warm-up cycle
# True only when serving the dedicated desktop window (run_desktop), so the HUD's
# real window effects (opacity / always-on-top) never touch a normal browser window.
_DESKTOP_MODE = False
# Only the formats the image tool writes are servable, so the route can never be used
# to read an arbitrary file that happens to sit in the images directory.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
# Same rule for written documents: only the formats the document tool writes are servable.
DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown; charset=utf-8",
}


def _compose_command(command: str, attachments: object) -> str:
    """Fold uploaded file paths into the command the orchestrator runs.

    A bare upload (no typed message) goes straight through the universal file
    processor, which auto-detects the type and picks the best action. When the
    user typed something, the paths are appended as context so the LLM/heuristic
    router can target them for whatever the message asks.
    """
    command = (command or "").strip()
    paths = [str(p) for p in attachments if p] if isinstance(attachments, list) else []
    if not paths:
        return command
    if not command:
        if len(paths) == 1:
            return _bare_attachment_command(paths[0])
        return "multi " + " ;; ".join(_bare_attachment_command(path) for path in paths)
    listing = "; ".join(paths)
    return (
        f"{command}\n\n[The user attached file(s) saved at: {listing}. "
        "Use the path(s) as the target for any file, image, audio, document, or indexing action.]"
    )


def _bare_attachment_command(path: str) -> str:
    """Best default command for a bare (no-message) upload. Images go through the
    vision-first ``describe image`` path (which itself falls back to OCR) so an attached
    photo is read by the configured vision model instead of dead-ending when the optional
    Tesseract binary is absent."""
    from laptop_agent.tools.transcribe import IMAGE_EXTENSIONS

    if Path(path).suffix.lower() in IMAGE_EXTENSIONS:
        return f"describe image {path}"
    return f"process file {path}"


def _stt_engine() -> str | None:
    """Name of the speech engine a recording would reach, cached after the first look:
    importing Whisper to answer a health poll is far too slow to do every time."""
    if "stt" not in _LLM_STATUS:
        from laptop_agent.tools.transcribe import stt_engine_name

        try:
            _LLM_STATUS["stt"] = stt_engine_name()
        except Exception as exc:  # a broken engine must not break health
            record_failure("stt/probe", exc)
            _LLM_STATUS["stt"] = None
    return _LLM_STATUS["stt"]  # type: ignore[return-value]


def _ocr_engine() -> str | None:
    """Name of the OCR engine an image would reach. Cached for the same reason."""
    if "ocr" not in _LLM_STATUS:
        from laptop_agent.tools.transcribe import ocr_engine_name

        try:
            _LLM_STATUS["ocr"] = ocr_engine_name()
        except Exception as exc:  # a broken engine must not break health
            record_failure("ocr/probe", exc)
            _LLM_STATUS["ocr"] = None
    return _LLM_STATUS["ocr"]  # type: ignore[return-value]


def _safe_artifact(name: str, folder: str, types: dict[str, str]) -> Path | None:
    """Resolve a generated file by bare filename, or None if it is not one of ours.

    The name must be a plain filename in that folder with a format we write, so a route
    can never be walked into an arbitrary file on disk."""
    if not name or name != Path(name).name or name.startswith("."):
        return None
    if Path(name).suffix.lower() not in types:
        return None
    directory = (_CONFIG.data_dir / folder).resolve()
    target = (directory / name).resolve()
    if target.parent != directory or not target.is_file():
        return None
    return target


def _document_path(name: str) -> Path | None:
    return _safe_artifact(name, "documents", DOCUMENT_TYPES)


def _image_path(name: str) -> Path | None:
    """Resolve a generated image by bare filename, or None if it is not one of ours.

    The name must be a plain filename in the images directory with a format the image
    tool writes, so the route can never be walked into an arbitrary file on disk.
    """
    return _safe_artifact(name, "images", IMAGE_TYPES)


def _schedule_snapshot() -> dict:
    """Current scheduled jobs as plain JSON for the web panel."""
    result = asyncio.run(_orchestrator.handle("schedule list"))
    return {"ok": result.ok, "message": result.message, "jobs": _json_safe(result.data.get("jobs", []))}


# Injectable so tests exercise the /api/tts success path without a speech engine.
_TTS_BACKEND = None


def _render_tts(text: str) -> bytes | None:
    return synthesize_wav(text, _TTS_BACKEND)


def _agent_runs_snapshot() -> dict:
    """Autonomous agent run history as plain JSON for the web panel."""
    result = asyncio.run(_orchestrator.handle("agent runs"))
    return {"ok": result.ok, "runs": _json_safe(result.data.get("agent_runs", []))}


def _jobs_snapshot() -> dict:
    """Job pipeline (records + chart-ready stats) as plain JSON for the dashboard."""
    jobs = _orchestrator.context.jobs
    return {"ok": True, "jobs": _json_safe(jobs.list()), "stats": _json_safe(jobs.stats())}


def _pipeline_snapshot() -> dict:
    """Pipeline view: jobs annotated with live ATS scores + base-resume status."""
    return _json_safe(_orchestrator.pipeline_snapshot())


def _model_label(provider) -> str:
    model = getattr(provider, "model", "") or ""
    return model.split("/")[-1][:20] if model else "llm"


def _planner_label() -> str:
    provider = getattr(_orchestrator.planner, "provider", None)
    if provider and "OpenAI" in type(provider).__name__:
        return _model_label(provider)
    return "heuristic"


def _smart_label() -> str:
    planner = getattr(_orchestrator, "smart_planner", None)
    provider = getattr(planner, "provider", None) if planner else None
    if provider and "OpenAI" in type(provider).__name__:
        return _model_label(provider)
    return "—"


def _ultra_label() -> str:
    planner = getattr(_orchestrator, "ultra_planner", None)
    provider = getattr(planner, "provider", None) if planner else None
    if provider and "OpenAI" in type(provider).__name__:
        return _model_label(provider)
    return "—"


def _vision_label() -> str:
    planner = getattr(_orchestrator, "vision_planner", None)
    provider = getattr(planner, "provider", None) if planner else None
    if provider and "OpenAI" in type(provider).__name__:
        return _model_label(provider)
    return "—"


# Reading is waved through; anything that changes the outside world is put to the user.
# Before this, the browser could not answer the gate at all, so every HIGH/CRITICAL action
# was auto-denied and downloads, shell commands and sending mail did not work in the app's
# main interface.
_APPROVALS = ApprovalBroker()


def _guarded_approval(request: ApprovalRequest) -> bool:
    if request.risk == RiskLevel.MEDIUM:
        return True
    return _APPROVALS.request(
        action=request.action,
        risk=request.risk.value,
        reason=request.reason,
        preview=request.preview,
    )


_orchestrator = build_orchestrator(approval_callback=_guarded_approval)


def _probe_llm(ping: Callable[[], bool], attempts: int = 2, delay: float = 1.5) -> bool:
    """True if any ping succeeds. The free hosted endpoints refuse an occasional
    request, and one refusal used to mark the model unreachable in the header for the
    whole 200s until the next warm cycle — so give it a second chance, as the web
    search path already does for its flaky endpoint."""
    for attempt in range(attempts):
        try:
            if ping():
                return True
        except (OSError, TimeoutError):
            pass
        if attempt + 1 < attempts:
            time.sleep(delay)
    return False


def _refresh_llm_status() -> None:
    """Ping the model and cache reachability (also keeps it warm)."""
    provider = getattr(_orchestrator.planner, "provider", None)
    ping = getattr(provider, "ping", None)
    if ping is None:
        _LLM_STATUS["reachable"] = None  # heuristic planner — not applicable
        return
    reachable = _probe_llm(ping)
    _LLM_STATUS["reachable"] = reachable
    _orchestrator.model_status.record("fast", reachable)


def _warmup() -> None:
    threading.Thread(target=_refresh_llm_status, daemon=True).start()


def _keep_warm(interval: float = 200.0) -> None:
    """Ping the fast model periodically so it does not go cold between messages,
    and refresh the cached reachability used by the health check."""
    if getattr(getattr(_orchestrator.planner, "provider", None), "ping", None) is None:
        return

    def loop() -> None:
        stop = threading.Event()
        while not stop.wait(interval):
            _refresh_llm_status()

    threading.Thread(target=loop, daemon=True).start()



# Generated artifacts and upload scratch folders were never cleaned: one day of use left
# 7.1MB of images and a temp directory with one folder per attachment, forever.
_LAST_SWEEP = [0.0]
_SWEEP_EVERY = 3600.0


def _housekeeping() -> None:
    """Trim generated artifacts, at most once an hour, on the ticker thread."""
    now = time.time()
    if now - _LAST_SWEEP[0] < _SWEEP_EVERY:
        return
    _LAST_SWEEP[0] = now
    try:
        outcome = sweep(_CONFIG.data_dir)
        stale = sweep_uploads(UPLOAD_DIR)
        if outcome.get("removed") or stale:
            record_failure(
                "retention/swept",
                f"removed {outcome.get('removed')} artifacts and {stale} upload folder(s)",
            )
    except Exception as exc:  # housekeeping must never take the ticker down
        record_failure("retention/sweep", exc)


def _schedule_ticker(interval: float = 60.0) -> None:
    """Fire any due scheduled jobs once a minute. Daemon thread so it stops with the app."""

    def loop() -> None:
        stop = threading.Event()
        while not stop.wait(interval):
            try:
                asyncio.run(_orchestrator.run_due_schedules())
                _housekeeping()
            except Exception as exc:
                # Never let one scheduled run take down the ticker - but a ticker that
                # swallows failures silently means scheduled jobs can stop working and
                # nobody finds out until they are missed.
                record_failure("scheduler/tick", exc)

    threading.Thread(target=loop, daemon=True).start()



class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer with a realistic listen backlog.

    The stdlib default is 5 pending connections, and anything past that is refused by the
    OS before Python ever sees it - measured: 30 simultaneous requests produced one hard
    ECONNREFUSED. The page itself opens several at once (the SSE stream, health polls,
    images), so two tabs can reach this.
    """

    request_queue_size = 128
    daemon_threads = True
    # A socket left in TIME_WAIT should not stop a restart from binding the same port.
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", (
            f"default-src 'self'; script-src 'nonce-{_SCRIPT_NONCE}'; "
            "style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; "
            "media-src 'self' blob:; connect-src 'self'; "
            "frame-src 'self' https://www.openstreetmap.org; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        ))
        super().end_headers()

    def _trusted_request(self, mutation: bool = False) -> bool:
        host = self.headers.get("Host", "")
        allowed = {f"{h}:{self.server.server_port}" for h in
                   ("127.0.0.1", "localhost", HOST, self.server.server_address[0])}
        origin = self.headers.get("Origin")
        if host not in allowed or (origin and origin != f"http://{host}"):
            self._json(403, {"ok": False, "message": "Untrusted request origin."})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._json(403, {"ok": False, "message": "Cross-site requests are blocked."})
            return False
        if mutation and not secrets.compare_digest(self.headers.get("X-Jarvis-Token", ""), _API_TOKEN):
            self._json(403, {"ok": False, "message": "Reload J.A.R.V.I.S before retrying."})
            return False
        return True

    def log_message(self, *args: object) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except ConnectionError:
            pass  # The client closed a completed, non-streaming response.

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, default=str).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        if not self._trusted_request():
            return
        path = self.path.split("?", 1)[0]  # ignore query (the native window loads /?app=1)
        if path in {"/", "/index.html"}:
            page = (
                PAGE.replace("{{PLANNER}}", _planner_label())
                .replace("{{SMART}}", _smart_label())
                .replace("{{ULTRA}}", _ultra_label())
                .replace("{{VISION}}", _vision_label())
                .replace("{{NONCE}}", _SCRIPT_NONCE)
                .replace("{{API_TOKEN}}", _API_TOKEN)
            )
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/health":
            report = system_health(_orchestrator, _LLM_STATUS.get("reachable"), _CONFIG)
            # The page decides between its own recognizer and posting audio here.
            report["stt"] = {"engine": _stt_engine()}
            report["ocr"] = {"engine": _ocr_engine()}
            self._json(200, report)
        elif path == "/api/metrics":
            self._json(200, system_metrics())
        elif path == "/api/agents":
            self._json(200, {"ok": True, "control_room": _json_safe(_orchestrator.control_room.snapshot())})
        elif path == "/api/vault":
            status = asyncio.run(_orchestrator.handle("notes status"))
            listing = asyncio.run(_orchestrator.handle("notes list"))
            self._json(
                200,
                {
                    "ok": status.ok,
                    "message": status.message,
                    "status": _json_safe(status.data),
                    "notes": listing.data.get("notes", []),
                },
            )
        elif path == "/api/schedule":
            self._json(200, _schedule_snapshot())
        elif path == "/api/failures":
            self._json(200, {"ok": True, "summary": FAILURES.summary(), "recent": FAILURES.recent(40)})
        elif path == "/api/approvals":
            self._json(200, {"ok": True, "pending": _APPROVALS.pending()})
        elif path == "/api/traces":
            self._json(200, {"ok": True, "summary": _orchestrator.traces.summary(),
                             "recent": _orchestrator.traces.recent(40)})
        elif path == "/api/agent-runs":
            self._json(200, _agent_runs_snapshot())
        elif path == "/api/jobs":
            self._json(200, _jobs_snapshot())
        elif path == "/api/pipeline":
            self._json(200, _pipeline_snapshot())
        elif path == "/api/resume-pdf":
            self._serve_resume_pdf()
        elif path == "/api/image":
            self._serve_image()
        elif path == "/api/document":
            self._serve_document()
        else:
            self._send(404, b"not found", "text/plain")

    def _read_json(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("payload too large")
        self.connection.settimeout(15)
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object")
        for key in ("command", "goal", "action", "name", "path", "text", "query", "data", "audio", "ext", "resume_text", "job_text", "company", "role", "stage", "when", "spec", "kind"):
            if key in payload and not isinstance(payload[key], str):
                raise ValueError(f"{key} must be text")
        for key in ("attachments", "stops"):
            if key in payload and (not isinstance(payload[key], list) or any(not isinstance(x, str) for x in payload[key])):
                raise ValueError(f"{key} must be a list of text values")
        if "history" in payload:
            history = payload["history"]
            if not isinstance(history, list) or len(history) > 100 or any(
                not isinstance(turn, dict) or turn.get("role") not in {"user", "assistant"}
                or not isinstance(turn.get("text", ""), str) for turn in history
            ):
                raise ValueError("Invalid chat history")
        return payload

    def do_POST(self) -> None:
        if not self._trusted_request(mutation=True):
            return
        try:
            if self.path in {"/api/stream", "/api/agent", "/api/command"}:
                request_id = self.headers.get("X-Jarvis-Request", secrets.token_hex(16))
                if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", request_id):
                    raise ValueError("Invalid request ID")
                with operation(request_id):
                    self._dispatch_post()
            else:
                self._dispatch_post()
        except OperationCancelled:
            pass
        except (ValueError, TypeError, UnicodeError):
            self._json(400, {"ok": False, "message": "Invalid request values."})
        except OSError:
            self._json(500, {"ok": False, "message": "Could not access local data. Check disk space and permissions."})

    def _dispatch_post(self) -> None:
        if self.path == "/api/approve":
            self._handle_approve()
            return
        if self.path == "/api/cancel":
            payload = self._read_json()
            request_id = payload.get("request_id", "")
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,80}", request_id):
                raise ValueError("Invalid request ID")
            active = cancel(request_id)
            self._json(200, {"ok": True, "active": active, "message": "Stop requested; no further steps will run."})
        elif self.path == "/api/upload":
            self._handle_upload()
        elif self.path == "/api/command":
            self._handle_command()
        elif self.path == "/api/stream":
            self._handle_stream()
        elif self.path == "/api/agent":
            self._handle_agent()
        elif self.path == "/api/schedule":
            self._handle_schedule()
        elif self.path == "/api/jobs":
            self._handle_jobs()
        elif self.path == "/api/copilot":
            self._handle_copilot()
        elif self.path == "/api/pipeline":
            self._handle_pipeline()
        elif self.path == "/api/map":
            self._handle_map()
        elif self.path == "/api/window":
            self._handle_window()
        elif self.path == "/api/notes":
            self._handle_notes()
        elif self.path == "/api/trip":
            self._handle_trip()
        elif self.path == "/api/transcribe":
            self._handle_transcribe()
        elif self.path == "/api/tts":
            self._handle_tts()
        else:
            self._send(404, b"not found", "text/plain")

    def _command_with_attachments(self, payload: dict) -> tuple[str, list]:
        command = _compose_command(str(payload.get("command", "")), payload.get("attachments") or [])
        history = payload.get("history") or []
        return command, history if isinstance(history, list) else []

    def _handle_stream(self) -> None:
        try:
            payload = self._read_json()
            command, history = self._command_with_attachments(payload)
            voice = bool(payload.get("voice"))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(obj: dict) -> None:
            check_cancelled()
            try:
                self.wfile.write(("data: " + json.dumps(obj, default=str) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                raise OperationCancelled("Client disconnected")

        # In voice mode, split the streamed reply into sentences and push each as a
        # `tts` event the moment it completes, so the browser starts speaking the
        # first sentence instead of waiting for the whole reply (low time-to-audio).
        chunker = SpeechChunker() if voice else None

        def on_token(token: str) -> None:
            emit({"type": "token", "text": token})
            if chunker is not None:
                for sentence in chunker.feed(token):
                    spoken = clean_for_speech(sentence)
                    if spoken:
                        emit({"type": "tts", "text": spoken})

        def reset_tokens():
            if chunker is not None:
                chunker.flush()
            emit({"type": "reset"})
        on_token.reset = reset_tokens

        # A risky action asks the user mid-turn: push the request down this stream so the
        # page can show it while the worker thread waits on the answer.
        def on_approval(request: dict) -> None:
            emit({"type": "approval", "request": request})

        _APPROVALS.add_listener(on_approval)
        agent_id = _orchestrator.control_room.start(command)
        try:
            result = asyncio.run(_orchestrator.handle(command, history=history, on_token=on_token))
            if chunker is not None:
                tail = clean_for_speech(chunker.flush() or "")
                if tail:
                    emit({"type": "tts", "text": tail})
            _orchestrator.control_room.finish(agent_id, result.message, ok=result.ok)
            emit({"type": "done", "ok": result.ok, "message": result.message, "data": _json_safe(result.data)})
        except OperationCancelled:
            _orchestrator.control_room.finish(agent_id, "Stopped by user", ok=False)
        except ApprovalDenied as exc:
            _orchestrator.control_room.finish(agent_id, str(exc), ok=False)
            emit({"type": "done", "ok": False, "message": f"Not approved — {exc}", "data": {}})
        except Exception as exc:  # pragma: no cover - defensive for the preview server.
            _orchestrator.control_room.finish(agent_id, str(exc), ok=False)
            emit({"type": "done", "ok": False, "message": f"Error: {exc}", "data": {}})
        finally:
            _APPROVALS.remove_listener(on_approval)

    @staticmethod
    @contextlib.contextmanager
    def _approval_bridge(emit: Callable[[dict], None]):
        """Push approval requests down an open SSE stream for as long as it lasts.

        Every streaming handler needs this, not just chat: without it the broker sees no
        listener and denies immediately, which meant `agent run` could not perform a
        single risky step even though the user was sitting there watching it.
        """
        def on_approval(request: dict) -> None:
            emit({"type": "approval", "request": request})

        _APPROVALS.add_listener(on_approval)
        try:
            yield
        finally:
            _APPROVALS.remove_listener(on_approval)

    def _handle_agent(self) -> None:
        """Run the autonomous agent, streaming each plan/act/observe step over SSE."""
        try:
            payload = self._read_json()
            goal = str(payload.get("goal", "")).strip()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        if not goal:
            self._json(400, {"ok": False, "message": "no goal"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(obj: dict) -> None:
            check_cancelled()
            try:
                self.wfile.write(("data: " + json.dumps(obj, default=str) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                raise OperationCancelled("Client disconnected")

        emit({"type": "start", "goal": goal})
        history = payload.get("history") or []  # validated by _read_json
        try:
            with self._approval_bridge(emit):
                result = asyncio.run(
                    _orchestrator.run_agent(
                        goal, on_step=lambda step: emit({"type": "step", "step": step.__dict__}), history=history
                    )
                )
            emit({"type": "done", "ok": result.ok, "message": result.message, "data": _json_safe(result.data)})
        except OperationCancelled:
            return
        except ApprovalDenied as exc:
            emit({"type": "done", "ok": False, "message": f"Not approved — {exc}", "data": {}})
        except Exception as exc:
            record_failure("api/agent", exc, goal=goal[:120])
            emit({"type": "done", "ok": False, "message": f"Error: {exc}", "data": {}})

    def _handle_upload(self) -> None:
        try:
            payload = self._read_json()
            name = Path(str(payload.get("name", "upload.bin"))).name or "upload.bin"
            data = str(payload.get("data", ""))
            if data.startswith("data:") and "," in data:
                data = data.split(",", 1)[1]
            raw = base64.b64decode(data, validate=True)
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "could not read upload"})
            return
        if len(raw) > MAX_UPLOAD_BYTES:
            self._json(413, {"ok": False, "message": "file too large (max 35MB)"})
            return
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(" .")[:180] or "upload.bin"
        dest = Path(tempfile.mkdtemp(prefix="upload_", dir=UPLOAD_DIR)) / name
        dest.write_bytes(raw)
        self._json(200, {"ok": True, "path": str(dest), "name": name, "size": len(raw)})

    def _handle_transcribe(self) -> None:
        """Speech-to-text for the native app's voice loop: accept a recorded audio
        clip and return the transcript via the local TranscribeTool. This replaces
        the browser Web Speech API so voice works in a non-browser (webview) window."""
        try:
            payload = self._read_json()
            data = str(payload.get("audio", ""))
            if data.startswith("data:") and "," in data:
                data = data.split(",", 1)[1]
            raw = base64.b64decode(data, validate=True)
            suffix = str(payload.get("ext", "webm")).lstrip(".").lower()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "could not read audio"})
            return
        if not raw:
            self._json(400, {"ok": False, "message": "empty audio"})
            return
        if len(raw) > MAX_UPLOAD_BYTES:
            self._json(413, {"ok": False, "message": "audio too large"})
            return
        if suffix not in {"webm", "ogg", "wav", "m4a", "mp4", "mp3"}:
            suffix = "webm"
        # Believe the bytes, not the label. The extension decides the engine - Riva takes
        # PCM WAV only, so `auto` skips it for anything else - and it arrives as a client
        # field with a silent default. A WAV posted without `ext` was written as .webm and
        # quietly transcribed by the slower local engine: measured, the same clip gave
        # "Kernoal" through Riva and "Cornule" through Whisper, with no sign anything had
        # been downgraded.
        if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
            suffix = "wav"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="voice_", suffix="." + suffix, dir=UPLOAD_DIR, delete=False) as handle:
            clip = Path(handle.name)
            handle.write(raw)
        try:
            result = _orchestrator.context.transcribe.transcribe_media(str(clip))
        finally:
            clip.unlink(missing_ok=True)
        text = str(result.data.get("text", "")).strip() if result.ok else ""
        self._json(200, {"ok": result.ok and bool(text), "text": text, "message": result.message})

    def _handle_tts(self) -> None:
        """Text-to-speech for the native app's voice loop: render a sentence to WAV
        audio server-side (offline engine) so the page can play it without the
        browser's speechSynthesis, which is unavailable in a webview window."""
        try:
            payload = self._read_json()
            text = str(payload.get("text", ""))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        wav = _render_tts(text)
        if not wav:
            self._json(503, {"ok": False, "message": "Text-to-speech needs: pip install pyttsx3"})
            return
        self._send(200, wav, "audio/wav")

    def _handle_copilot(self) -> None:
        """Tailor a resume to a job description (ATS score + grounded bullets / cover
        letter / interview pack). Ported Job-CoPilot logic on this app's LLM provider."""
        try:
            payload = self._read_json()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        result = _orchestrator.tailor_application(
            str(payload.get("resume_text", "")), str(payload.get("job_text", "")),
            company=str(payload.get("company", "")), role=str(payload.get("role", "")),
        )
        self._json(200, {"ok": result.ok, "message": result.message, **_json_safe(result.data or {})})

    def _serve_document(self) -> None:
        """Hand back a written document as a download."""
        from urllib.parse import parse_qs, urlparse

        name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
        target = _document_path(name)
        if target is None:
            self._send(404, b"no such document", "text/plain")
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", DOCUMENT_TYPES[target.suffix.lower()])
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_image(self) -> None:
        """Serve a generated picture so the chat can embed it (CSP allows img-src 'self')."""
        from urllib.parse import parse_qs, urlparse

        name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
        target = _image_path(name)
        if target is None:
            self._send(404, b"no such image", "text/plain")
            return
        kind = IMAGE_TYPES[target.suffix.lower()]
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def _serve_resume_pdf(self) -> None:
        """Stream a job's tailored resume PDF as a download."""
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(self.path).query)
        try:
            job_id = int((qs.get("id", ["0"])[0]).lstrip("#") or 0)
        except ValueError:
            self._send(400, b"bad id", "text/plain")
            return
        job = _orchestrator.context.jobs.get(job_id)
        pdf = (job or {}).get("tailored_pdf")
        if not pdf or not Path(pdf).exists():
            self._send(404, b"No PDF yet for this job. Tailor it first.", "text/plain")
            return
        data = Path(pdf).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'attachment; filename="resume_{job_id}.pdf"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_pipeline(self) -> None:
        """Drive the live pipeline page: pull Jobright leads, set the base resume (pasted or
        from a file), tailor a job on demand, or change a job's stage. Each action returns the
        refreshed pipeline snapshot so the board re-renders from one round-trip."""
        try:
            payload = self._read_json()
            action = str(payload.get("action", "")).strip().lower()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        ok, message = True, ""
        try:
            if action == "pull":
                result = asyncio.run(_orchestrator.handle("jobright pull"))
                ok, message = result.ok, result.message
            elif action == "tailor":
                job_id = int(str(payload.get("id", "0")).lstrip("#") or 0)
                result = _orchestrator.tailor_job(job_id)
                if result.ok:
                    pdf = asyncio.run(_orchestrator.render_job_pdf(job_id))
                    ok = True
                    message = "Tailored and exported to PDF." if pdf.ok else f"Tailored (PDF export failed: {pdf.message})"
                else:
                    ok, message = False, result.message
            elif action == "profile":
                profile = payload.get("profile")
                if not isinstance(profile, dict) or any(not isinstance(v, str) for v in profile.values()):
                    raise ValueError("Profile fields must be text")
                allowed = {"contact_links", "cert_links", "github_user"}
                _orchestrator.context.jobs.set_resume_profile({k: v.strip()[:2000] for k, v in profile.items() if k in allowed})
                message = "Resume profile saved. Re-tailor existing resumes to apply it."
            elif action == "resume":
                result = _orchestrator.set_resume_text(str(payload.get("text", "")), source="pasted")
                ok, message = result.ok, result.message
            elif action == "resume_file":
                result = _orchestrator.set_resume_from_file(str(payload.get("path", "")))
                ok, message = result.ok, result.message
            elif action == "clear_leads":
                removed = _orchestrator.context.jobs.clear_leads()
                ok, message = True, f"Cleared {removed} lead(s)."
            elif action == "stage":
                updated = _orchestrator.context.jobs.update(
                    int(str(payload.get("id", "0")).lstrip("#") or 0), stage=str(payload.get("stage", "")))
                ok = updated is not None
                message = "Updated." if ok else "No such job."
            elif action == "remove":
                ok = _orchestrator.context.jobs.remove(int(str(payload.get("id", "0")).lstrip("#") or 0))
                message = "Removed." if ok else "No such job."
            else:
                self._json(400, {"ok": False, "message": "Unknown action."})
                return
        except ApprovalDenied as exc:
            ok, message = False, f"Not approved — {exc}"
        except (ValueError, TypeError) as exc:
            ok, message = False, str(exc)
        snap = _pipeline_snapshot()
        snap["ok"], snap["message"] = ok, message
        self._json(200, snap)

    def _handle_jobs(self) -> None:
        """Add / update-stage / edit / remove a tracked job application, then return the
        refreshed pipeline + stats so the dashboard re-renders from one round-trip.
        Local JSON only (not approval gated), like reminders/tasks."""
        try:
            payload = self._read_json()
            action = str(payload.get("action", "")).strip().lower()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        jobs = _orchestrator.context.jobs
        ok, message = True, ""
        try:
            if action == "add":
                job = jobs.add(
                    str(payload.get("company", "")), role=str(payload.get("role", "")),
                    stage=str(payload.get("stage", "applied")), recruiter=str(payload.get("recruiter", "")),
                    next_date=str(payload.get("next_date", "")), notes=str(payload.get("notes", "")),
                )
                message = f"Added {job['company']}."
            elif action == "update":
                job_id = int(str(payload.get("id", "0")).lstrip("#") or 0)
                fields = {k: payload[k] for k in ("company", "role", "stage", "recruiter", "next_date", "notes") if k in payload}
                ok = jobs.update(job_id, **fields) is not None
                message = "Updated." if ok else f"No job #{job_id}."
            elif action == "remove":
                job_id = int(str(payload.get("id", "0")).lstrip("#") or 0)
                ok = jobs.remove(job_id)
                message = "Removed." if ok else f"No job #{job_id}."
            else:
                self._json(400, {"ok": False, "message": "Unknown action."})
                return
        except (ValueError, TypeError) as exc:
            ok, message = False, str(exc)
        snap = _jobs_snapshot()
        self._json(200, {"ok": ok, "message": message, "jobs": snap["jobs"], "stats": snap["stats"]})

    def _handle_schedule(self) -> None:
        """Add / remove / toggle a scheduled job, then return the refreshed list.

        Mutations route through the same orchestrator commands the CLI uses, so the
        parsing/validation and approval semantics stay identical; the response always
        carries the current job list so the panel re-renders from one round-trip.
        """
        try:
            payload = self._read_json()
            action = str(payload.get("action", "")).strip().lower()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return

        message = ""
        ok = True
        if action == "add":
            kind = "agent" if str(payload.get("kind", "command")).strip().lower() == "agent" else "command"
            when = str(payload.get("when", "")).strip()
            spec = str(payload.get("spec", "")).strip()
            if not when or not spec:
                self._json(400, {"ok": False, "message": "Provide both a schedule and a command/goal.", "jobs": []})
                return
            prefix = "schedule agent " if kind == "agent" else "schedule "
            result = asyncio.run(_orchestrator.handle(f"{prefix}{when} :: {spec}"))
            ok, message = result.ok, result.message
        elif action == "remove":
            try:
                job_id = int(str(payload.get("id", "")).lstrip("#"))
            except ValueError:
                self._json(400, {"ok": False, "message": "Invalid job id.", "jobs": []})
                return
            result = asyncio.run(_orchestrator.handle(f"schedule remove {job_id}"))
            ok, message = result.ok, result.message
        elif action in {"enable", "disable"}:
            try:
                job_id = int(str(payload.get("id", "")).lstrip("#"))
            except ValueError:
                self._json(400, {"ok": False, "message": "Invalid job id.", "jobs": []})
                return
            changed = _orchestrator.context.scheduler.set_enabled(job_id, action == "enable")
            ok = changed
            message = f"Job #{job_id} {action}d." if changed else f"No scheduled job #{job_id}."
        else:
            self._json(400, {"ok": False, "message": "Unknown action.", "jobs": []})
            return

        snapshot = _schedule_snapshot()
        self._json(200, {"ok": ok, "message": message, "jobs": snapshot["jobs"]})

    def _handle_map(self) -> None:
        """Resolve a place or 'A to B' route to map coordinates for the Map panel.

        Routes through the same 'map …' orchestrator command the CLI uses, so the
        geocoding/approval semantics stay identical; the structured data (OSM embed
        URL, bbox, points, directions link) is folded into the JSON response.
        """
        try:
            payload = self._read_json()
            query = str(payload.get("query", "")).strip()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        if not query:
            self._json(200, {"ok": False, "message": "Enter a place, or 'origin to destination'."})
            return
        try:
            result = asyncio.run(_orchestrator.handle(f"map {query}"))
        except ApprovalDenied:
            self._json(200, {"ok": False, "message": "Map lookup was blocked."})
            return
        self._json(200, {"ok": result.ok, "message": result.message, **_json_safe(result.data or {})})

    def _handle_trip(self) -> None:
        """Multi-stop trip planner for the Trip panel: chains driving legs through the
        given stops (routed via the `trip …` orchestrator command) and returns the
        per-leg breakdown, totals, route geometry, bbox, and a directions link."""
        try:
            payload = self._read_json()
            raw_stops = payload.get("stops") or []
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        stops = [str(s).strip() for s in raw_stops if str(s).strip()]
        if len(stops) < 2:
            self._json(200, {"ok": False, "message": "Add at least two stops."})
            return
        try:
            result = asyncio.run(_orchestrator.handle("trip " + " | ".join(stops)))
        except ApprovalDenied:
            self._json(200, {"ok": False, "message": "Trip lookup was blocked."})
            return
        self._json(200, {"ok": result.ok, "message": result.message, **_json_safe(result.data or {})})

    def _handle_notes(self) -> None:
        """Vault browser backend: read a note (with backlinks/outlinks) or search.
        Reads local Markdown only — not approval gated, like the rest of the vault."""
        try:
            payload = self._read_json()
            action = str(payload.get("action", "read")).strip().lower()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        vault = _orchestrator.context.obsidian
        if action == "search":
            result = vault.search(str(payload.get("query", "")))
        else:
            result = vault.note_detail(str(payload.get("name", "")))
        self._json(200, {"ok": result.ok, "message": result.message, **_json_safe(result.data or {})})

    def _handle_window(self) -> None:
        """Adaptive-HUD window effects for the desktop app: translucency (opacity)
        and an always-on-top pin. A no-op (native=False) in a normal browser so we
        never make the user's whole browser see-through."""
        try:
            payload = self._read_json()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        if not _DESKTOP_MODE:
            self._json(200, {"ok": True, "native": False, "applied": {"opacity": None, "on_top": None}})
            return
        opacity = payload.get("opacity")
        on_top = payload.get("on_top")
        applied = apply_window_effects(
            opacity=float(opacity) if isinstance(opacity, (int, float)) else None,
            on_top=bool(on_top) if on_top is not None else None,
        )
        self._json(200, {"ok": True, "native": True, "applied": applied})

    def _handle_approve(self) -> None:
        """Answer one pending approval. The id must match exactly, and an id is single-use,
        so approving a download can never authorise the shell command behind it."""
        try:
            payload = self._read_json()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        request_id = payload.get("id", "")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
            self._json(400, {"ok": False, "message": "Invalid approval id."})
            return
        approved = payload.get("approved") is True
        if _APPROVALS.resolve(request_id, approved):
            self._json(200, {"ok": True, "approved": approved})
        else:
            # Already answered, timed out, or never existed — all the same to the caller.
            self._json(409, {"ok": False, "message": "That approval is no longer pending."})

    def _handle_command(self) -> None:
        try:
            payload = self._read_json()
            command = _compose_command(str(payload.get("command", "")), payload.get("attachments") or [])
            history = payload.get("history") or []
            if not isinstance(history, list):
                history = []
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return

        # Light up the matching specialist in the control room while this runs,
        # so the agents panel reflects single commands, not just multi subtasks.
        agent_id = _orchestrator.control_room.start(command)
        try:
            result = asyncio.run(_orchestrator.handle(command, history=history))
            _orchestrator.control_room.finish(agent_id, result.message, ok=result.ok)
            body = {"ok": result.ok, "message": result.message, "data": _json_safe(result.data)}
        except ApprovalDenied as exc:
            _orchestrator.control_room.finish(agent_id, str(exc), ok=False)
            body = {"ok": False, "message": f"Not approved — {exc}", "data": {}}
        except Exception as exc:  # pragma: no cover - defensive for the preview server.
            _orchestrator.control_room.finish(agent_id, str(exc), ok=False)
            body = {"ok": False, "message": f"Error: {exc}", "data": {}}
        self._json(200, body)


def main() -> None:
    server = _Server((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    _warmup()
    _keep_warm()
    _schedule_ticker()
    print(f"J.A.R.V.I.S chat running at {url}")
    print("Guarded mode: high-risk actions ask for approval; read/search/research run freely. Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


def _find_chromium() -> str | None:
    for name in ("msedge", "chrome", "chromium", "chromium-browser", "brave"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    return next((path for path in candidates if os.path.exists(path)), None)


def _launch_app_window(url: str) -> subprocess.Popen | None:
    exe = _find_chromium()
    if not exe:
        return None
    profile = Path(tempfile.gettempdir()) / "laptop_agent_deck_profile"
    args = [
        exe,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1280,860",
    ]
    return subprocess.Popen(args)


def _launch_webview(url: str) -> bool:
    try:
        import webview  # type: ignore
    except ImportError:
        return False
    # ?app=1 tells the page it is in a non-browser window, so voice uses the
    # server-side speech path (record -> /api/transcribe, play /api/tts) instead of
    # the Web Speech API, which is unavailable inside a webview.
    sep = "&" if "?" in url else "?"
    webview.create_window("J.A.R.V.I.S", f"{url}{sep}app=1", width=1280, height=860, background_color="#08090d")
    profile = _CONFIG.data_dir / "webview"
    profile.mkdir(parents=True, exist_ok=True)
    webview.start(private_mode=False, storage_path=str(profile))
    return True


def run_desktop() -> None:
    """Serve the chat and open it in a dedicated desktop window (no browser chrome)."""
    global _DESKTOP_MODE
    _DESKTOP_MODE = True  # enable real HUD window effects (opacity / always-on-top)
    server = _Server((HOST, PORT), Handler)
    port = server.server_address[1]
    url = f"http://{HOST}:{port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _warmup()
    _keep_warm()
    _schedule_ticker()
    # Warm the speech model in the background so the first voice turn isn't slow
    # (no-op if no STT engine is installed).
    from laptop_agent.tools.transcribe import warm_stt

    threading.Thread(target=warm_stt, daemon=True).start()
    print(f"J.A.R.V.I.S chat serving at {url}")

    # Prefer a true native window (pywebview): no Edge, its own taskbar entry. Voice
    # works because speech is handled server-side (Whisper STT + offline TTS), not via
    # the browser's Web Speech API. This is the real "downloadable app" experience.
    if _launch_webview(url):
        print("Opened as a native app window.")
        server.shutdown()
        return

    # Fallback when pywebview isn't installed: a frameless Chrome/Edge --app window.
    # It looks like an app and keeps the (browser) Web Speech API for voice.
    process = _launch_app_window(url)
    if process is not None:
        print("pywebview not installed — opened a frameless Chrome/Edge app window instead.")
        try:
            process.wait()          # quit the app when the window is closed
        except KeyboardInterrupt:
            pass
        finally:
            if process.poll() is None:
                process.terminate()
            server.shutdown()
        return

    print("No app window backend found; opening in the default browser instead.")
    webbrowser.open(url)
    print("Running. Press Ctrl+C here to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    import sys

    # `python -m laptop_agent.webui --desktop` opens a frameless desktop app window
    # (same as the `laptop-agent-deck` command); without it, serves a browser tab.
    if any(flag in sys.argv[1:] for flag in ("--desktop", "-d", "--app")):
        run_desktop()
    else:
        main()

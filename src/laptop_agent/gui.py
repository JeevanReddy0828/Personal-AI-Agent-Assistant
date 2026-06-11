from __future__ import annotations

import asyncio
import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from laptop_agent.app import build_orchestrator
from laptop_agent.safety import ApprovalDenied, ApprovalRequest
from laptop_agent.voice import VoiceIO


class LaptopAgentGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Laptop Agent")
        self.root.geometry("920x640")
        self.voice = VoiceIO()
        self.last_response = ""
        self.orchestrator = build_orchestrator(self._ask_approval)
        self._build_ui()
        self._append("Agent", "Ready. Type 'help' for commands.")

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.transcript = scrolledtext.ScrolledText(self.root, wrap=tk.WORD, state=tk.DISABLED, font=("Segoe UI", 10))
        self.transcript.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 8))

        bar = ttk.Frame(self.root)
        bar.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        bar.columnconfigure(0, weight=1)

        self.input_var = tk.StringVar()
        self.input_entry = ttk.Entry(bar, textvariable=self.input_var)
        self.input_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.input_entry.bind("<Return>", lambda _event: self._submit())

        ttk.Button(bar, text="Send", command=self._submit).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(bar, text="Listen", command=self._listen).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(bar, text="Speak", command=self._speak_last).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(bar, text="Audit", command=lambda: self._run_command("audit")).grid(row=0, column=4, padx=(0, 6))
        ttk.Button(bar, text="Help", command=lambda: self._run_command("help")).grid(row=0, column=5)

    def _submit(self) -> None:
        text = self.input_var.get().strip()
        if not text:
            return
        self.input_var.set("")
        self._run_command(text)

    def _run_command(self, text: str) -> None:
        self._append("You", text)
        thread = threading.Thread(target=self._worker_handle, args=(text,), daemon=True)
        thread.start()

    def _worker_handle(self, text: str) -> None:
        try:
            result = asyncio.run(self.orchestrator.handle(text))
            message = result.message
            if result.data:
                message += "\n" + json.dumps(_json_safe(result.data), indent=2, default=str)
        except ApprovalDenied as exc:
            message = f"Denied: {exc}"
        except Exception as exc:  # pragma: no cover - GUI safeguard.
            message = f"Error: {exc}"
        self.root.after(0, lambda: self._append("Agent", message))

    def _listen(self) -> None:
        self._append("Agent", "Listening once...")
        thread = threading.Thread(target=self._worker_listen, daemon=True)
        thread.start()

    def _worker_listen(self) -> None:
        result = self.voice.listen_once()
        if result.ok:
            text = str(result.data["text"])
            self.root.after(0, lambda: self.input_var.set(text))
            self.root.after(0, lambda: self._append("Agent", f"Heard: {text}"))
        else:
            self.root.after(0, lambda: self._append("Agent", result.message))

    def _speak_last(self) -> None:
        if not self.last_response:
            self._append("Agent", "No response to speak yet.")
            return
        thread = threading.Thread(target=self._worker_speak, args=(self.last_response,), daemon=True)
        thread.start()

    def _worker_speak(self, text: str) -> None:
        result = self.voice.speak(text)
        if not result.ok:
            self.root.after(0, lambda: self._append("Agent", result.message))

    def _append(self, speaker: str, text: str) -> None:
        if speaker == "Agent":
            self.last_response = text
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, f"{speaker}: {text}\n\n")
        self.transcript.see(tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def _ask_approval(self, request: ApprovalRequest) -> bool:
        response: queue.Queue[bool] = queue.Queue(maxsize=1)

        def show_dialog() -> None:
            details = [
                f"Action: {request.action}",
                f"Risk: {request.risk.value}",
                f"Reason: {request.reason}",
            ]
            if request.preview:
                details.extend(["", "Preview:", request.preview])
            approved = messagebox.askyesno("Approval Required", "\n".join(details), parent=self.root)
            response.put(approved)

        self.root.after(0, show_dialog)
        return response.get()


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dict__"):
        return _json_safe(value.__dict__)
    return value


def main() -> None:
    root = tk.Tk()
    LaptopAgentGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()

"""J.A.R.V.I.S-style desktop dashboard for the laptop agent.

A modern HUD-themed Tkinter window (pure standard library, no extra deps):
an animated arc-reactor core, a system-status sidebar, a live activity log,
and a glowing command bar. It drives the same AgentOrchestrator as the CLI,
so every risky action still routes through the approval gate.

Run:  python -m laptop_agent.dashboard
"""

from __future__ import annotations

import asyncio
import json
import math
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

from laptop_agent.app import build_orchestrator
from laptop_agent.safety import ApprovalDenied, ApprovalRequest
from laptop_agent.voice import VoiceIO


THEME = {
    "bg": "#060a12",
    "panel": "#0a1120",
    "panel_alt": "#0d1626",
    "border": "#15324a",
    "grid": "#0f2336",
    "cyan": "#3fe1ff",
    "cyan_dim": "#1d6f86",
    "gold": "#ffb454",
    "text": "#d6eef7",
    "muted": "#5f7d8c",
    "ok": "#3ddc97",
    "fail": "#ff5c7a",
    "you": "#ffd479",
}


class Dashboard:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.voice = VoiceIO()
        self.last_response = ""
        self.busy = False
        self._phase = 0.0
        self._cmd_count = 0
        self.orchestrator = build_orchestrator(self._ask_approval)

        self._init_fonts()
        self._configure_window()
        self._build_header()
        self._build_body()
        self._build_command_bar()

        self._append("SYSTEM", "All systems online. Type a command or press a quick action.", THEME["cyan"])
        self._log_activity("boot", "dashboard online", THEME["ok"])
        self._animate()
        self.entry.focus_set()

    # ----------------------------------------------------------------- setup
    def _init_fonts(self) -> None:
        self.f_title = tkfont.Font(family="Consolas", size=20, weight="bold")
        self.f_sub = tkfont.Font(family="Consolas", size=9)
        self.f_label = tkfont.Font(family="Consolas", size=9, weight="bold")
        self.f_body = tkfont.Font(family="Segoe UI", size=11)
        self.f_mono = tkfont.Font(family="Consolas", size=10)
        self.f_chip = tkfont.Font(family="Consolas", size=8, weight="bold")

    def _configure_window(self) -> None:
        self.root.title("J.A.R.V.I.S  //  Laptop Agent")
        self.root.geometry("1180x740")
        self.root.minsize(980, 620)
        self.root.configure(bg=THEME["bg"])
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

    # ---------------------------------------------------------------- header
    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=THEME["bg"], height=92)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        header.grid_propagate(False)
        header.columnconfigure(1, weight=1)

        self.reactor = tk.Canvas(header, width=74, height=74, bg=THEME["bg"], highlightthickness=0)
        self.reactor.grid(row=0, column=0, rowspan=2, padx=(4, 14))

        tk.Label(header, text="J.A.R.V.I.S", font=self.f_title, fg=THEME["cyan"], bg=THEME["bg"]).grid(
            row=0, column=1, sticky="sw"
        )
        self.status_line = tk.Label(
            header,
            text="Just A Rather Very Intelligent System  ·  status: ONLINE",
            font=self.f_sub,
            fg=THEME["muted"],
            bg=THEME["bg"],
        )
        self.status_line.grid(row=1, column=1, sticky="nw")

        chips = tk.Frame(header, bg=THEME["bg"])
        chips.grid(row=0, column=2, rowspan=2, sticky="e")
        self._chip(chips, f"PLANNER · {self._planner_name()}")
        self._chip(chips, "APPROVALS · ON")
        self._chip(chips, "LOCAL · OFFLINE")

        tk.Frame(self.root, bg=THEME["border"], height=1).grid(row=0, column=0, sticky="sew", padx=16)

    def _chip(self, parent: tk.Frame, text: str) -> None:
        chip = tk.Label(
            parent,
            text=text,
            font=self.f_chip,
            fg=THEME["cyan"],
            bg=THEME["panel"],
            padx=10,
            pady=5,
        )
        chip.pack(side=tk.LEFT, padx=4)

    # ------------------------------------------------------------------ body
    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=THEME["bg"])
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        self._build_sidebar(body)
        self._build_transcript(body)
        self._build_activity(body)

    def _panel(self, parent: tk.Frame, title: str, width: int | None = None) -> tk.Frame:
        outer = tk.Frame(parent, bg=THEME["border"])
        inner = tk.Frame(outer, bg=THEME["panel"])
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        head = tk.Frame(inner, bg=THEME["panel"])
        head.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(head, text="◤", font=self.f_label, fg=THEME["cyan"], bg=THEME["panel"]).pack(side=tk.LEFT)
        tk.Label(head, text="  " + title, font=self.f_label, fg=THEME["muted"], bg=THEME["panel"]).pack(side=tk.LEFT)
        if width:
            outer.configure(width=width)
            outer.pack_propagate(False)
        return inner

    def _build_sidebar(self, body: tk.Frame) -> None:
        panel = self._panel(body, "SYSTEM", width=232)
        panel.master.grid(row=0, column=0, sticky="ns", padx=(0, 12))

        stats = tk.Frame(panel, bg=THEME["panel"])
        stats.pack(fill=tk.X, padx=12, pady=(6, 8))
        capabilities = self._capability_count()
        for key, value, color in [
            ("AGENT", "READY", THEME["ok"]),
            ("PLANNER", self._planner_name(), THEME["cyan"]),
            ("CAPABILITIES", str(capabilities), THEME["cyan"]),
            ("COMMANDS RUN", "0", THEME["gold"]),
        ]:
            row = tk.Frame(stats, bg=THEME["panel"])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=key, font=self.f_sub, fg=THEME["muted"], bg=THEME["panel"]).pack(side=tk.LEFT)
            lbl = tk.Label(row, text=value, font=self.f_label, fg=color, bg=THEME["panel"])
            lbl.pack(side=tk.RIGHT)
            if key == "COMMANDS RUN":
                self.cmd_value = lbl

        tk.Frame(panel, bg=THEME["grid"], height=1).pack(fill=tk.X, padx=12, pady=6)
        tk.Label(panel, text="  QUICK ACTIONS", font=self.f_sub, fg=THEME["muted"], bg=THEME["panel"]).pack(
            anchor="w", padx=10, pady=(2, 6)
        )

        actions = [
            ("⌗  Help", "help"),
            ("◎  Scan files", "scan files ."),
            ("⌸  Tasks", "tasks"),
            ("🜲  Memory", "memory"),
            ("⟲  Audit", "audit"),
            ("◧  Read screen", "read screen"),
        ]
        for label, command in actions:
            self._action_button(panel, label, command)

    def _action_button(self, parent: tk.Frame, label: str, command: str) -> None:
        btn = tk.Label(
            parent,
            text=label,
            font=self.f_mono,
            fg=THEME["text"],
            bg=THEME["panel_alt"],
            anchor="w",
            padx=12,
            pady=8,
        )
        btn.pack(fill=tk.X, padx=10, pady=3)
        btn.bind("<Enter>", lambda _e: btn.configure(bg=THEME["border"], fg=THEME["cyan"]))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=THEME["panel_alt"], fg=THEME["text"]))
        btn.bind("<Button-1>", lambda _e: self._run_command(command))

    def _build_transcript(self, body: tk.Frame) -> None:
        panel = self._panel(body, "CONVERSATION")
        panel.master.grid(row=0, column=1, sticky="nsew")

        wrap = tk.Frame(panel, bg=THEME["panel"])
        wrap.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))
        self.transcript = tk.Text(
            wrap,
            wrap=tk.WORD,
            bg=THEME["bg"],
            fg=THEME["text"],
            insertbackground=THEME["cyan"],
            relief=tk.FLAT,
            font=self.f_body,
            padx=14,
            pady=12,
            state=tk.DISABLED,
            spacing1=2,
            spacing3=8,
        )
        scroll = tk.Scrollbar(wrap, command=self.transcript.yview, bg=THEME["panel"], troughcolor=THEME["bg"], width=10)
        self.transcript.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.transcript.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.transcript.tag_configure("speaker", font=self.f_label, foreground=THEME["muted"], spacing1=8)
        self.transcript.tag_configure("you", foreground=THEME["you"])
        self.transcript.tag_configure("agent", foreground=THEME["text"])
        self.transcript.tag_configure("data", foreground=THEME["cyan_dim"], font=self.f_mono)

    def _build_activity(self, body: tk.Frame) -> None:
        panel = self._panel(body, "ACTIVITY LOG", width=288)
        panel.master.grid(row=0, column=2, sticky="ns", padx=(12, 0))
        self.activity = tk.Text(
            panel,
            wrap=tk.WORD,
            bg=THEME["bg"],
            fg=THEME["muted"],
            relief=tk.FLAT,
            font=self.f_mono,
            padx=12,
            pady=10,
            state=tk.DISABLED,
            width=30,
        )
        self.activity.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))
        self.activity.tag_configure("ok", foreground=THEME["ok"])
        self.activity.tag_configure("fail", foreground=THEME["fail"])
        self.activity.tag_configure("info", foreground=THEME["cyan"])
        self.activity.tag_configure("dim", foreground=THEME["muted"])

    # ----------------------------------------------------------- command bar
    def _build_command_bar(self) -> None:
        bar = tk.Frame(self.root, bg=THEME["bg"])
        bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 16))
        bar.columnconfigure(0, weight=1)

        field = tk.Frame(bar, bg=THEME["cyan_dim"])
        field.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        inner = tk.Frame(field, bg=THEME["panel"])
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        tk.Label(inner, text="❯", font=self.f_label, fg=THEME["cyan"], bg=THEME["panel"]).pack(side=tk.LEFT, padx=(12, 4))
        self.input_var = tk.StringVar()
        self.entry = tk.Entry(
            inner,
            textvariable=self.input_var,
            font=self.f_mono,
            bg=THEME["panel"],
            fg=THEME["text"],
            insertbackground=THEME["cyan"],
            relief=tk.FLAT,
        )
        self.entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, ipady=10, padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self._submit())

        self._bar_button(bar, "SEND", self._submit, primary=True).grid(row=0, column=1, padx=3)
        self._bar_button(bar, "LISTEN", self._listen).grid(row=0, column=2, padx=3)
        self._bar_button(bar, "SPEAK", self._speak_last).grid(row=0, column=3, padx=3)

    def _bar_button(self, parent: tk.Frame, text: str, command, primary: bool = False) -> tk.Label:
        fg = THEME["bg"] if primary else THEME["cyan"]
        bg = THEME["cyan"] if primary else THEME["panel"]
        hover = THEME["gold"] if primary else THEME["border"]
        btn = tk.Label(parent, text=text, font=self.f_label, fg=fg, bg=bg, padx=18, pady=11)
        btn.bind("<Enter>", lambda _e: btn.configure(bg=hover))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=bg))
        btn.bind("<Button-1>", lambda _e: command())
        return btn

    # ------------------------------------------------------------- animation
    def _animate(self) -> None:
        self._phase += 0.09
        self._draw_reactor(self._phase)
        if self.busy:
            dots = "▰" * (int(self._phase * 3) % 4 + 1)
            self.status_line.configure(text=f"processing {dots}", fg=THEME["gold"])
        self.root.after(45, self._animate)

    def _draw_reactor(self, phase: float) -> None:
        c = self.reactor
        c.delete("all")
        cx, cy = 37, 37
        pulse = (math.sin(phase) + 1) / 2  # 0..1
        accent = THEME["gold"] if self.busy else THEME["cyan"]

        # outer rotating ticks
        for i in range(12):
            ang = phase * 0.6 + i * (math.pi / 6)
            r1, r2 = 30, 35
            x1, y1 = cx + r1 * math.cos(ang), cy + r1 * math.sin(ang)
            x2, y2 = cx + r2 * math.cos(ang), cy + r2 * math.sin(ang)
            c.create_line(x1, y1, x2, y2, fill=THEME["cyan_dim"], width=2)

        # glowing concentric rings
        for radius, color in [(28, THEME["cyan_dim"]), (22, accent), (15, accent)]:
            c.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline=color, width=2)

        # pulsing core
        core = 5 + pulse * 5
        c.create_oval(cx - core, cy - core, cx + core, cy + core, fill=accent, outline="")
        c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=THEME["text"], outline="")

    # -------------------------------------------------------------- commands
    def _submit(self) -> None:
        text = self.input_var.get().strip()
        if not text:
            return
        self.input_var.set("")
        self._run_command(text)

    def _run_command(self, text: str) -> None:
        self._append("YOU", text, THEME["you"], tag="you")
        self.busy = True
        threading.Thread(target=self._worker_handle, args=(text,), daemon=True).start()

    def _worker_handle(self, text: str) -> None:
        ok = True
        try:
            result = asyncio.run(self.orchestrator.handle(text))
            ok = result.ok
            message = result.message
            data = json.dumps(_json_safe(result.data), indent=2, default=str) if result.data else ""
        except ApprovalDenied as exc:
            ok = False
            message = f"Denied: {exc}"
            data = ""
        except Exception as exc:  # pragma: no cover - GUI safeguard.
            ok = False
            message = f"Error: {exc}"
            data = ""
        self.root.after(0, lambda: self._finish_command(text, message, data, ok))

    def _finish_command(self, text: str, message: str, data: str, ok: bool) -> None:
        self.busy = False
        self.status_line.configure(
            text="Just A Rather Very Intelligent System  ·  status: ONLINE", fg=THEME["muted"]
        )
        self._append("J.A.R.V.I.S", message, THEME["text"], tag="agent")
        if data:
            self._append_data(data)
        self._cmd_count += 1
        self.cmd_value.configure(text=str(self._cmd_count))
        short = text if len(text) <= 26 else text[:25] + "…"
        self._log_activity(short, "ok" if ok else "failed", THEME["ok"] if ok else THEME["fail"])

    # -------------------------------------------------------------- voice io
    def _listen(self) -> None:
        self._append("SYSTEM", "Listening once…", THEME["cyan"])
        threading.Thread(target=self._worker_listen, daemon=True).start()

    def _worker_listen(self) -> None:
        result = self.voice.listen_once()
        if result.ok:
            text = str(result.data["text"])
            self.root.after(0, lambda: self.input_var.set(text))
            self.root.after(0, lambda: self._append("SYSTEM", f"Heard: {text}", THEME["cyan"]))
        else:
            self.root.after(0, lambda: self._append("SYSTEM", result.message, THEME["fail"]))

    def _speak_last(self) -> None:
        if not self.last_response:
            self._append("SYSTEM", "No response to speak yet.", THEME["muted"])
            return
        threading.Thread(target=self._worker_speak, args=(self.last_response,), daemon=True).start()

    def _worker_speak(self, text: str) -> None:
        result = self.voice.speak(text)
        if not result.ok:
            self.root.after(0, lambda: self._append("SYSTEM", result.message, THEME["fail"]))

    # ----------------------------------------------------------------- panes
    def _append(self, speaker: str, text: str, color: str, tag: str = "agent") -> None:
        if speaker == "J.A.R.V.I.S":
            self.last_response = text
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, f"{speaker}\n", "speaker")
        self.transcript.insert(tk.END, f"{text}\n", tag)
        self.transcript.see(tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def _append_data(self, data: str) -> None:
        clipped = data if len(data) <= 2600 else data[:2600] + "\n…(truncated)"
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, clipped + "\n", "data")
        self.transcript.see(tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def _log_activity(self, label: str, status: str, color: str) -> None:
        tag = "ok" if status == "ok" else "fail" if status == "failed" else "info"
        self.activity.configure(state=tk.NORMAL)
        marker = "✓" if status == "ok" else "✗" if status == "failed" else "▸"
        self.activity.insert("1.0", f"{marker} ", tag)
        self.activity.insert("1.0 + 2c", f"{label}\n", "dim")
        self.activity.configure(state=tk.DISABLED)

    # -------------------------------------------------------------- approval
    def _ask_approval(self, request: ApprovalRequest) -> bool:
        response: queue.Queue[bool] = queue.Queue(maxsize=1)

        def show_dialog() -> None:
            details = [f"Action: {request.action}", f"Risk: {request.risk.value}", f"Reason: {request.reason}"]
            if request.preview:
                details.extend(["", "Preview:", request.preview])
            approved = messagebox.askyesno("⚠  Approval Required", "\n".join(details), parent=self.root)
            response.put(approved)

        self.root.after(0, show_dialog)
        return response.get()

    # ----------------------------------------------------------------- intel
    def _planner_name(self) -> str:
        provider = getattr(self.orchestrator.planner, "provider", None)
        name = type(provider).__name__ if provider else "None"
        return "OpenAI" if "OpenAI" in name else "Heuristic"

    def _capability_count(self) -> int:
        lines = self.orchestrator.help_text().splitlines()
        return sum(1 for line in lines if line.startswith("  "))


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
    Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()

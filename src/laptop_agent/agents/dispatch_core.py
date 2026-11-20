"""The command tables for the app itself: memory, jobs, automation, files, knowledge, notes, vision, tasks, research and utilities.

One part of `AgentOrchestrator` (agents/orchestrator.py), split out by area; it reads the
rest of the orchestrator through `self`."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from laptop_agent.autopilot import parse_autopilot_steps
from laptop_agent.planner.heuristic import fact_question
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.breakdown import breakdown_command
from laptop_agent.tools.diagnostics import diagnostics_command
from laptop_agent.tools.forecast import forecast_command
from laptop_agent.agents.common import _REMINDER_REMOVE, _query_and_root


class CoreDispatch:
    async def _dispatch_meta(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for help, memory, audit, the daily briefing."""
        if lowered in {"help", "/help"}:
            return ToolResult.success(self.help_text())

        if lowered.startswith("remember note "):
            return self.context.obsidian.append_memory(command[len("remember note ") :].strip())

        if lowered == "learn from chat":
            return self._learn().learn(history_turns, self.context.memory.get_profile())

        if lowered.startswith("remember "):
            return self._remember(command[len("remember ") :])

        # "forget knowledge" clears the document index and belongs to _dispatch_knowledge,
        # which runs after this one.
        if lowered.startswith("forget ") and lowered != "forget knowledge":
            return self._forget(command[len("forget ") :])

        if lowered in {"memory", "show memory"}:
            memory = self.context.memory.dump()
            return ToolResult.success(self._memory_text(memory), memory=memory)

        if lowered in {"audit", "show audit"}:
            return ToolResult.success("Recent audit events.", events=self.context.audit.tail())

        if lowered in {"briefing", "daily briefing", "status briefing", "morning briefing"}:
            return self._briefing()

        if lowered in {"briefing format", "briefing settings"}:
            return self._briefing_format()

        if lowered in {"briefing reset", "briefing default"}:
            return self._briefing_reset()

        if lowered.startswith("briefing set "):
            return self._briefing_change("set", command[len("briefing set ") :])

        if lowered.startswith("briefing add "):
            return self._briefing_change("add", command[len("briefing add ") :])

        if lowered.startswith("briefing remove "):
            return self._briefing_change("remove", command[len("briefing remove ") :])
        return None

    async def _dispatch_jobs(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for the job tracker, Jobright and resume tailoring."""
        if lowered in {"jobs", "job list", "list jobs", "job tracker"}:
            return self._jobs_list()

        if lowered in {"jobright", "jobright pull", "pull jobs", "pull jobright", "jobright sync"}:
            return await self._jobright_pull()

        if lowered.startswith("tailor job "):
            raw = command[len("tailor job ") :].strip().lstrip("#")
            return self.tailor_job(int(raw)) if raw.isdigit() else ToolResult.failure("Use: tailor job <id>")

        if lowered.startswith("resume file "):
            return self.set_resume_from_file(command[len("resume file ") :].strip())

        if lowered.startswith("job add "):
            return self._job_add(command[len("job add ") :].strip())

        if lowered.startswith("job stage "):
            return self._job_stage(command[len("job stage ") :].strip())

        if lowered.startswith("job remove ") or lowered.startswith("job delete "):
            return self._job_remove(command.split(None, 2)[2].strip() if len(command.split(None, 2)) > 2 else "")
        return None

    async def _dispatch_automation(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for autopilot, reminders, schedules and the agent loop."""
        if lowered == "plugins":
            return self._plugins().listing()
        if lowered.startswith("plugin "):
            parts = command[len("plugin ") :].strip().split(None, 1)  # a newline or tab ends the name too
            return self._plugins().run(parts[0] if parts else "", parts[1].strip() if len(parts) > 1 else "")

        if lowered in {"autopilot status", "autonomous status"}:
            return self._autopilot_status()

        if lowered.startswith("autopilot workflow "):
            return await self._run_autopilot(
                goal="custom workflow",
                commands=parse_autopilot_steps(command[len("autopilot workflow ") :].strip()),
            )

        if lowered.startswith("autopilot "):
            goal = command[len("autopilot ") :].strip()
            return await self._run_autopilot(goal=goal, commands=self.autopilot_planner.plan(goal))

        if lowered in {"reminders", "reminders list", "show reminders"}:
            return self._reminders_list()

        if lowered.startswith("reminders on "):
            return self._reminders_list(command[len("reminders on ") :].strip())

        if lowered in {"reminders due", "due reminders", "show due reminders"}:
            return self._reminders_due()

        if lowered in {"reminders next", "reminders next alarm", "reminders next timer"}:
            return self._next_reminder(lowered.split()[-1] if lowered.endswith(("alarm", "timer")) else "")

        if lowered.startswith("reminder add "):
            return self._reminder_add(command[len("reminder add ") :].strip())

        if lowered.startswith("remind me "):
            # "remind me of my wife's birthday" asks for a fact; it names no time to set.
            asked = fact_question(command)
            recalled = self._recall_fact(*asked) if asked else None
            if recalled is not None:
                return recalled
            return self._reminder_add(command[len("remind me ") :].strip())

        if lowered == "reminder done" or lowered.startswith("reminder done "):
            return self._reminder_done(command[len("reminder done") :].strip())

        if lowered.startswith("reminder move "):
            return self._reminder_move(command[len("reminder move ") :].strip())

        if _REMINDER_REMOVE.match(lowered):
            return self._reminder_remove(command.split(" ", 2)[2] if len(command.split(" ", 2)) > 2 else "")

        if lowered == "reminder stop" or lowered.startswith("reminder stop "):
            return self._reminder_stop(command[len("reminder stop") :].strip())

        if lowered == "reminder snooze" or lowered.startswith("reminder snooze "):
            return self._reminder_snooze(command[len("reminder snooze") :].strip())

        if lowered == "timers" or lowered.startswith("timers "):
            return self._timers(command[len("timers") :].strip())

        if lowered == "alarms":
            return self._alarms()

        if lowered == "timer" or lowered.startswith("timer "):
            return self._timer(command[len("timer ") :])

        if lowered == "alarm" or lowered.startswith("alarm "):
            return self._alarm(command[len("alarm ") :])

        if lowered in {"schedule", "schedule list", "schedules", "show schedule"}:
            return self._schedule_list()

        if lowered in {"schedule run due", "run due schedules", "schedule tick"}:
            return await self.run_due_schedules()

        if lowered.startswith("schedule remove "):
            return self._schedule_remove(command[len("schedule remove ") :].strip())

        if lowered.startswith("schedule agent "):
            return self._schedule_add("agent", command[len("schedule agent ") :].strip())

        if lowered.startswith("schedule "):
            return self._schedule_add("command", command[len("schedule ") :].strip())

        if lowered in {"agents off", "agents on"}:
            return self._agents_switch(lowered == "agents off")

        if lowered in {"agent runs", "agent history", "autonomous runs"}:
            return self._agent_runs()

        if lowered in {"agent last", "agent status", "last agent run"}:
            return self._agent_last()

        if lowered.startswith("agent run "):
            return await self._run_agent(command[len("agent run ") :].strip(), history=history_turns)

        if lowered.startswith("agent team "):
            return await self._run_agent(command[len("agent team ") :].strip(), history=history_turns, team=True)

        if lowered in {"agents", "agent control", "control room", "agent dashboard"}:
            return self._agent_control_room()

        if lowered.startswith("agent "):
            return self._agent_detail(command[len("agent ") :].strip())
        return None

    async def _dispatch_files(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for reading, scanning and indexing files."""
        if lowered == "undo file":
            return self._file_ops().undo()
        if lowered in {"undo file move", "undo file copy", "undo file rename"}:
            return self._file_ops().undo(lowered.rsplit(" ", 1)[1])
        if lowered.startswith(("file move ", "file copy ", "file rename ")):
            changed = self._file_change_parts(command)
            if changed is None:
                return ToolResult.failure("Say where it goes: file move <file> to <folder or path>.")
            verb, source, target = changed
            if verb == "rename":
                return self._file_ops().rename(source, target)
            return self._file_ops().move(source, target, copy=verb == "copy")
        if lowered.startswith("file delete "):
            return self._file_ops().delete(command[len("file delete ") :])
        if lowered.startswith("scan files "):
            root = command[len("scan files ") :].strip()
            by_size = re.search(r"(?:^|\s+)by\s+size$", root, re.IGNORECASE)
            root = root[: by_size.start()].strip() if by_size else root
            by_date = (
                None
                if by_size
                else re.search(
                    r"(?:^|\s+)(?:by\s+(?:date|newest)|newest\s+first)"
                    r"(?:\s+(?P<period>today|yesterday|this\s+week))?$",
                    root,
                    re.IGNORECASE,
                )
            )
            root = root[: by_date.start()].strip() if by_date else root
            if by_date:
                scanned = self.context.files.scan(root or ".", by_date=True)
                period = " ".join((by_date.group("period") or "").lower().split())
                if not period or not scanned.ok:
                    return scanned
                # Only what changed in the day asked for: "what did i download today" listed a file
                # from three days before as if it were today's (Codex's review of #276).
                midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
                start, end = {
                    "today": (midnight, None),
                    "yesterday": (midnight - timedelta(days=1), midnight),
                    "this week": (midnight - timedelta(days=midnight.weekday()), None),
                }[period]
                files = scanned.data.get("files") or []
                kept = [
                    entry
                    for entry in files
                    if start <= datetime.fromisoformat(str(entry["modified"]))
                    and (end is None or datetime.fromisoformat(str(entry["modified"])) < end)
                ]
                return ToolResult.success(
                    f"{len(kept)} file(s) in {scanned.data['root']} changed {period}.",
                    files=kept,
                    root=scanned.data["root"],
                    order="date",
                    period=period,
                    newest=files[0] if files else None,
                )
            # The agent copies "by size" into orders that do not exist ("by folder") and was told
            # only that the path was missing, so it spent another step guessing.
            other = re.fullmatch(r"(.+?)\s+by\s+(\w+)", root, re.IGNORECASE)
            if (
                not by_size
                and other
                and not Path(root).expanduser().exists()
                and Path(other.group(1)).expanduser().exists()
            ):
                return ToolResult.failure(
                    f"scan files can list largest first (`by size`) or newest first (`by date`), but not by "
                    f"{other.group(2)}. Use "
                    f"`scan files {other.group(1)}`, which also counts the files of each type."
                )
            return self.context.files.scan(root or ".", by_size=bool(by_size))

        if lowered.startswith("read file "):
            return self.context.files.read_text(command[len("read file ") :].strip())

        if lowered.startswith("ask file "):
            return self._ask_file(command[len("ask file ") :].strip())

        if lowered.startswith("summarize file "):
            return self._summarize_any(command[len("summarize file ") :].strip())

        if lowered.startswith("extract text "):
            return self._extract_text_any(command[len("extract text ") :].strip())

        if lowered.startswith("index file "):
            return self._index_file(command[len("index file ") :].strip())
        return None

    async def _dispatch_knowledge(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for the knowledge base."""
        if lowered in {"knowledge list", "knowledge"}:
            documents = self.context.knowledge.list_documents()
            return ToolResult.success(f"{len(documents)} document(s) indexed.", documents=documents)

        if lowered in {"knowledge stats", "knowledge status"}:
            stats = self.context.knowledge.stats()
            return ToolResult.success(
                f"Knowledge base: {stats['document_count']} document(s), {stats['total_char_count']} indexed character(s).",
                stats=stats,
            )

        if lowered.startswith("knowledge export "):
            return self._knowledge_export(command[len("knowledge export ") :].strip())

        if lowered in {"knowledge reindex", "reindex knowledge", "knowledge backfill"}:
            return self._knowledge_reindex()

        if lowered in {"knowledge prune", "prune knowledge"}:
            outcome = self.context.knowledge.prune()
            removed = int(outcome.get("removed") or 0)
            if not removed:
                return ToolResult.success(
                    f"Nothing to prune — {outcome.get('remaining')} document(s) indexed.", **outcome
                )
            listed = "\n".join(f"- {source[:80]}" for source in list(outcome.get("sources") or [])[:12])
            return ToolResult.success(
                f"Pruned {removed} of my own generated document(s); {outcome.get('remaining')} remain. "
                f"Your own indexed files are never pruned.\n\n{listed}",
                **outcome,
            )

        if lowered.startswith("knowledge forget "):
            return self._knowledge_forget(command[len("knowledge forget ") :].strip())

        if lowered in {"knowledge clear", "forget knowledge"}:
            removed = self.context.knowledge.clear()
            return ToolResult.success(f"Cleared {removed} indexed document(s).", removed=removed)

        if lowered.startswith("knowledge search "):
            return self._knowledge_search(command[len("knowledge search ") :].strip())

        if lowered.startswith("ask knowledge "):
            return self._knowledge_answer(command[len("ask knowledge ") :].strip(), history_turns)

        if lowered.startswith("answer from knowledge "):
            return self._knowledge_answer(command[len("answer from knowledge ") :].strip(), history_turns)

        if lowered.startswith("recall "):
            return self._knowledge_search(command[len("recall ") :].strip())
        return None

    async def _dispatch_vault(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for the Obsidian vault."""
        if lowered in {"notes", "vault", "notes status", "vault status", "obsidian", "obsidian status"}:
            return self.context.obsidian.status()

        if lowered in {"notes list", "list notes"}:
            return self.context.obsidian.list_notes()

        if lowered.startswith("notes search "):
            return self.context.obsidian.search(command[len("notes search ") :].strip())

        if lowered.startswith("note search "):
            return self.context.obsidian.search(command[len("note search ") :].strip())

        if lowered in {"notes audit", "vault audit", "audit notes", "audit vault"}:
            return self.context.obsidian.audit()

        if lowered.startswith("ask vault ") or lowered.startswith("ask notes "):
            return self._ask_vault(command.split(" ", 2)[2].strip() if len(command.split(" ", 2)) > 2 else "")

        if lowered.startswith("read note "):
            return self.context.obsidian.read_note(command[len("read note ") :].strip())

        if lowered.startswith("save note "):
            rest = command[len("save note ") :].strip()
            match = re.search(r"\s*[:|]\s*", rest)
            if match:
                title, body = rest[: match.start()].strip(), rest[match.end() :].strip()
            else:
                title, body = rest, rest
            return self.context.obsidian.save_note(title, body)
        return None

    async def _dispatch_file_intelligence(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for file inspection, conversion and extraction."""
        if lowered.startswith("file info "):
            return self.context.files.file_info(command[len("file info ") :].strip())

        if lowered.startswith("extract tables "):
            return self.context.files.extract_tables(command[len("extract tables ") :].strip())

        if lowered.startswith("analyze spreadsheet "):
            return self.context.files.analyze_spreadsheet(command[len("analyze spreadsheet ") :].strip())

        if lowered.startswith("analyse spreadsheet "):
            return self.context.files.analyze_spreadsheet(command[len("analyse spreadsheet ") :].strip())

        if lowered.startswith("inspect file "):
            return self._file_processor().inspect(command[len("inspect file ") :].strip())

        if lowered.startswith("process file "):
            rest = command[len("process file ") :].strip()
            target, intent = self._split_process_intent(rest)
            return self._file_processor().process(target, intent)

        if lowered.startswith("ocr image "):
            return self.context.transcribe.ocr_image(command[len("ocr image ") :].strip())

        if lowered.startswith("ocr "):
            return self.context.transcribe.ocr_image(command[len("ocr ") :].strip())

        if (result := forecast_command(command)) is not None:
            return result
        if (result := diagnostics_command(command)) is not None:
            return result
        if (result := breakdown_command(command)) is not None:
            return result
        if lowered == "record" or re.fullmatch(r"record -?\d+(?:\.\d+)?", lowered):
            from laptop_agent.recordings import MAX_SECONDS, recording_seconds

            seconds = recording_seconds(command)
            if seconds is None or not 0 < seconds <= MAX_SECONDS:
                return ToolResult.failure("Choose a recording duration greater than 0 and at most 120 seconds.")
            if not self.recording_enabled:
                return ToolResult.failure(
                    "Voice recording needs the J.A.R.V.I.S app or web page. Open it and ask there."
                )
            return ToolResult.success(
                f"Record up to {seconds:g} seconds. Stop or press Space to keep a shorter clip.",
                record={"seconds": float(seconds)},
            )

        if lowered.startswith("transcribe "):
            return self.context.transcribe.transcribe_media(command[len("transcribe ") :].strip())
        return None

    async def _dispatch_vision(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for screen, image and webcam vision."""
        if lowered in {
            "read screen",
            "screen text",
            "what is on my screen",
            "what's on my screen",
            "look at my screen",
        }:
            return self._read_screen()

        if lowered.startswith("read screen "):
            return self._read_screen(command[len("read screen ") :].strip())

        if lowered.startswith("look at screen "):
            return self._read_screen(command[len("look at screen ") :].strip())

        if lowered.startswith("describe image "):
            return self._describe_image(command[len("describe image ") :].strip())

        if lowered.startswith("look at image "):
            return self._describe_image(command[len("look at image ") :].strip())

        if lowered.startswith("describe video "):
            return self._describe_video(command[len("describe video ") :].strip())

        if lowered.startswith("watch video "):
            return self._describe_video(command[len("watch video ") :].strip())

        if lowered in {"look at webcam", "describe webcam", "what do you see", "look at me", "webcam"}:
            return self._webcam_look()

        if lowered.startswith("look at webcam "):
            return self._webcam_look(command[len("look at webcam ") :].strip())

        if lowered.startswith("describe webcam "):
            return self._webcam_look(command[len("describe webcam ") :].strip())

        if lowered in {"capture webcam", "webcam capture", "take a photo"}:
            return self.context.webcam.capture()
        return None

    async def _dispatch_tasks(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for task dashboards, workflows and folder operations."""
        if lowered.startswith("code run "):
            return self._code_fix().run(command[len("code run ") :])
        if lowered.startswith("code fix "):
            return self._code_fix().fix(command[len("code fix ") :])

        if lowered in {"tasks", "task dashboard", "show tasks"}:
            dashboard = self.context.tasks.latest()
            if dashboard is None:
                return ToolResult.success("No parallel task runs yet. Use 'multi <cmd> ;; <cmd>'.", dashboard=None)
            retry_hint = (
                " Use 'multi retry failed' to rerun failed subtasks." if dashboard.get("retry_available") else ""
            )
            return ToolResult.success(
                f"Latest run: {dashboard['ok_count']} ok, {dashboard['failed_count']} failed.{retry_hint}",
                dashboard=dashboard,
            )

        if lowered in {"workflow status", "workflows", "workflow dashboard"}:
            return self._workflow_status()

        if lowered in {"workflow retry failed", "retry failed workflow"}:
            return await self._workflow_retry_failed()

        if lowered.startswith("workflow "):
            return await self._run_workflow(command[len("workflow ") :].strip())

        if lowered.startswith("convert file "):
            rest = command[len("convert file ") :].strip()
            match = re.search(r"\s+to\s+", rest, re.IGNORECASE)
            if not match:
                return ToolResult.failure("Use: convert file <source> to <destination>")
            source = rest[: match.start()].strip().strip("'\"")
            destination = rest[match.end() :].strip().strip("'\"")
            if not source or not destination:
                return ToolResult.failure("Use: convert file <source> to <destination>")
            return self.context.files.convert(source, destination)

        if lowered.startswith("organize folder "):
            rest = command[len("organize folder ") :].strip()
            apply = False
            if re.search(r"\s+apply$", rest, re.IGNORECASE):
                apply = True
                rest = re.sub(r"\s+apply$", "", rest, flags=re.IGNORECASE).strip()
            target = rest.strip("'\"") or "."
            return self.context.files.organize(target, apply=apply)

        if lowered.startswith("search files "):
            split = _query_and_root(command[len("search files ") :].strip())
            if split is None:
                return ToolResult.failure("Use: search files <query> <root>")
            return self.context.files.search_text(*split)
        return None

    async def _dispatch_research(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for research and the advisor."""
        if lowered == "mcp":
            return self._mcp().listing()
        if lowered.startswith("mcp "):
            server, _, rest = command[len("mcp ") :].strip().partition(" ")
            tool, _, arguments = rest.strip().partition(" ")
            if not tool:
                return ToolResult.failure('Say it like: mcp <server> <tool> {"argument": "value"}. "mcp" lists them.')
            return self._mcp().call(server, tool, arguments)

        if lowered.startswith("save research report "):
            return self._save_research_report(command[len("save research report ") :].strip())

        for verb in ("solve ", "advise me on ", "advise ", "strategize ", "strategise "):
            if lowered.startswith(verb):
                return self._solve(command[len(verb) :].strip(), history_turns)

        if lowered.startswith("research report "):
            return self._research_report(command[len("research report ") :].strip())

        if lowered.startswith("research "):
            return self._research(command[len("research ") :].strip())
        return None

    async def _dispatch_utilities(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for the clock, calculator, capabilities and diagnostics."""
        if lowered == "watches":
            return self._watches().listing()
        if lowered == "watch health":
            return self._watches().watch_health()
        if lowered == "watch check health":
            return self._watches().check_health()
        if lowered.startswith("watch check news "):
            return self._watches().check_news(command[len("watch check news ") :])
        if lowered.startswith("watch news "):
            return self._watches().watch_news(command[len("watch news ") :])
        if lowered.startswith("unwatch "):
            return self._watches().unwatch(command[len("unwatch ") :])

        if lowered == "timecard" or lowered.startswith("timecard "):
            return self._time_card(command[len("timecard ") :].strip() if " " in command else "")

        # One prefix, because the clock parses the zone out of the whole phrase itself —
        # the heuristic hands it `time what time is it in EST` verbatim.
        if lowered.startswith(("time ", "date ", "clock ")):
            return self._clock.now(command.split(" ", 1)[1])

        if lowered in {
            "time",
            "date",
            "clock",
            "what time is it",
            "what is the time",
            "current time",
            "today",
            "what day is it",
            "datetime",
        }:
            return self._clock.now("")

        if lowered in {"capabilities", "what can you do"}:
            return self._capabilities()

        if lowered in {"failures", "errors", "what broke", "recent errors"}:
            return self._failure_report()

        if lowered in {"selfcheck", "self check", "self-check", "does everything work", "check yourself"}:
            return self._selfcheck()

        if lowered in {"diagnose", "diagnosis", "self diagnosis", "self-diagnosis", "run diagnostics"}:
            return self.diagnose()

        if lowered.startswith(("calculate ", "calc ", "compute ")):
            rest = command.split(" ", 1)[1]
            return self._calculator.compute(rest)

        if lowered in {"latency", "traces", "why slow", "speed"}:
            return self._latency_report()
        return None

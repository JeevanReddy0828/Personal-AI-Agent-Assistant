"""Acting on its own: agent runs and teams under the supervisor, schedules, autopilot, workflows and the control room.

One part of `AgentOrchestrator` (agents/orchestrator.py), split out by area; it reads the
rest of the orchestrator through `self`."""

from __future__ import annotations

from laptop_agent.cancellation import check_cancelled, OperationCancelled
import asyncio
import re
from dataclasses import replace
from datetime import datetime
from laptop_agent.autopilot import AutopilotStep
from laptop_agent.context import AGENT_BUDGET, context_block
from laptop_agent.copilot import JobCopilot
from laptop_agent.planner import Planner
from laptop_agent.reasoning import AutonomousAgent
from laptop_agent.access import SignedOut, ensure_signed_in
from laptop_agent.safety import ApprovalDenied, ApprovalRequest, RiskLevel
from laptop_agent.scheduler import ScheduleError, parse_schedule
from laptop_agent import supervisor as supervision
from laptop_agent.supervisor import RunStopped, RunTree, Supervisor, Verdict, allowed_in_a_run
from laptop_agent.tasks import TaskRecord
from laptop_agent.tools.base import ToolResult
from laptop_agent.failures import record_failure
from laptop_agent.tools.codefix import CodeFixTool
from laptop_agent.workflows import WorkflowStep
from laptop_agent.agents.common import (
    NL,
    _agent_schedule_refused,
    _answers,
    _counted,
    _job_line,
    _too_often,
)


class AgentMode:
    def _run_terminal_command(self, expression: str) -> ToolResult:
        cleaned = expression.strip()
        if not cleaned:
            return ToolResult.failure("Use: run command <command>")
        match = re.match(r"in\s+(.+?)\s+::\s+(.+)$", cleaned, re.IGNORECASE | re.DOTALL)
        if match:
            cwd = match.group(1).strip().strip("'\"")
            command = match.group(2).strip()
            return self.context.terminal.run(command, cwd=cwd)
        return self.context.terminal.run(cleaned)

    async def _run_autopilot(self, goal: str, commands: list[str]) -> ToolResult:
        cleaned_goal = goal.strip() or "autopilot"
        if not commands:
            return ToolResult.failure("Autopilot could not build a plan for that goal.", goal=cleaned_goal)

        results = []
        records: list[AutopilotStep] = []
        for index, planned in enumerate(commands):
            command = planned.strip()
            if not self.autopilot_planner.is_safe_command(command):
                message = "Blocked: this command needs supervision or approval."
                results.append({"command": command, "ok": False, "message": message, "data": {}})
                records.append(AutopilotStep(index=index, command=command, status="blocked", message=message))
                continue
            agent_id = self.control_room.start(command)
            try:
                result = await self.handle(command, _allow_planner=False)
            except OperationCancelled as exc:
                self.control_room.finish(agent_id, str(exc), ok=False)
                raise
            except Exception as exc:
                self.control_room.finish(agent_id, str(exc), ok=False)
                result = ToolResult.failure(str(exc))
            else:
                self.control_room.finish(agent_id, result.message, ok=result.ok)
            results.append({"command": command, "ok": result.ok, "message": result.message, "data": result.data})
            records.append(
                AutopilotStep(
                    index=index,
                    command=command,
                    status="ok" if result.ok else "failed",
                    message=result.message,
                )
            )

        run = self.context.autopilot.record_run(cleaned_goal, records)
        if run["status"] == "ok":
            return ToolResult.success(
                f"Autopilot completed {run['ok_count']} step(s) for: {cleaned_goal}",
                goal=cleaned_goal,
                plan=commands,
                results=results,
                autopilot=run,
            )
        return ToolResult.failure(
            f"Autopilot finished with {run['failed_count']} failed and {run['blocked_count']} blocked step(s).",
            goal=cleaned_goal,
            plan=commands,
            results=results,
            autopilot=run,
        )

    def _autopilot_status(self) -> ToolResult:
        latest = self.context.autopilot.latest()
        if latest is None:
            return ToolResult.success("No autopilot runs yet.", autopilot=None)
        return ToolResult.success(
            f"Latest autopilot run: {latest['status']} ({latest['ok_count']} ok, {latest['failed_count']} failed, {latest['blocked_count']} blocked).",
            autopilot=latest,
        )

    # The autonomous agent's action vocabulary. These are canonical command forms the
    # orchestrator dispatches directly (no planner), with argument placeholders for the
    # model to fill. This is deliberately broad so users can speak naturally and let the
    # agent translate intent into the right action — read-only steps just run, while
    # state-changing ones (send, write, download, shell, browser) still pass through the
    # approval gate. Keep entries to one line each; the model copies them verbatim.
    _AGENT_COMMANDS = (
        # files & documents
        "scan files <path>",
        "scan files <path> by size",
        "read file <path>",
        "ask file <path> about <question>",
        "summarize file <path>",
        "extract text <path>",
        "extract tables <path>",
        "analyze spreadsheet <path>",
        "process file <path>",
        "file info <path>",
        "search files <query> <path>",
        "file move <file> to <folder>",
        "file copy <file> to <folder>",
        "file rename <file> to <name>",
        "file delete <file>",
        "undo file",
        "convert file <source> to <destination>",
        "organize folder <path>",
        "index file <path>",
        # knowledge base
        "recall <query>",
        "ask knowledge <question>",
        "knowledge list",
        "knowledge stats",
        # notes / vault memory
        "notes search <query>",
        "ask vault <question>",
        "notes audit",
        "read note <name>",
        "save note <title> : <body>",
        "remember <key> = <value>",
        "learn from chat",
        "forget <key>",
        "knowledge prune",
        "window <name> <left|right|top|bottom|full|corner>",
        "windows",
        # web & research
        "web search <query>",
        "news [topic]",
        "watch news <topic>",
        "watch health",
        "watches",
        "unwatch <topic|health>",
        "image <description>",
        "document <request> as pdf|word|markdown",
        "workspace <request>",
        "weather <location>",
        "translate <text> to <language>",
        "distance <origin> to <destination>",
        "trip <stop1> to <stop2> to <stop3>",
        "around <category>",
        "map <place|origin to destination>",
        "hotels near <place>",
        "summarize youtube <url>",
        "research <topic>",
        "research report <topic>",
        "mcp",
        "mcp <server> <tool> <json arguments>",
        "solve <problem or decision>",
        "open url <https url>",
        "message whatsapp|telegram [to <who>] :: <text>",
        "download <url>",
        "inspect page <url>",
        # vision / desktop
        "read screen <question>",
        "describe image <path>",
        "describe video <path> :: <question>",
        "look at webcam <question>",
        "open app <path-or-app>",
        "screenshot <output.png>",
        "play music <file-folder-or-url>",
        # email (state-changing steps are approval-gated)
        "email digest",
        "email api search gmail <query>",
        "email api draft gmail to <addr> subject <subject> body <body>",
        "email api send gmail to <addr> subject <subject> body <body>",
        # tasks, reminders, status
        "briefing",
        "briefing set <reminders, weather [in city], news [about topic], mail, <name> list, laptop>",
        "total|average|count <column> by <column> in <file.csv>",
        "jobs",
        "job add <company> [stage]",
        "tasks",
        "reminders due",
        "reminder add <YYYY-MM-DD> <text>",
        "timer <duration>",
        "alarm <time>",
        "timers",
        "alarms",
        "lists",
        "list <name> show",
        "list <name> add <items>",
        "calendar",
        "convert <amount> <unit> to <unit>",
        "run command <command>",
        "code run <script.py>",
        "code fix <script.py>",
        "memory",
        "audit",
    )

    _TEAM_STEPS = 8
    _CHILD_STEPS = 4

    def _agent_reference(self, delegate_to: tuple[str, ...] = (), helper: bool = False) -> str:
        lines = [
            f"- {command}"
            for command in self._AGENT_COMMANDS
            if allowed_in_a_run(command) and (not helper or supervision.offered_to_helper(command))
        ]
        if delegate_to:
            lines.append(
                f"- delegate <{'|'.join(delegate_to)}> <sub-task>   (hand a part that does not need you to a "
                "cheaper model, written out in full; its answer comes back as the OBSERVATION. A helper "
                "can only look things up, work things out and answer, so make any change yourself; at "
                f"most {supervision.MAX_AGENTS} helpers in a run, so give one helper several items)"
            )
        return "\n".join(lines)

    def _tier_planners(self) -> dict[str, Planner | None]:
        return {"fast": self.planner, "smart": self.smart_planner, "ultra": self.ultra_planner}

    def _delegate_to(self, tier: str, depth: int) -> tuple[str, ...]:
        """The configured tiers below `tier` an agent at `depth` may be offered (none at the depth cap)."""
        if depth >= supervision.MAX_DEPTH or tier not in supervision.TIER_RANK:
            return ()
        planners = self._tier_planners()
        return tuple(
            name
            for name in ("fast", "smart")
            if supervision.TIER_RANK[name] < supervision.TIER_RANK[tier] and _answers(planners[name])
        )

    def _record_verdict(self, source: str, command: str, tier: str, decision, **where: object) -> None:
        try:
            self.context.audit.record(
                "supervisor",
                source=source,
                command=command[:300],
                tier=tier,
                verdict=decision.verdict.value,
                rule=decision.rule,
                reason=decision.reason,
                **where,
            )
        except Exception as exc:  # a full disk must not let a step run unrecorded and unnoticed
            record_failure("supervisor/audit", exc)

    def _record_message(self, **payload: object) -> None:
        try:
            self.context.audit.record("agent_message", **payload)
        except Exception as exc:  # as above: a hand-off must not go unrecorded and unnoticed
            record_failure("supervisor/audit", exc)

    def _supervised(
        self, history: list[dict[str, str]] | None, brain=None, supervisor: Supervisor | None = None, delegate=None
    ):
        """One agent's `execute`: each step is put to its Supervisor before it is dispatched, and the
        verdict is written to the audit log (supervisor.py). `brain` is the agent's own `decide`,
        which says which model tier wrote the step. In a team run `supervisor` shares the run's tree,
        and `delegate` runs the agent a `delegate` step hands work to."""
        supervisor = supervisor or Supervisor()
        gate = self.context.web.approval_gate

        async def execute(command: str) -> ToolResult:
            tier = getattr(brain, "tier", "")
            decision = supervisor.check(command, tier)
            self._record_verdict(
                "agent", command, tier, decision, run=supervisor.agent, parent=supervisor.parent, depth=supervisor.depth
            )
            if decision.verdict is Verdict.STOP:
                raise RunStopped(f"Stopped by the supervisor: {decision.reason}.")
            if decision.verdict is Verdict.REFUSE:
                return ToolResult.failure(f"Refused by the supervisor: {decision.reason}")
            if decision.rule == "delegate":
                if delegate is None:
                    return ToolResult.failure("This run cannot hand work to another agent.")
                return await delegate(command)
            try:
                if decision.verdict is Verdict.ASK:
                    gate.require(
                        ApprovalRequest(
                            action=f"Agent step: {command[:200]}",
                            risk=RiskLevel.HIGH,
                            reason=decision.reason,
                            preview=command,
                        )
                    )
                return await self.handle(command, _allow_planner=False, history=history)
            except ApprovalDenied:
                supervisor.refused()
                raise

        return execute

    def _delegator(self, tree: RunTree, goal: str, parent: str, depth: int, on_step=None):
        """The `delegate` handler for the agent `parent` of a team run: runs a child agent on the
        named lower tier, in the same tree, and returns its answer as the step's result."""

        async def delegate(command: str) -> ToolResult:
            words = command.split(maxsplit=2)
            tier = words[1].lower()  # the supervisor allowed it, so it names a tier below the parent's
            task = words[2].strip() if len(words) > 2 else ""
            if not task:
                return ToolResult.failure("Use: delegate <tier> <sub-task>, with the sub-task written out.")
            planner = self._tier_planners()[tier]
            if not _answers(planner):
                return ToolResult.failure(f"There is no {tier} model configured to hand that to.")
            child_depth = depth + 1
            agent = tree.add_agent(parent, child_depth, tier, task)
            record = tree.agents[-1]
            where = dict(run=agent, parent=parent, depth=child_depth, tier=tier)
            self._record_message(**where, direction="task", text=task[:300], status="sent")

            def child_step(step) -> None:
                step = replace(step, agent=agent, depth=child_depth)
                record["steps"].append(step.__dict__)
                if on_step is not None:
                    on_step(step)

            brain = self._build_agent_brain((planner, self.fallback_planner))
            child = AutonomousAgent(
                decide=_counted(brain, tree),
                execute=self._supervised(
                    None,
                    brain,
                    Supervisor(tree, agent=agent, parent=parent, depth=child_depth, tier=tier),
                    self._delegator(tree, goal, agent, child_depth, on_step),
                ),
                command_reference=self._agent_reference(self._delegate_to(tier, child_depth), helper=True),
                max_steps=self._CHILD_STEPS,
                close_on_stop=False,
            )
            result = await child.run(
                f"{task}\n(This is one part of a larger goal: {goal}. Answer in FINAL from what you know; use a "
                "command only to look up something you cannot know, such as a file, today's news or a figure to "
                "compute. You cannot save, change, create or send anything. If the sub-task asks you to save, "
                "change, create or send something, do not pretend it is done: answer FINAL: Not done - the "
                "planner must do this itself: <what>. The planner combines your answer with the others.)",
                on_step=child_step,
            )
            stopped = tree.stopped
            self._record_message(
                **where,
                direction="result",
                text=result.final_answer[:300],
                status="stopped" if stopped is not None else result.status,
            )
            if stopped is not None:
                # One stop anywhere stops the whole tree, not only the child it happened in.
                raise RunStopped(f"Stopped by the supervisor: {stopped.reason}.")
            answer = result.final_answer
            if result.status == "stopped":
                answer = f"(step limit reached) {answer}"
            return ToolResult(ok=result.status != "failed", message=answer)

        return delegate

    def _build_agent_brain(self, planners=None, answer_max_tokens: int | None = None):
        """Return a sync ``decide(prompt) -> str`` backed by the strongest available model.

        By default tries the smart tier, then the fast planner, then the cross-provider
        fallback (OpenRouter), using the first that returns text — so reasoning (the
        autonomous agent and the advisor) keeps working when a tier is congested. Pass an
        explicit ``planners`` tuple to change the order, and ``answer_max_tokens`` to allow
        long outputs (e.g. a full resume) instead of a concise chat reply. Yields '' when no
        LLM is configured, so callers end with a clear message.
        """
        if planners is None:
            planners = (self.smart_planner, self.planner, self.fallback_planner)
        # The backup is written last, so one planner serving two tiers is named as the least trusted.
        names = {
            id(planner): name
            for name, planner in (
                ("fast", self.planner),
                ("smart", self.smart_planner),
                ("ultra", self.ultra_planner),
                ("fallback", self.fallback_planner),
            )
            if planner is not None
        }
        answerers = []
        for planner in planners:
            answer = getattr(planner.provider, "answer", None) if planner else None
            if answer is not None:
                answerers.append((names.get(id(planner), ""), answer))
        if not answerers:
            return lambda _prompt: ""
        profile = self.context.memory.get_profile()

        def decide(prompt: str) -> str:
            check_cancelled()
            for tier, answer in answerers:
                # Which tier wrote the reply about to be used: the supervisor trusts a step by it.
                decide.tier = tier  # type: ignore[attr-defined]
                try:
                    reply = answer(prompt, profile, None, None, max_tokens=answer_max_tokens)
                except TypeError:
                    reply = answer(prompt, profile, None, None)  # older provider without max_tokens
                except Exception:
                    reply = None
                check_cancelled()
                if reply:
                    return reply
            return ""

        return decide

    def _resume_copilot(self) -> JobCopilot:
        """CoPilot for full-resume generation. Uses a large output budget so the whole resume
        (every experience + projects + education) is never truncated. The smart tier leads
        because it reliably emits the complete document; the ultra reasoning tier (which tends
        to stop a long structured output early) and OpenRouter are fallbacks."""
        if self._resume_copilot_cache is None:
            brain = self._build_agent_brain(
                (self.smart_planner, self.ultra_planner, self.planner, self.fallback_planner),
                answer_max_tokens=8000,
            )
            self._resume_copilot_cache = JobCopilot(decide=brain)
        return self._resume_copilot_cache

    async def run_agent(
        self, goal: str, on_step=None, history: list[dict[str, str]] | None = None, team: bool = False
    ) -> ToolResult:
        """Public entry for autonomous runs with an optional per-step callback (used by the
        web UI to stream the live trace). Mirrors the 'agent run <goal>' command path, or with
        ``team`` 'agent team <goal>'. ``history`` is the session transcript so the goal can refer
        to earlier turns."""
        return await self._run_agent(goal, on_step=on_step, history=history, team=team)

    async def _run_agent(
        self,
        goal: str,
        on_step=None,
        history: list[dict[str, str]] | None = None,
        team: bool = False,
        unattended: bool = False,
    ) -> ToolResult:
        goal = goal.strip()
        if not goal:
            return ToolResult.failure("Use: agent run <goal>  (e.g. 'agent run summarize the README and index it')")
        # One agent run at a time across the app: two tabs, or a tab and a scheduled job, each started one.
        if not self._agent_running.acquire(blocking=False):
            return ToolResult.failure("Not started: another agent run is going. Wait for it to finish, or stop it.")
        try:
            refused = self.agent_ledger.start_run()
            if refused:
                return ToolResult.failure(f"Not started: {refused}.")
            return await self._run_agent_now(goal, on_step, history, team, unattended)
        finally:
            self._agent_running.release()

    async def _run_agent_now(
        self, goal: str, on_step, history: list[dict[str, str]] | None, team: bool, unattended: bool
    ) -> ToolResult:
        agent_id = self.control_room.start(f"agent team: {goal}" if team else f"agent: {goal}")
        tree = RunTree(ledger=self.agent_ledger, unattended=unattended)
        if team:
            # The planner leads on the strongest tier configured, with the tiers below and the backup behind it.
            planners = self._tier_planners()
            tier = next((name for name in ("ultra", "smart", "fast") if _answers(planners[name])), "fast")
            ladder = [
                planners[name]
                for name in ("ultra", "smart", "fast")
                if supervision.TIER_RANK[name] <= supervision.TIER_RANK[tier]
            ]
            brain = self._build_agent_brain((*ladder, self.fallback_planner))
            execute = self._supervised(
                history, brain, Supervisor(tree, tier=tier), self._delegator(tree, goal, "a0", 0, on_step)
            )
            reference, max_steps = self._agent_reference(self._delegate_to(tier, 0)), self._TEAM_STEPS
        else:
            brain = self._build_agent_brain()
            execute, reference, max_steps = (
                self._supervised(history, brain, Supervisor(tree)),
                self._agent_reference(),
                6,
            )
        agent = AutonomousAgent(
            decide=_counted(brain, tree), execute=execute, command_reference=reference, max_steps=max_steps
        )
        context = context_block(history or [], goal, budget=AGENT_BUDGET)
        try:
            result = await agent.run(goal, on_step=on_step, context=context)
        except OperationCancelled as exc:
            self.control_room.finish(
                agent_id, "Stopped: the session ended" if isinstance(exc, SignedOut) else "Stopped by user", ok=False
            )
            raise
        except Exception as exc:  # defensive — keep the control room consistent
            self.control_room.finish(agent_id, str(exc), ok=False)
            return ToolResult.failure(f"Autonomous run crashed: {exc}", goal=goal)
        self.control_room.finish(agent_id, result.final_answer, ok=result.status != "failed")

        run = self.context.agent_runs.record_run(result, agents=tree.agents if team else None)
        payload = dict(
            goal=goal,
            answer=result.final_answer,
            steps=[step.__dict__ for step in result.steps],
            agent=run,
        )
        if result.status == "failed":
            return ToolResult.failure(result.final_answer, **payload)
        return ToolResult.success(result.final_answer, **payload)

    def _agents_switch(self, off: bool) -> ToolResult:
        """The kill switch: off refuses every agent run, team and scheduled agent until turned back on.
        A run already going stops at its next step."""
        self.agent_ledger.set_off(off)
        state = self.agent_ledger.state()
        if off:
            return ToolResult.success(
                "Agents are off: no agent run, team or scheduled agent will start, and a run "
                "going now stops at its next step. Say `agents on` to turn them back on.",
                agents_off=True,
            )
        return ToolResult.success(
            f"Agents are on. Today: {state.get('runs', 0)} of {supervision.MAX_RUNS_PER_DAY} "
            f"runs and {state.get('steps', 0)} of {supervision.MAX_STEPS_PER_DAY} steps used.",
            agents_off=False,
        )

    def _agent_runs(self) -> ToolResult:
        runs = self.context.agent_runs.all_runs()
        return ToolResult.success(f"{len(runs)} autonomous run(s) on record.", agent_runs=runs)

    def _agent_last(self) -> ToolResult:
        latest = self.context.agent_runs.latest()
        if latest is None:
            return ToolResult.success("No autonomous agent runs yet.", agent=None)
        return ToolResult.success(
            f"Latest agent run: {latest['status']} ({latest['ok_count']} ok, "
            f"{latest['failed_count']} failed across {latest['step_count']} step(s)).",
            agent=latest,
        )

    def _schedule_add(self, kind: str, expression: str) -> ToolResult:
        # Expression: "<schedule> :: <command-or-goal>", e.g. "daily at 08:00 :: briefing".
        if "::" not in expression:
            return ToolResult.failure(
                "Use: schedule <when> :: <command>   or   schedule agent <when> :: <goal>\n"
                "e.g. 'schedule daily at 08:00 :: briefing' or 'schedule agent every 2 hours :: triage my unread email'"
            )
        when, spec = (part.strip() for part in expression.split("::", 1))
        try:
            if kind == "agent":
                refused = _agent_schedule_refused(parse_schedule(when), self.context.scheduler.list_jobs())
                if refused:
                    return ToolResult.failure(refused)
            job = self.context.scheduler.add(kind, spec, when, datetime.now().astimezone())
        except ScheduleError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(
            f"Scheduled {kind} #{job.id}: {job.schedule.describe()} -> {job.spec}",
            job=job.to_dict(),
        )

    def _schedule_remove(self, raw: str) -> ToolResult:
        try:
            job_id = int(raw.strip().lstrip("#"))
        except ValueError:
            return ToolResult.failure("Use: schedule remove <id>")
        if self.context.scheduler.remove(job_id):
            return ToolResult.success(f"Removed scheduled job #{job_id}.")
        return ToolResult.failure(f"No scheduled job #{job_id}.")

    def _code_fix(self) -> CodeFixTool:
        if self._code_fix_cache is None:
            self._code_fix_cache = CodeFixTool(
                self._build_document_writer(), self.context.web.approval_gate, self.data_dir / "code_backups"
            )
        return self._code_fix_cache

    def _schedule_list(self) -> ToolResult:
        jobs = [job.to_dict() for job in self.context.scheduler.list_jobs()]
        if not jobs:
            return ToolResult.success("No scheduled jobs. Add one with 'schedule <when> :: <command>'.", jobs=[])
        # "1 scheduled job(s)." named nothing: what was scheduled was only in the data.
        lines = [f"- #{job['id']} {job['schedule_text']} — {_job_line(job)}" for job in jobs[:20]]
        if len(jobs) > 20:
            lines.append(f"- … and {len(jobs) - 20} more")
        noun = "job" if len(jobs) == 1 else "jobs"
        return ToolResult.success(f"{len(jobs)} scheduled {noun}:\n" + "\n".join(lines), jobs=jobs)

    async def run_due_schedules(self, now: datetime | None = None) -> ToolResult:
        """Run every job whose schedule is due. Called by the background ticker and the
        'schedule run due' command. Each job runs through handle()/run_agent so risky steps
        still hit the approval gate."""
        moment = now or datetime.now().astimezone()
        # No `now` given means this read the laptop's clock, so today's target takes the
        # zone's rules: the ticker is what runs jobs through a daylight-saving change.
        due = self.context.scheduler.claim_due_jobs(moment, local=now is None)
        ran = []
        for index, job in enumerate(due):
            try:
                if job.kind == "agent" and _too_often(job.schedule):
                    # Made before the hourly floor: it is not run, and says why.
                    result = ToolResult.failure("Not run: a scheduled agent may run at most once an hour.")
                elif job.kind == "agent":
                    # Nobody is watching: no approval card would be answered, so it reads and computes only.
                    result = await self._run_agent(job.spec, unattended=True)
                else:
                    result = await self.handle(job.spec, _allow_planner=False)
                status = "ok" if result.ok else "failed"
                message = result.message
            except OperationCancelled:
                for pending in due[index:]:
                    self.context.scheduler.mark_ran(pending.id, moment, "stopped")
                raise
            except Exception as exc:
                status, message = "failed", str(exc)
            self.context.scheduler.mark_ran(job.id, datetime.now().astimezone(), status)
            ran.append({"id": job.id, "kind": job.kind, "spec": job.spec, "status": status, "message": message})
        if not ran:
            return ToolResult.success("No scheduled jobs are due.", ran=[])
        ok = sum(1 for item in ran if item["status"] == "ok")
        return ToolResult(ok=ok == len(ran), message=f"Ran {len(ran)} due job(s): {ok} ok.", data={"ran": ran})

    async def _run_many(self, expression: str, retry_of: int | None = None) -> ToolResult:
        commands = [item.strip() for item in expression.split(";;") if item.strip()]
        if not commands:
            return ToolResult.failure("Use: multi <command 1> ;; <command 2>")
        if len(commands) > 20:
            return ToolResult.failure("Run at most 20 subtasks in one batch.")
        slots = asyncio.Semaphore(4)

        async def run(command):
            async with slots:
                check_cancelled()
                return await asyncio.to_thread(lambda: asyncio.run(self._run_tracked_subtask(command)))

        results = await asyncio.gather(*(run(command) for command in commands), return_exceptions=True)
        # gather keeps a stopped subtask as a bare CancelledError, so ask again before reporting.
        check_cancelled()
        ensure_signed_in()
        payload = []
        records = []
        for index, (command, result) in enumerate(zip(commands, results)):
            if isinstance(result, BaseException):
                payload.append({"command": command, "ok": False, "message": str(result), "data": {}})
                records.append(TaskRecord(index=index, command=command, status="failed", message=str(result)))
            else:
                payload.append({"command": command, "ok": result.ok, "message": result.message, "data": result.data})
                records.append(
                    TaskRecord(
                        index=index,
                        command=command,
                        status="ok" if result.ok else "failed",
                        message=result.message,
                    )
                )
        dashboard = self.context.tasks.record_run(records, retry_of=retry_of)
        verb = "Retried" if retry_of is not None else "Ran"
        succeeded = sum(1 for item in payload if item["ok"])
        lines = [f"**{verb} {len(payload)} subtask(s)** — {succeeded} succeeded", ""]
        for item in payload:
            mark = "ok" if item["ok"] else "failed"
            first = str(item.get("message", "")).strip().splitlines()
            lines.append(f"- `{item['command']}` — {mark}: {(first[0] if first else '')[:110]}")
        return ToolResult(
            ok=succeeded == len(payload),
            message=NL.join(lines),
            data={"results": payload, "dashboard": dashboard},
        )

    async def _run_tracked_subtask(self, command: str) -> ToolResult:
        agent_id = self.control_room.start(command)
        try:
            result = await self.handle(command)
        except (Exception, OperationCancelled) as exc:
            self.control_room.finish(agent_id, str(exc), ok=False)
            raise
        self.control_room.finish(agent_id, result.message, ok=result.ok)
        return result

    async def _retry_failed_tasks(self) -> ToolResult:
        plan = self.context.tasks.retry_plan()
        commands = [str(command) for command in plan.get("commands", []) if str(command).strip()]
        if not plan.get("run"):
            return ToolResult.failure("No task run to retry. Use 'multi <cmd> ;; <cmd>' first.")
        if not commands:
            return ToolResult.success("No failed subtasks to retry.", retry=plan)
        return await self._run_many(" ;; ".join(commands), retry_of=int(plan["run"]))

    async def _run_workflow(self, expression: str) -> ToolResult:
        commands = [item.strip() for item in expression.split(";;") if item.strip()]
        if not commands:
            return ToolResult.failure("Use: workflow <command 1> ;; <command 2>")
        if any(command.lower().startswith("workflow ") for command in commands):
            return ToolResult.failure("Workflow steps cannot start another workflow.")

        payload = []
        records: list[WorkflowStep] = []
        stopped_at = None
        for index, command in enumerate(commands):
            agent_id = self.control_room.start(command)
            try:
                result = await self.handle(command)
            except OperationCancelled as exc:
                self.control_room.finish(agent_id, str(exc), ok=False)
                raise
            except Exception as exc:
                self.control_room.finish(agent_id, str(exc), ok=False)
                result = ToolResult.failure(str(exc))
            else:
                self.control_room.finish(agent_id, result.message, ok=result.ok)
            payload.append({"command": command, "ok": result.ok, "message": result.message, "data": result.data})
            records.append(
                WorkflowStep(
                    index=index,
                    command=command,
                    status="ok" if result.ok else "failed",
                    message=result.message,
                )
            )
            if not result.ok:
                stopped_at = index
                for pending_index, pending_command in enumerate(commands[index + 1 :], start=index + 1):
                    records.append(
                        WorkflowStep(
                            index=pending_index,
                            command=pending_command,
                            status="pending",
                            message="Not run because an earlier step failed.",
                        )
                    )
                break

        dashboard = self.context.workflows.record_run(records, stopped_at=stopped_at)
        if stopped_at is None:
            return ToolResult.success(
                f"Workflow completed: {len(records)} step(s) ok.",
                results=payload,
                workflow=dashboard,
            )
        return ToolResult.failure(
            f"Workflow stopped at step {stopped_at + 1}: {records[-1].message}",
            results=payload,
            workflow=dashboard,
        )

    def _workflow_status(self) -> ToolResult:
        dashboard = self.context.workflows.latest()
        if dashboard is None:
            return ToolResult.success("No workflow runs yet. Use 'workflow <cmd> ;; <cmd>'.", workflow=None)
        retry_hint = (
            " Use 'workflow retry failed' to resume from the failed step." if dashboard.get("retry_available") else ""
        )
        return ToolResult.success(
            f"Latest workflow: {dashboard['ok_count']} ok, {dashboard['failed_count']} failed.{retry_hint}",
            workflow=dashboard,
        )

    async def _workflow_retry_failed(self) -> ToolResult:
        commands = self.context.workflows.retry_commands()
        if not self.context.workflows.latest():
            return ToolResult.failure("No workflow run to retry. Use 'workflow <cmd> ;; <cmd>' first.")
        if not commands:
            return ToolResult.success("No failed workflow steps to retry.", workflow=self.context.workflows.latest())
        return await self._run_workflow(" ;; ".join(commands))

    def _agent_control_room(self) -> ToolResult:
        snapshot = self.control_room.snapshot()
        summary = snapshot["summary"]
        return ToolResult.success(
            (
                "Agent control room: "
                f"{summary['working']} working, {summary['idle']} idle, "
                f"{summary['unavailable']} unavailable."
            ),
            control_room=snapshot,
        )

    def _agent_detail(self, raw: str) -> ToolResult:
        agent_id = raw.strip().lower()
        if not agent_id:
            return ToolResult.failure("Use: agent <id>")
        if agent_id == "run":  # "agent run" with its goal missing, not an agent named "run"
            return ToolResult.failure("Use: agent run <goal>  (e.g. 'agent run summarize the README and index it')")
        if agent_id == "team":
            return ToolResult.failure(
                "Use: agent team <goal>  (e.g. 'agent team compare the weather in Paris and Tokyo')"
            )
        detail = self.control_room.detail(agent_id)
        if detail is None:
            return ToolResult.failure(
                f"No agent named '{agent_id}'.",
                available=[agent["id"] for agent in self.control_room.snapshot()["agents"]],
            )
        lines = [
            f"**{detail['name']}** — {detail['role']}",
            f"Status: {detail['status']} · completed {detail.get('completed', 0)} · failed {detail.get('failed', 0)}",
        ]
        history = detail.get("history", [])
        if history:
            lines.append("\nRecent activity:")
            lines.extend(f"- {'✓' if item.get('ok') else '✗'} {item.get('task', '')}" for item in history[:6])
        else:
            lines.append("\nNo activity yet.")
        return ToolResult.success("\n".join(lines), agent=detail)

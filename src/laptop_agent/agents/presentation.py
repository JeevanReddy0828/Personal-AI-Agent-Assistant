"""What the user reads: help, capabilities, reports and humanized results.

One part of `AgentOrchestrator` (agents/orchestrator.py), split out by area; it reads the
rest of the orchestrator through `self`."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from laptop_agent.timeparse import describe
from laptop_agent.tools.base import ToolResult, reserve_new_path
from laptop_agent.failures import FAILURES
from laptop_agent.diagnosis import diagnose as run_diagnosis, local_setup_rows
from laptop_agent.selfcheck import run_selfcheck
from laptop_agent.tools.diagnostics import ANOMALIES_USAGE, DRIVERS_USAGE
from laptop_agent.tools.forecast import USAGE as FORECAST_USAGE
from laptop_agent.agents.common import NL, _readable_size
from laptop_agent.agents.knowledge_actions import KnowledgeActions


class Presentation:
    def _time_card(self, request: str) -> ToolResult:
        """`timecard [zone]` — an image whose text is the actual time.

        A diffusion model cannot spell, so asking FLUX for "the current time" yields
        clock-shaped glyph soup. This draws the real thing.
        """
        from laptop_agent.tools.textcard import render_card

        clock = self._clock.now(request)
        if not clock.ok:
            return clock
        moment = datetime.fromisoformat(str(clock.data["iso"]))
        zone = str(clock.data.get("zone") or "")
        headline = moment.strftime("%I:%M %p").lstrip("0")
        # datetime.fromisoformat keeps the offset but loses the zone *name*, so %Z
        # returns "UTC-04:00" here and pairing it with the offset printed it twice. The
        # readable name is already in the clock's data; it goes in the footer.
        lines = [
            headline,
            moment.strftime("%A, %d %B %Y").replace(" 0", " "),
            str(clock.data.get("offset", "")),
        ]
        directory = self.data_dir / "images"
        path = reserve_new_path(directory, f"time-{int(moment.timestamp())}", ".png")
        drawn = render_card([line for line in lines if line], path, footer=zone)
        if not drawn.ok:
            # Honest failure beats a picture of nonsense: give them the time itself.
            return ToolResult.failure(f"{drawn.message}{NL}{NL}{clock.message}")
        url = f"/api/image?name={path.name}"
        return ToolResult.success(
            f"![The time is {headline}]({url}){NL}{NL}{clock.message}",
            image=str(path),
            name=path.name,
            url=url,
            **{k: v for k, v in clock.data.items() if k != "iso"},
            iso=clock.data["iso"],
        )

    def _selfcheck(self) -> ToolResult:
        """`selfcheck` — do the things I say still reach the tools that handle them?

        Offline and instant, so it can be run at any moment without spending the user's
        free-tier quota to find out whether their own phrasings work. It asserts
        outcomes rather than shapes, because every expensive bug here looked healthy
        beside a passing test: a meter that rendered nothing for three months, an ETag
        that never saved a byte, and two routing holes that answered questions about the
        user's own data with a model that could not see it.
        """
        checks, verdict = run_selfcheck(self.router)
        failed = [check for check in checks if not check.ok]
        lines = [verdict, ""]
        for check in failed:
            lines.append(f"  FAIL  {check.name}")
            lines.append(f"        {check.detail}")
        if not failed:
            lines.append(
                f"  Routing contract: {len(checks)} phrasings, all reaching the "
                "right tool, none grabbing ordinary prose."
            )
        payload = {
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
            "passed": len(checks) - len(failed),
            "failed": len(failed),
        }
        build = ToolResult.success if not failed else ToolResult.failure
        return build("\n".join(lines).rstrip(), **payload)

    def diagnose(self) -> ToolResult:
        """`diagnose` — what is wrong right now: Setup, storage, routing, caught failures and
        response times, then one live call to each configured model tier and a web search
        (diagnosis.py). The Setup panel's Run diagnosis button calls this too."""
        rows = self.setup_source() if self.setup_source is not None else local_setup_rows(self)
        report = run_diagnosis(self, rows)
        # It ran, whatever it found: a failure here is traced as a failed turn, which the next
        # diagnosis would then report as one of the problems.
        return ToolResult.success(report.markdown(), **report.as_data())

    @staticmethod
    def _failure_report() -> ToolResult:
        """`failures` — what has been caught and swallowed this session.

        Every `except` that returns a fallback also records here, because the two worst
        bugs found in this codebase both hid behind code that handled an error politely:
        a permanently 400-ing ultra tier reported itself as "busy", and a 503 from the
        chat endpoint was shown to the user as "the model returned an empty document"."""
        summary = FAILURES.summary()
        recent = FAILURES.recent(12)
        if not recent:
            return ToolResult.success("Nothing has failed this session.", **summary)
        lines = [
            f"**{summary['kept']} failure(s) recorded**, {summary['distinct']} distinct.",
            "",
            "| where | what | age | detail |",
            "|---|---|---|---|",
        ]
        for entry in recent:
            detail = str(entry.get("message", "")).replace("|", "/")[:80]
            lines.append(f"| `{entry['where']}` | {entry['kind']} | {entry['age_seconds']}s ago | {detail} |")
        frequent = summary.get("most_frequent") or []
        if frequent:
            lines.append("")
            lines.append(
                "**Most frequent:** " + " · ".join(f"{item['what']} ×{item['count']}" for item in frequent[:5])
            )
        return ToolResult.success(NL.join(lines), **summary)

    @staticmethod
    def _capabilities() -> ToolResult:
        """A grouped tour rather than the raw command list.

        "what can you do" is the first thing anyone asks, and it used to return a hundred
        lines of command syntax. `help` still does, for reference."""
        text = NL.join(
            [
                "I run on your laptop and I can act on it, not just talk about it. "
                "Plain language works for all of this — the commands are just shortcuts.",
                "",
                "**Day to day**",
                "Reminders, timers and alarms that ring on screen and out loud · shopping and to-do "
                "lists · unit conversions and date counting · I remember what you tell me.",
                "_Try:_ `set a pasta timer for 10 minutes` · `add milk and eggs to my shopping list` · "
                "`how many days until christmas` · `remember my wife's birthday is june 5`",
                "",
                "**Files and documents**",
                "Read, search, convert and organise your files · summarise a PDF, DOCX or "
                "spreadsheet · answer questions about one file · pull tables out · per-column stats.",
                "_Try:_ `summarize the readme` · `what files are here` · `analyze spreadsheet sales.csv`",
                "",
                "**See and hear**",
                "OCR an image or scan with layout preserved · transcribe audio and video · "
                "look at your screen or webcam · summarise a YouTube video.",
                "_Try:_ `read screen` · `ocr image receipt.png` · `summarize youtube <url>`",
                "",
                "**Make things**",
                "Draw a picture · write a real PDF, Word or Markdown document · draw a diagram "
                "or flowchart · build a table you can copy or export as CSV.",
                "_Try:_ `draw a red fox in snow` · `write a one page brief on X as a pdf` · "
                "`draw an ERD for a users and orders schema`",
                "",
                "**Know things**",
                "Real headlines and weather · web search and multi-source research · a searchable "
                "knowledge base of anything you index · your Obsidian vault as memory · exact arithmetic.",
                "_Try:_ `news` · `weather Hyderabad` · `research local-first AI agents` · `what is 67458363*37834872`",
                "",
                "**Do things**",
                "Open URLs and download files · open apps · run shell commands · send and search "
                "email · play a song or video on YouTube · driving distances, trips and maps.",
                "_Anything that changes the outside world asks you first._",
                "_Try:_ `play music despacito` · `distance Hyderabad to Kurnool` · `email unread`",
                "",
                "**Think ahead**",
                "Weigh a decision with researched options and a plan · run an autonomous "
                "multi-step goal · schedule recurring jobs · reminders · a daily briefing.",
                "_Try:_ `should I use Postgres or MongoDB for this` · `agent run tidy my downloads folder` · "
                "`daily at 08:00 :: briefing`",
                "",
                "Ask `help` for the full command list, or `latency` to see where time went.",
            ]
        )
        return ToolResult.success(text, kind="capabilities")

    @staticmethod
    def help_text() -> str:
        return "\n".join(
            [
                "Commands:",
                "  remember <key> = <value>",
                "  forget <key>",
                "  knowledge prune",
                "  window <name> <position>",
                "  windows",
                "  memory",
                "  audit",
                "  briefing  ·  briefing set <sections>  ·  briefing add|remove <sections>  ·  briefing format|reset",
                "  jobs  ·  job add <company> [stage]  ·  job stage <id> <stage>  ·  job remove <id>",
                "  autopilot <goal>",
                "  autopilot workflow <safe command 1> ;; <safe command 2>",
                "  autopilot status",
                "  agent run <goal>   (autonomous multi-step: plans, acts, observes, repeats)",
                "  agent team <goal>  (the strongest model plans and hands parts to cheaper ones)",
                "  agent runs | agent last",
                "  schedule <when> :: <command>     (e.g. 'daily at 08:00 :: briefing')",
                "  schedule agent <when> :: <goal>  (run the autonomous agent on a schedule)",
                "  agents off | agents on  (turn every agent run off, or back on)",
                "  schedule list | schedule remove <id> | schedule run due",
                '  remind me <what> <when>  ("to call mom at 6pm", "in 20 minutes", "every day at 8am")',
                "  reminders | reminders due | reminders next [alarm|timer] | reminder done <id>",
                "  reminder delete <id|words|all>  ·  reminder stop <words>  (only what is going off now)",
                "  reminder snooze [id] [<n>m]",
                "  timer <duration> [label]  ·  alarm <time>  ·  timers  (running timers and the time left)",
                "  lists | list <name> show | list <name> add <items> | list <name> remove <item> | list <name> clear",
                "  calendar | calendar add <event and time>  (no calendar is connected: it sets a reminder)",
                "  system status  (battery, CPU, memory, disk)",
                "  convert <amount> <unit> to <unit>",
                "  how many days until <date|holiday|my ...> | when is <holiday|my ...>",
                "  flip a coin | roll a dice | pick a number between <a> and <b>",
                "  what's my <fact>  (read back from what the user told me)",
                "  screenshot",
                "  agents | agent <id>",
                "  scan files <path>",
                "  scan files <path> by size  (largest files first)",
                "  read file <path>",
                "  ask file <path> about <question>",
                "  summarize file <path>  (text, PDF, DOCX, images, audio, video)",
                "  extract text <path>",
                "  file info <path>",
                "  extract tables <path>",
                "  analyze spreadsheet <path>  (per-column stats for CSV/TSV)",
                f"  {FORECAST_USAGE}  (computed, with a range from its own past errors)",
                f"  {DRIVERS_USAGE}  (associations, scored on rows the fit never saw)",
                "  total|average|count <column> by <column> in <file.csv>  (computed, with a chart; also top N, by month)",
                f"  {ANOMALIES_USAGE}  (robust median/MAD scores)",
                "  process file <path> [as <operation>]  (auto-detects type, picks the best action)",
                "  convert file <source> to <destination>",
                "  organize folder <path> [apply]",
                "  ocr image <path>",
                "  record <seconds>  (app/web microphone; up to 120 seconds)",
                "  transcribe <audio-or-video-path>",
                "  read screen [question]   (vision: looks at your screen)",
                "  describe image <path>    (vision)",
                "  describe video <path> [:: question]  (video model: watches a clip)",
                "  look at webcam [question]  (vision: captures and describes a camera frame)",
                "  index file <path>",
                "  recall <query>",
                "  ask knowledge <question>",
                "  knowledge list",
                "  knowledge stats",
                "  knowledge reindex  (embed documents stored before semantic search)",
                "  knowledge export <path>",
                "  knowledge forget <id>",
                "  notes status | notes list | notes search <query>",
                "  ask vault <question>  (link-aware answer from your notes)",
                "  notes audit  (orphans, broken links, notes missing a summary)",
                "  read note <name> | save note <title> : <body>",
                "  remember note <text>",
                "  learn from chat  (the lasting facts you said here, saved after you approve them)",
                "  search files <query> <path>",
                "  file move|copy <file> to <folder> | file rename <file> to <name> | file delete <file> | undo file  (each asks; delete goes to the Recycle Bin)",
                "  web search <query>",
                "  clipboard | clipboard copy <text|that> | clipboard ask <request>  (what you copied; 'copy that' copies my last reply)",
                "  news [topic]  (real headlines from free feeds, with article text)",
                "  watch news <topic> | watch health | watches | unwatch <topic|health>  (told when there's a new headline, or the laptop needs attention)",
                "  image <description>  (draw a picture; add landscape/portrait/wide/tall)",
                "  document <request> [as pdf|word|markdown]  (write and render a real file; keep any length asked for, such as 'one page')",
                "  workspace <request>  (make a folder and fill it with files, e.g. 'a folder called python basics with starter scripts'; asks first)",
                "  time | date | time in <place>  (this machine's clock, never the web)",
                "  calculate <expression>  (exact arithmetic: big integers, fractions, functions)",
                "  failures  (what has been caught and swallowed this session)",
                "  plugins | plugin <name> <request>  (your own commands from <data dir>/plugins, when LAPTOP_AGENT_PLUGINS=1)",
                "  latency  (where recent turns spent their time)",
                "  weather <location>  (real current + 3-day forecast)",
                "  translate <text> to <language> [from <language>]  (NVIDIA's hosted translation model)",
                "  distance <origin> to <destination>  (driving miles + ETA)",
                "  trip <stop1> to <stop2> to <stop3> …  (multi-stop route + totals)",
                "  hotels near <place>  ·  nearby <category> near <place>",
                "  around <category>  ·  where am i  (IP location)  ·  map <place|A to B>",
                "  summarize youtube <url>  (transcript -> summary, asks indexed too)",
                "  research <topic>",
                "  mcp | mcp <server> <tool> <json arguments>  (tools from MCP servers set up in <data dir>/mcp.json; each asks)",
                "  research report <topic>",
                "  save research report <topic> to <path|obsidian>",
                "  solve <problem or decision>  (researches, weighs options, recommends a plan)",
                "  open url <url>",
                "  message whatsapp|telegram [to <name or number>] :: <text>  (opened ready to send; you press send)",
                "  download <url>",
                "  inspect page <url>",
                "  inspect forms <url>",
                "  preview form fill <url>",
                "  fill form <url>",
                "  open app <path-or-app>",
                "  screenshot <output.png>",
                "  keys <new tab|back|scroll down|zoom in|…> [in <app>] | keys type [<app>] :: <text>  (sent to the app you name, else the one you were in)",
                "  run command <command>",
                "  run command in <cwd> :: <command>",
                "  code run <script.py> | code fix <script.py>  (run it; fix it from its own error, shown as a diff first)",
                "  play music <file-folder-or-url>",
                "  media playpause|next|previous|stop|volumeup|volumedown|mute",
                "  lock screen | power sleep|shutdown|restart|cancel | brightness [0-100|up|down]  (the laptop itself; sleep and power ask)",
                "  wallpaper <image path>|restore | processes | end process <name> | settings <page>",
                "  email search <query>",
                "  email unread",
                "  email digest  (summarize your unread inbox)",
                "  email api search gmail|outlook <query>",
                "  email api unread gmail|outlook",
                "  email api draft gmail|outlook to <addr> subject <subject> body <body>",
                "  email api send gmail|outlook to <addr> subject <subject> body <body>",
                "  email oauth status",
                "  email oauth url gmail|outlook",
                "  email oauth exchange gmail|outlook <authorization-code>",
                "  email oauth refresh gmail|outlook",
                "  email oauth forget gmail|outlook",
                "  email tokens status",
                "  email to <addr> subject <subject> body <body>",
                "  send email to <addr> subject <subject> body <body>",
                "  plan apply job <job-url-or-description>",
                "  multi <command 1> ;; <command 2>",
                "  multi retry failed",
                "  tasks",
                "  workflow <command 1> ;; <command 2>",
                "  workflow status | workflow retry failed",
            ]
        )

    def _latency_report(self) -> ToolResult:
        """`latency` — where recent turns actually spent their time."""
        stats = self.traces.summary()
        if not stats.get("turns"):
            return ToolResult.success("No turns recorded yet. Ask me a few things and try again.", **stats)

        def ms(value) -> str:
            return "—" if value is None else f"{value} ms"

        lines = [
            f"Over the last {stats['turns']} turn(s) — {stats['chat_turns']} chat, {stats['command_turns']} command:",
            "",
            "| measure | median |",
            "|---|---|",
            f"| total | {ms(stats['median_total_ms'])} |",
            f"| routing | {ms(stats['median_route_ms'])} |",
            f"| time to first token (chat) | {ms(stats['median_ttft_ms'])} |",
            f"| chat total | {ms(stats['median_chat_total_ms'])} |",
            f"| tool run (command) | {ms(stats['median_tool_ms'])} |",
            "",
            f"{stats['llm_routed']} turn(s) ({stats['llm_routed_pct']}%) paid an extra model call just to "
            "classify the request before answering it.",
        ]
        if stats["degraded"]:
            lines.append(f"{stats['degraded']} turn(s) fell back to a different model tier.")
        if stats["failed"]:
            lines.append(f"{stats['failed']} turn(s) failed.")
        return ToolResult.success(chr(10).join(lines), latency=stats, recent=self.traces.recent(10))

    @staticmethod
    def _humanize(result: ToolResult) -> str:
        """Turn a tool result into a friendly sentence locally (no LLM round-trip)."""
        if not result.ok:
            return result.message
        data = result.data
        if data.get("digest"):  # already an LLM-written summary; leave it as-is
            return result.message
        if data.get("answer"):
            return str(data["answer"])
        if data.get("summary"):
            return str(data["summary"])
        if "files" in data and "root" in data:
            # A count is not an answer. "what files are here" used to reply "I found 200
            # file(s) in E:\projects\..." and name none of them.
            files = data["files"] if isinstance(data["files"], list) else []
            period = data.get("period")
            if not files and period:
                newest = data.get("newest")
                if not isinstance(newest, dict):
                    return f"There are no files in {data['root']}."
                changed = describe(
                    datetime.fromisoformat(str(newest["modified"])), datetime.now().astimezone(), local=True
                )
                return (
                    f"Nothing in {data['root']} changed {period}. The newest is "
                    f"`{Path(str(newest['path'])).name}`, {changed}."
                )
            if not files:
                return f"There are no files in {data['root']}."
            shown = files[:12]
            total = data.get("total_files")
            by_size, by_date = data.get("order") == "size", data.get("order") == "date"
            headline = (
                f"**{len(files)} file(s) in {data['root']} changed {period}**"
                if period
                else f"**Largest of {total} file(s) in {data['root']}**"
                if by_size
                else f"**Newest of {total} file(s) in {data['root']}**"
                if by_date
                else f"**{total} file(s) in {data['root']}**"
                if isinstance(total, int) and total != len(files)
                else f"**{len(files)} file(s) in {data['root']}**"
            )
            lines = [headline, ""]
            # The breakdown is the answer to "how many python files are here", and
            # counting names out of a truncated listing is how that got answered wrong.
            breakdown = data.get("by_extension")
            if isinstance(breakdown, dict) and len(breakdown) > 1 and not (by_size or by_date):
                top = list(breakdown.items())[:6]
                summary = " · ".join(f"`{ext}` {count}" for ext, count in top)
                if len(breakdown) > len(top):
                    summary += f" · +{len(breakdown) - len(top)} other types"
                lines.append("By type: " + summary)
                lines.append("")
            for entry in shown:
                if not isinstance(entry, dict):
                    continue
                name = Path(str(entry.get("path", ""))).name or str(entry.get("path", ""))
                size = entry.get("size_bytes")
                if by_date and entry.get("modified"):
                    changed = datetime.fromisoformat(str(entry["modified"]))
                    lines.append(f"- `{name}` — {describe(changed, datetime.now().astimezone(), local=True)}")
                    continue
                lines.append(f"- `{name}`" + (f" — {_readable_size(size)}" if isinstance(size, int) else ""))
            if len(files) > len(shown):
                lines.append(f"- …and {len(files) - len(shown)} more")
            return "\n".join(lines)
        if isinstance(data.get("text"), str) and data["text"].strip() and "path" in data:
            # `read file` / `extract text` put the content in data and the message named
            # only the path, so the thing the user asked to see was never shown.
            body = data["text"].strip()
            head = f"**{Path(str(data['path'])).name}**\n\n"
            return head + (body if len(body) <= 6000 else body[:6000] + "\n\n_(truncated)_")
        if isinstance(data.get("results"), list):
            results = data["results"]
            query = str(data.get("query", "")).strip()
            if not results:
                return f"I couldn't find anything for '{query}'." if query else result.message
            first = results[0] if isinstance(results[0], dict) else {}
            if "url" in first:
                lines = [f"{i + 1}. {r.get('title', '')} — {r.get('url', '')}" for i, r in enumerate(results[:5])]
                return "Here are the top results:\n" + "\n".join(lines)
            if "source" in first:
                lines = [f"- {r.get('source', '')}: {str(r.get('snippet', '')).strip()[:140]}" for r in results[:5]]
                return f"I found {len(results)} match(es):\n" + "\n".join(lines)
        if isinstance(data.get("messages"), list):
            messages = data["messages"]
            if not messages:
                return "Your inbox has no messages matching that."
            lines = []
            for index, mail in enumerate(messages[:8], start=1):
                sender = str(mail.get("from", "")).split("<")[0].strip().strip('"') or str(mail.get("from", ""))
                subject = str(mail.get("subject", "")).strip() or "(no subject)"
                snippet = " ".join(str(mail.get("snippet", "")).split())[:110]
                lines.append(f"{index}. **{sender}** — {subject}" + (f"\n   {snippet}" if snippet else ""))
            more = f"\n\n…and {len(messages) - 8} more." if len(messages) > 8 else ""
            return f"Here are your {len(messages)} most recent message(s):\n\n" + "\n".join(lines) + more
        if isinstance(data.get("columns"), list) and "row_count" in data:
            columns = data["columns"]
            lines = []
            for column in columns[:12]:
                if not isinstance(column, dict):
                    continue
                if column.get("type") == "number":
                    lines.append(
                        f"- {column.get('name')}: number "
                        f"(min {column.get('min')}, max {column.get('max')}, mean {column.get('mean')})"
                    )
                else:
                    samples = ", ".join(str(value) for value in column.get("samples", [])[:3])
                    detail = f"{column.get('unique', 0)} unique" + (f"; e.g. {samples}" if samples else "")
                    lines.append(f"- {column.get('name')}: {column.get('type')} ({detail})")
            more = f"\n…and {len(columns) - 12} more column(s)." if len(columns) > 12 else ""
            return result.message + "\n" + "\n".join(lines) + more
        if "text" in data and "char_count" in data:
            text = str(data.get("text", "")).strip()
            return ("Here's what I read:\n" + text[:700]) if text else result.message
        if "returncode" in data and "command" in data:
            stdout = str(data.get("stdout") or "").strip()
            stderr = str(data.get("stderr") or "").strip()
            output = stdout or stderr
            if output:
                return result.message + "\n" + output[:900]
            return result.message
        if data.get("dashboard"):
            board = data["dashboard"]
            return f"Latest run: {board.get('ok_count', 0)} ok, {board.get('failed_count', 0)} failed across {board.get('task_count', 0)} task(s)."
        if data.get("workflow"):
            workflow = data["workflow"]
            if isinstance(workflow, dict):
                return f"Latest workflow: {workflow.get('ok_count', 0)} ok, {workflow.get('failed_count', 0)} failed across {workflow.get('step_count', 0)} step(s)."
        if data.get("autopilot"):
            autopilot = data["autopilot"]
            if isinstance(autopilot, dict):
                return (
                    f"Autopilot {autopilot.get('status')}: "
                    f"{autopilot.get('ok_count', 0)} ok, {autopilot.get('failed_count', 0)} failed, "
                    f"{autopilot.get('blocked_count', 0)} blocked."
                )
        if isinstance(data.get("reminders"), list):
            # The reminder commands write their own readable list; appending another one
            # here printed every reminder twice, the second time as a raw UTC timestamp.
            return result.message
        if "documents" in data and isinstance(data["documents"], list):
            docs = data["documents"]
            if not docs:
                return "Your knowledge base is empty. Index a file to get started."
            return (
                f"You have {len(docs)} document(s) indexed: "
                + ", ".join(str(d.get("source", "")) for d in docs[:6])
                + "."
            )
        if isinstance(data.get("memory"), dict):
            return KnowledgeActions._memory_text(data["memory"])
        return result.message

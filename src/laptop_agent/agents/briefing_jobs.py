"""The daily briefing, the job tracker and job applications.

One part of `AgentOrchestrator` (agents/orchestrator.py), split out by area; it reads the
rest of the orchestrator through `self`."""

from __future__ import annotations

import contextvars
import html
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from laptop_agent.copilot import JobCopilot, ats_score, extract_keywords
from laptop_agent.jobs import normalize_stage
from laptop_agent.metrics import system_metrics
from laptop_agent.briefing import (
    CAN_INCLUDE,
    DEFAULT_FORMAT,
    MAIL_FETCH,
    MAX_SECTIONS,
    PROFILE_KEY,
    Section,
    describe_format,
    greeting,
    mail_lines,
    news_lines,
    parse_format,
    weather_text,
)
from laptop_agent.timeparse import describe, on_laptop_clock
from laptop_agent.safety import ApprovalDenied
from laptop_agent.tools.base import ToolResult
from laptop_agent.failures import record_failure
from laptop_agent.agents.common import _say_seconds


class BriefingJobs:
    def _jobs_list(self) -> ToolResult:
        jobs = self.context.jobs.list()
        stats = self.context.jobs.stats()
        if not jobs:
            return ToolResult.success(
                "No tracked applications yet. Add one with: job add <company> [stage]", jobs=[], stats=stats
            )
        lines = [
            f"**Job pipeline — {stats['total']} application(s), "
            f"{stats['interviews']} interview(s), {stats['offers']} offer(s), "
            f"{round(stats['response_rate'] * 100)}% response rate**"
        ]
        for job in jobs[:20]:
            role = f" — {job['role']}" if job["role"] else ""
            when = f" · {job['next_date']}" if job["next_date"] else ""
            lines.append(f"- #{job['id']} **{job['company']}**{role} · _{job['stage']}_{when}")
        return ToolResult.success("\n".join(lines), jobs=jobs, stats=stats)

    def _job_add(self, text: str) -> ToolResult:
        text = text.strip()
        if not text:
            return ToolResult.failure("Use: job add <company> [stage]")
        # A trailing recognized stage word becomes the stage; the rest is the company.
        parts = text.rsplit(None, 1)
        company, stage = text, "applied"
        if len(parts) == 2 and normalize_stage(parts[1]) != "applied":
            company, stage = parts[0], parts[1]
        elif len(parts) == 2 and parts[1].lower() in {"applied", "apply"}:
            company, stage = parts[0], "applied"
        try:
            job = self.context.jobs.add(company.strip(), stage=stage)
        except ValueError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(f"Tracking #{job['id']} {job['company']} at stage '{job['stage']}'.", job=job)

    def _job_stage(self, text: str) -> ToolResult:
        parts = text.split(None, 1)
        if len(parts) < 2 or not parts[0].lstrip("#").isdigit():
            return ToolResult.failure("Use: job stage <id> <stage>")
        job = self.context.jobs.update(int(parts[0].lstrip("#")), stage=parts[1].strip())
        if job is None:
            return ToolResult.failure(f"No tracked job #{parts[0].lstrip('#')}.")
        return ToolResult.success(f"#{job['id']} {job['company']} → {job['stage']}.", job=job)

    async def _jobright_pull(self) -> ToolResult:
        resume_text = self.context.jobs.get_resume().get("text", "")
        result = await self.context.jobright.pull(resume_text=resume_text)
        if not result.ok:
            return result
        leads = result.data.get("leads", [])
        summary = self.context.jobs.import_leads(leads)
        return ToolResult.success(
            f"Pulled {len(leads)} Jobright lead(s); added {summary['added']} new, "
            f"skipped {summary['skipped']} already tracked.",
            scraped=result.data.get("scraped", len(leads)),
            relevant=len(leads),
            added=summary["added"],
            skipped=summary["skipped"],
            added_jobs=summary["added_jobs"],
        )

    def _job_remove(self, raw: str) -> ToolResult:
        if not raw.lstrip("#").isdigit():
            return ToolResult.failure("Use: job remove <id>")
        removed = self.context.jobs.remove(int(raw.lstrip("#")))
        return ToolResult.success("Removed." if removed else f"No tracked job #{raw.lstrip('#')}.", removed=removed)

    # The briefing's network sections are fetched at once: weather, three news feeds and an inbox
    # one after another is their sum, and each has a 15-20 second timeout when offline.
    _BRIEFING_NETWORK = frozenset({"weather", "news", "mail"})

    def _briefing(self) -> ToolResult:
        """The daily briefing, in the sections and order the user chose (`briefing set ...`)."""
        sections, custom = self._briefing_sections()
        now = datetime.now().astimezone()
        network = [section for section in sections if section.kind in self._BRIEFING_NETWORK]
        fetched: dict[Section, ToolResult] = {}
        if network:
            pool = ThreadPoolExecutor(max_workers=len(network), thread_name_prefix="briefing")
            try:
                # Each in a copy of this turn's context, so the signed-in check, Stop and the
                # trace still see the turn the section belongs to.
                futures = {
                    section: pool.submit(contextvars.copy_context().run, self._briefing_fetch, section)
                    for section in network
                }
                # A Stop raised in one section ends the briefing now, not after a slower feed
                # (review of #296): only OperationCancelled escapes _briefing_fetch.
                done, _ = wait(futures.values(), return_when=FIRST_EXCEPTION)
                for future in done:
                    if future.exception() is not None:
                        raise future.exception()
                fetched = {section: future.result() for section, future in futures.items()}
            finally:
                pool.shutdown(wait=False, cancel_futures=True)  # a stalled feed ends on its own timeout

        lines = [f"## {greeting(now.hour)}", f"_Your briefing for {now.strftime('%A, %B')} {now.day}._", ""]
        due_reminders = self.context.reminders.due()
        metrics: dict[str, object] | None = None
        for section in sections:
            if section.kind == "reminders":
                lines += self._briefing_reminders(now)
            elif section.kind == "laptop":
                metrics = system_metrics(force=True)
                lines.append(self._briefing_laptop(metrics))
            elif section.kind == "list":
                items = self.context.memory.list_items(section.about)
                name = f"{section.about.capitalize()} list"
                lines.append(f"**{name}** — " + (", ".join(items) + "." if items else "empty."))
            else:
                lines += self._briefing_network_lines(section, fetched[section])
            lines.append("")
        if not custom:
            lines.append('_Change what\'s in it: "set my briefing to weather, AI news and my reminders"._')
        return ToolResult.success(
            "\n".join(lines).strip(),
            briefing={"sections": [section.describe() for section in sections], "custom": custom},
            due_reminders=due_reminders,
            metrics=metrics,
        )

    def _briefing_fetch(self, section: Section) -> ToolResult:
        """One network section. A failure is that section's line, never the whole briefing; a
        Stop or a sign-out still ends the turn (OperationCancelled is not an Exception)."""
        try:
            if section.kind == "weather":
                place = section.about or self._home_place()
                if not place:
                    return ToolResult.failure(
                        'Tell me your city once - "remember my city is Austin" - and the weather will be here.',
                        no_place=True,
                    )
                return self._weather_tool().forecast(place, days=1)
            if section.kind == "news":
                return self._news_tool().headlines(section.about, limit=3, with_text=0)
            return self.context.email.search_inbox("UNSEEN", limit=MAIL_FETCH)
        except ApprovalDenied as exc:
            return ToolResult.failure(f"skipped ({exc}).")
        except Exception as exc:
            record_failure("briefing/" + section.kind, exc)
            return ToolResult.failure("couldn't check just now.")

    @staticmethod
    def _briefing_network_lines(section: Section, result: ToolResult) -> list[str]:
        if section.kind == "weather":
            if not result.ok:
                # The tool's own wording carries the exception ("<urlopen error Tunnel connection
                # failed: 403 Forbidden>"), which voice reads out; "no place called X" is useful.
                said = (
                    "couldn't reach the weather service just now."
                    if result.message.startswith("Couldn't reach")
                    else result.message
                )
                return [f"**Weather** — {said}"]
            return [f"**Weather** — {weather_text(result.data)}"]
        if section.kind == "news":
            label = f"**News about {section.about}**" if section.about else "**News**"
            headlines = result.data.get("headlines") or [] if result.ok else []
            if not headlines:
                return [f"{label} — couldn't reach the news feeds just now."]
            return [label, *news_lines(headlines)]
        if not result.ok:
            if result.data.get("missing"):
                return ['**Mail** — not connected yet. Say "check my email" to see how to set it up.']
            return ["**Mail** — couldn't check the inbox just now."]  # the tool's text carries the exception
        return ["**Mail**", *mail_lines(result.data.get("messages") or [])]

    def _briefing_reminders(self, now: datetime) -> list[str]:
        """What is due, what is left today and the timers running, each once."""
        timer_ids = {item.get("id") for _due, item in self._running_timers()}
        lines = ["**Reminders**"]
        later: list[tuple[datetime, dict]] = []
        for due, item in self._dated():
            message = str(item.get("message", "")).strip()
            if item.get("id") in timer_ids:
                left = round((due - now).total_seconds())
                name = re.sub(r"\s*\(.*\)$", "", message)
                lines.append(f"- {name}: " + ("going off now" if left <= 0 else f"{_say_seconds(left)} left"))
            elif due <= now:
                lines.append(f"- Due: {message}")
            elif on_laptop_clock(due, now.tzinfo).date() == now.date():
                lines.append(
                    f"- {message.capitalize() if message == 'alarm' else message} — {describe(due, now, local=True)}"
                )
            else:
                later.append((due, item))
        if len(lines) == 1:
            if later:
                due, item = later[0]
                lines.append(f"- Nothing due today. Next: {item.get('message')}, {describe(due, now, local=True)}.")
            else:
                lines.append('- Nothing set. Say "remind me to ..." to add one.')
        return lines

    @staticmethod
    def _briefing_laptop(metrics: dict[str, object]) -> str:
        cpu, ram = metrics.get("cpu_percent"), metrics.get("ram_percent")
        parts = [f"CPU {cpu}%" if cpu is not None else "CPU n/a", f"RAM {ram}%" if ram is not None else "RAM n/a"]
        return "**Laptop** — " + ", ".join(parts) + "."

    def _briefing_profile_keys(self) -> list[str]:
        """The remembered fact that holds the format, `briefing format`, however it is spelt. Only
        that one: matching any fact about the briefing let "add mail to my briefing" delete "my
        morning briefing is at 7:30"."""
        return [
            key
            for key in self.context.memory.get_profile()
            if re.sub(r"[^a-z]+", " ", str(key).lower()).strip() == PROFILE_KEY
        ]

    def _briefing_sections(self) -> tuple[list[Section], bool]:
        """The user's sections and True, or the default and False. Mail is in the default only when
        an inbox is connected: a briefing that opens with "not connected" every morning is noise."""
        profile = self.context.memory.get_profile()
        for key in self._briefing_profile_keys():
            sections, _unknown = parse_format(str(profile[key]))
            if sections:
                return sections, True
        sections, _unknown = parse_format(DEFAULT_FORMAT)
        config = getattr(self.context.email, "config", None)
        if config is not None and config.imap_host and config.imap_username and config.imap_password:
            sections.insert(1, Section("mail"))
        return sections, False

    def _briefing_format(self) -> ToolResult:
        sections, custom = self._briefing_sections()
        which = "Your briefing has" if custom else "Your briefing is the default"
        return ToolResult.success(
            f'{which}: {describe_format(sections)}.\n\nChange it with "set my briefing to ...", '
            f'"add ... to my briefing" or "remove ... from my briefing". It can include {CAN_INCLUDE}.',
            briefing={"sections": [section.describe() for section in sections], "custom": custom},
        )

    def _briefing_reset(self) -> ToolResult:
        removed = [key for key in self._briefing_profile_keys() if self.context.memory.forget_profile_value(key)]
        sections, _custom = self._briefing_sections()
        return ToolResult.success(
            ("Your briefing is back to the default" if removed else "Your briefing already is the default")
            + f": {describe_format(sections)}.",
            briefing={"sections": [section.describe() for section in sections], "custom": False},
        )

    def _briefing_change(self, how: str, raw: str) -> ToolResult:
        """`briefing set|add|remove <sections>`. Nothing is saved if any part cannot be read: a
        format that silently dropped "stocks" would look accepted and never show it."""
        wanted, unknown = parse_format(raw)
        if unknown or not wanted:
            said = "', '".join(unknown) if unknown else raw.strip()
            return ToolResult.failure(f"I can't put '{said}' in a briefing. It can include {CAN_INCLUDE}.")
        current, _custom = self._briefing_sections()
        if how == "set":
            sections = wanted
        elif how == "add":
            sections = current + [section for section in wanted if section not in current]
        else:
            sections = [
                section
                for section in current
                if not any(
                    section.kind == gone.kind and (not gone.about or section.about.lower() == gone.about.lower())
                    for gone in wanted
                )
            ]
            if sections == current:
                return ToolResult.failure(
                    f"Your briefing has no {describe_format(wanted)} in it. It has: {describe_format(current)}."
                )
            if not sections:
                return ToolResult.failure(
                    'That would leave your briefing empty. Say "reset my briefing" for the default instead.'
                )
        if len(sections) > MAX_SECTIONS:
            return ToolResult.failure(
                f"A briefing holds at most {MAX_SECTIONS} sections; that would make {len(sections)}. Remove one first."
            )
        for key in self._briefing_profile_keys():
            self.context.memory.forget_profile_value(key)
        self.context.memory.set_profile_value(PROFILE_KEY, describe_format(sections))
        return ToolResult.success(
            f'Your briefing is now: {describe_format(sections)}. Say "briefing" to hear it, or '
            f'"schedule daily at 07:30 :: briefing" to get it every morning.',
            briefing={"sections": [section.describe() for section in sections], "custom": True},
        )

    def _copilot(self) -> JobCopilot:
        if self._copilot_cache is None:
            self._copilot_cache = JobCopilot(decide=self._build_agent_brain())
        return self._copilot_cache

    def tailor_application(self, resume_text: str, job_text: str, company: str = "", role: str = "") -> ToolResult:
        """Tailor a resume to a job description (ATS score + missing keywords + grounded
        bullets/cover letter/interview pack). Public entry for the web Job Tracker page."""
        result = self._copilot().tailor(resume_text, job_text, company=company, role=role)
        if not result.ok:
            return ToolResult.failure(result.package)
        return ToolResult.success(
            result.package,
            ats=result.ats,
            keywords=result.keywords,
            missing=result.missing,
            grounding=result.grounding,
            used_llm=result.used_llm,
            company=company,
            role=role,
        )

    def pipeline_snapshot(self) -> dict:
        """The job pipeline for the live dashboard: every tracked job, each annotated with a
        live (local, no-LLM) ATS score when a base resume and a job description are present,
        plus the funnel stats and base-resume metadata."""
        jobs = self.context.jobs
        resume = jobs.get_resume()
        resume_text = resume.get("text", "")
        enriched = []
        for job in jobs.list():
            item = dict(job)
            jd = job.get("description", "")
            if resume_text and jd:
                item["ats"] = ats_score(extract_keywords(jd), resume_text)
            enriched.append(item)
        return {
            "ok": True,
            "jobs": enriched,
            "stats": jobs.stats(),
            "resume": {
                "present": bool(resume_text),
                "profile": resume.get("profile", {}),
                "chars": len(resume_text),
                "source": resume.get("source", ""),
                "updated_at": resume.get("updated_at", ""),
            },
        }

    def set_resume_text(self, text: str, source: str = "pasted") -> ToolResult:
        text = (text or "").strip()
        if not text:
            return ToolResult.failure("Resume text is empty.")
        self.context.jobs.set_resume(text, source=source)
        return ToolResult.success(f"Saved base resume ({len(text)} chars).", chars=len(text), source=source)

    def set_resume_from_file(self, path: str) -> ToolResult:
        extracted = self.context.files.extract_document_text(path)
        if not extracted.ok:
            return extracted
        text = str(extracted.data.get("text", "")).strip()
        if not text:
            return ToolResult.failure("No text could be extracted from that file.")
        name = Path(path.strip().strip("'\"")).name
        self.context.jobs.set_resume(text, source=name)
        return ToolResult.success(f"Loaded base resume from {name} ({len(text)} chars).", chars=len(text), source=name)

    def tailor_job(self, job_id: int) -> ToolResult:
        """Tailor the stored base resume to a tracked job's description, persisting the
        result on the job. The LLM call is on-demand (one job at a time)."""
        job = self.context.jobs.get(job_id)
        if job is None:
            return ToolResult.failure(f"No tracked job #{job_id}.")
        resume_record = self.context.jobs.get_resume()
        resume = resume_record.get("text", "")
        if not resume:
            return ToolResult.failure("Set a base resume first — paste it or load it from a file.")
        jd = job.get("description", "")
        if not jd:
            return ToolResult.failure(f"#{job_id} {job['company']} has no job description to tailor against.")
        profile = resume_record.get("profile", {}) or {}
        repos = self._github_repos(profile.get("github_user", ""))
        name = next((ln.strip() for ln in resume.splitlines() if ln.strip()), "")
        # Explicit profile overrides win; otherwise retain source contact details.
        contact = profile.get("contact_links", "") or self._contact_from_resume(resume)
        result = self._resume_copilot().tailor_resume(
            resume,
            jd,
            company=job.get("company", ""),
            role=job.get("role", ""),
            repos=repos,
            contact=contact,
            certs=profile.get("cert_links", ""),
            name=name,
        )
        if not result.ok:
            return ToolResult.failure(result.package, job_id=job_id)
        stored = self.context.jobs.set_tailoring(
            job_id,
            package=result.package,
            used_llm=result.used_llm,
            grounding=result.grounding,
            ats=result.ats,
            expected_resume=resume_record.get("updated_at", ""),
        )
        if stored is None:
            return ToolResult.failure(
                "The base resume changed or the job was removed during tailoring. Retry with the current resume."
            )
        return ToolResult.success(result.package, job_id=job_id, ats=result.ats, used_llm=result.used_llm)

    async def render_job_pdf(self, job_id: int) -> ToolResult:
        """Render a job's tailored HTML resume to a downloadable PDF under data_dir/resumes."""
        job = self.context.jobs.get(job_id)
        if job is None or not job.get("tailored_package"):
            return ToolResult.failure("Tailor the job first, then export to PDF.")
        from laptop_agent.tools.resume_pdf import render_html_to_pdf

        package = job["tailored_package"]
        digest = hashlib.sha256(package.encode("utf-8")).hexdigest()[:16]
        out = self.context.jobs.path.parent / "resumes" / f"job_{job_id}_{digest}.pdf"
        result = await render_html_to_pdf(package, out)
        if result.ok:
            if self.context.jobs.set_tailored_pdf(job_id, str(out), expected_package=package) is None:
                return ToolResult.failure("The resume changed during export. Export the current version again.")
        return result

    @staticmethod
    def _contact_from_resume(resume: str) -> str:
        """Recover a contact line (email · phone) from the top of the base resume, escaped
        as safe HTML for the fixed template. Best-effort; returns '' if nothing is found."""
        head = "\n".join(resume.splitlines()[:8])
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", head)
        phone = re.search(r"\+?\d[\d\s().-]{7,}\d", head)
        parts = [m.group(0).strip() for m in (email, phone) if m]
        parts.extend(re.findall(r"https?://[^\s<>]+", head))
        return " · ".join(html.escape(p) for p in parts)

    def _github_repos(self, user: str) -> list[dict]:
        """Public repo list (name + url) for grounding project links in tailored resumes.
        Cached per process; best-effort — returns [] if unset or unreachable."""
        user = (user or "").strip()
        if not user:
            return []
        if getattr(self, "_repo_cache", None) is None:
            self._repo_cache = {}
        if user in self._repo_cache:
            return self._repo_cache[user]
        repos: list[dict] = []
        try:
            request = urllib.request.Request(
                f"https://api.github.com/users/{user}/repos?per_page=100&sort=updated",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "jarvis-resume"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, list):
                repos = [
                    {"name": r.get("name", ""), "url": r.get("html_url", "")}
                    for r in data
                    if isinstance(r, dict) and r.get("html_url")
                ]
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
            repos = []
        self._repo_cache[user] = repos
        return repos

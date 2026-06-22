from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

# Pipeline stages, in funnel order. "lead" is a sourced-but-not-yet-applied state
# (e.g. a Jobright pull) that sits before the funnel; "rejected" is a terminal
# off-ramp tracked separately from the forward funnel.
STAGES = ["lead", "applied", "screen", "interview", "final", "offer", "rejected"]
FUNNEL = ["applied", "screen", "interview", "final", "offer"]
# A response = the application advanced past the initial "applied" cold state.
_RESPONDED = {"screen", "interview", "final", "offer"}

_STAGE_ALIASES = {
    "lead": "lead", "leads": "lead", "sourced": "lead", "discovered": "lead", "new": "lead",
    "apply": "applied", "applied": "applied", "submitted": "applied",
    "screen": "screen", "screening": "screen", "phone": "screen", "recruiter": "screen", "oa": "screen",
    "interview": "interview", "onsite": "interview", "technical": "interview",
    "final": "final", "finals": "final", "final round": "final",
    "offer": "offer", "offered": "offer",
    "reject": "rejected", "rejected": "rejected", "declined": "rejected", "closed": "rejected",
}


def normalize_stage(value: str) -> str:
    return _STAGE_ALIASES.get((value or "").strip().lower(), "applied")


class JobTracker:
    """Persistent store of job applications and their pipeline stage. JSON-backed
    (like the scheduler/task stores) so history survives restarts; the dashboard and
    chat commands both read/write through it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._jobs: list[dict] = []
        self._next_id = 1
        # The base resume used for ATS scoring + tailoring, persisted alongside the jobs.
        self._resume: dict = {}
        self._load()

    def add(self, company: str, role: str = "", stage: str = "applied", recruiter: str = "",
            next_date: str = "", notes: str = "", source: str = "manual",
            url: str = "", external_id: str = "", description: str = "") -> dict:
        company = (company or "").strip()
        if not company:
            raise ValueError("A job needs a company name.")
        now = datetime.now(UTC).isoformat()
        job = {
            "id": self._next_id,
            "company": company,
            "role": (role or "").strip(),
            "stage": normalize_stage(stage),
            "recruiter": (recruiter or "").strip(),
            "next_date": (next_date or "").strip(),
            "notes": (notes or "").strip(),
            "source": source,
            "url": (url or "").strip(),
            "external_id": (external_id or "").strip(),
            "description": (description or "").strip(),
            "created_at": now,
            "updated_at": now,
        }
        self._next_id += 1
        self._jobs.append(job)
        self._save()
        return job

    @staticmethod
    def _lead_key(company: str, role: str) -> str:
        return f"{company.strip().lower()}|{role.strip().lower()}"

    def import_leads(self, leads: list[dict]) -> dict:
        """Add scraped job leads (e.g. from a Jobright pull) at the 'lead' stage, skipping
        any that duplicate an existing tracked job by external id or (company, role)."""
        seen_keys = {self._lead_key(j.get("company", ""), j.get("role", "")) for j in self._jobs}
        seen_ext = {j.get("external_id") for j in self._jobs if j.get("external_id")}
        added: list[dict] = []
        skipped = 0
        for lead in leads:
            company = (lead.get("company") or "").strip()
            role = (lead.get("title") or lead.get("role") or "").strip()
            if not company or not role:
                skipped += 1
                continue
            ext = (lead.get("job_id_ext") or lead.get("external_id") or "").strip()
            key = self._lead_key(company, role)
            if (ext and ext in seen_ext) or key in seen_keys:
                skipped += 1
                continue
            job = self.add(
                company,
                role=role,
                stage="lead",
                source=(lead.get("source") or "jobright"),
                url=(lead.get("job_url") or lead.get("url") or ""),
                external_id=ext,
                description=(lead.get("description") or ""),
            )
            seen_keys.add(key)
            if ext:
                seen_ext.add(ext)
            added.append(job)
        return {"added": len(added), "skipped": skipped, "added_jobs": added}

    def list(self) -> list[dict]:
        # Most recently touched first.
        return sorted(self._jobs, key=lambda j: j.get("updated_at", ""), reverse=True)

    def get(self, job_id: int) -> dict | None:
        return next((j for j in self._jobs if j["id"] == job_id), None)

    def update(self, job_id: int, **fields) -> dict | None:
        job = self.get(job_id)
        if job is None:
            return None
        allowed = {"company", "role", "stage", "recruiter", "next_date", "notes"}
        for key, value in fields.items():
            if key not in allowed or value is None:
                continue
            job[key] = normalize_stage(value) if key == "stage" else str(value).strip()
        job["updated_at"] = datetime.now(UTC).isoformat()
        self._save()
        return job

    def set_resume(self, text: str, source: str = "") -> dict:
        """Store the base resume text used for ATS scoring and tailoring."""
        self._resume = {
            "text": (text or "").strip(),
            "source": (source or "").strip(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._save()
        return self._resume

    def get_resume(self) -> dict:
        return dict(self._resume)

    def set_tailoring(self, job_id: int, *, package: str, used_llm: bool,
                      grounding: dict | None = None, ats: dict | None = None) -> dict | None:
        """Persist a tailoring result (package + grounding + ATS) onto a tracked job."""
        job = self.get(job_id)
        if job is None:
            return None
        job["tailored"] = True
        job["tailored_at"] = datetime.now(UTC).isoformat()
        job["tailored_package"] = package
        job["tailored_used_llm"] = bool(used_llm)
        job["tailored_grounding"] = grounding or {}
        if ats:
            job["ats"] = ats
        job["updated_at"] = job["tailored_at"]
        self._save()
        return job

    def remove(self, job_id: int) -> bool:
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if j["id"] != job_id]
        if len(self._jobs) != before:
            self._save()
            return True
        return False

    def stats(self) -> dict:
        """Chart-ready aggregates: funnel counts, applications per ISO week, totals,
        and a response rate (share of applications that advanced past 'applied')."""
        funnel = {stage: 0 for stage in STAGES}
        for job in self._jobs:
            funnel[job.get("stage", "applied")] = funnel.get(job.get("stage", "applied"), 0) + 1
        total = len(self._jobs)
        leads = funnel.get("lead", 0)
        # Sourced leads aren't applications, so the response rate is measured against
        # everything that actually reached "applied" or beyond.
        applications = total - leads
        responded = sum(1 for j in self._jobs if j.get("stage") in _RESPONDED)
        interviews = sum(1 for j in self._jobs if j.get("stage") in {"interview", "final", "offer"})
        offers = funnel.get("offer", 0)
        return {
            "total": total,
            "leads": leads,
            "funnel": [{"stage": s, "count": funnel[s]} for s in FUNNEL],
            "rejected": funnel.get("rejected", 0),
            "interviews": interviews,
            "offers": offers,
            "response_rate": round(responded / applications, 3) if applications else 0.0,
            "by_week": self._by_week(),
        }

    def _by_week(self, weeks: int = 8) -> list[dict]:
        counts: dict[str, int] = {}
        for job in self._jobs:
            stamp = job.get("created_at", "")
            try:
                created = datetime.fromisoformat(stamp).date()
            except (ValueError, TypeError):
                continue
            iso = created.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
            counts[key] = counts.get(key, 0) + 1
        return [{"week": week, "count": counts[week]} for week in sorted(counts)][-weeks:]

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        jobs = data.get("jobs")
        if isinstance(jobs, list):
            self._jobs = [j for j in jobs if isinstance(j, dict) and "id" in j]
        if isinstance(data.get("resume"), dict):
            self._resume = data["resume"]
        existing = [int(j["id"]) for j in self._jobs if str(j.get("id", "")).isdigit()]
        self._next_id = max([int(data.get("next_id", 1)), *(n + 1 for n in existing)] or [1])

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"next_id": self._next_id, "jobs": self._jobs, "resume": self._resume}, indent=2),
            encoding="utf-8",
        )

from __future__ import annotations

import html as _html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

# Logic ported from the Agentic-AI-JOB-CoPilot project (ATS scoring, keyword/claims
# extraction, grounding) — all stdlib — reused here against this app's own LLM
# provider instead of the openai SDK, so it inherits tiered fallback + zero deps.

Decide = Callable[[str], str]

_STOPWORDS = {
    "the", "and", "with", "for", "you", "our", "are", "will", "have", "this", "that",
    "from", "to", "in", "on", "of", "as", "by", "or", "we", "is", "be", "at", "an", "a",
}


def extract_keywords(job_text: str, limit: int = 60) -> list[str]:
    """ATS-style keyword candidates from a job description (dedup, stopword-filtered)."""
    terms = re.findall(r"[A-Za-z][A-Za-z\+\#\.]{1,30}", job_text or "")
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        # Strip stray leading/trailing dots ("aws." / "kubernetes.") but keep
        # internal/suffix forms like node.js, c++, c#.
        key = term.strip(".").lower()
        if not key or key in _STOPWORDS or len(key) < 3 or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out[:limit]


def ats_score(job_keywords: list[str], resume_text: str) -> dict:
    """Share of JD keywords present (whole-word) in the resume."""
    resume = (resume_text or "").lower()
    hits, misses = [], []
    for keyword in job_keywords:
        clean = (keyword or "").strip().lower()
        if not clean:
            continue
        if re.search(r"\b" + re.escape(clean) + r"\b", resume):
            hits.append(keyword)
        else:
            misses.append(keyword)
    coverage = len(hits) / max(1, len(job_keywords))
    return {
        "score": round(100 * coverage),
        "coverage": round(coverage, 3),
        "hit_count": len(hits),
        "miss_count": len(misses),
        "hits": hits[:50],
        "misses": misses[:50],
    }


def extract_resume_claims(resume_text: str) -> list[str]:
    """Bullet-like evidence lines from the resume, used to ground generated content."""
    lines = [line.strip() for line in (resume_text or "").splitlines()]
    return [line for line in lines if line[:1] in {"-", "•", "*"} and len(line) > 20][:120]


def check_grounding(generated_points: list[str], resume_claims: list[str]) -> dict:
    """Flag generated bullets with little lexical overlap with the resume — a cheap
    hallucination guard (the CoPilot's core idea)."""
    blob = " ".join(resume_claims).lower()
    flagged = []
    for point in generated_points:
        tokens = {t for t in re.findall(r"[a-z]{4,}", point.lower())}
        if sum(1 for t in tokens if t in blob) < 2:
            flagged.append(point.strip())
    return {"flagged": flagged[:20], "ok_count": len(generated_points) - len(flagged)}


# Content rules for the resume generator. The model returns grounded CONTENT as JSON; a
# fixed template (render_resume_html) controls the exact layout, so the format never drifts.
RESUME_RULES = (
    "Act as a senior technical recruiter who screens 200 resumes a day. Tailor the candidate's resume to the "
    "target role and return ONLY a JSON object (no prose, no markdown fences) with EXACTLY this schema:\n"
    "{\n"
    '  "summary": "3 to 4 line professional summary tailored to the target role, grounded in the resume",\n'
    '  "skills": [{"category": "Languages", "items": "comma separated list"}],\n'
    '  "experiences": [{"company": "", "dates": "", "title": "", "location": "", "bullets": ["", ""]}],\n'
    '  "projects": [{"name": "", "stack": "", "repo_url": "", "bullet": ""}],\n'
    '  "education": [{"school": "", "degree": "", "dates": ""}]\n'
    "}\n"
    "Rules:\n"
    "- experiences: include EVERY role from the base resume, newest first, with 1 to 2 concise measurable bullets "
    "each (about 25 words per bullet). Do not drop, merge, or omit any role.\n"
    "- skills: use ONLY the categories and skills present in the base resume; reorder and lightly trim to "
    "emphasize what the JD wants, but NEVER add a category or a skill the resume does not list.\n"
    "- projects: the 4 most relevant to the JD; set repo_url ONLY from the provided repository list (exact match) "
    "or \"\" if none matches. Never invent a URL.\n"
    "- education: each degree with school, degree, dates. NEVER include GPA.\n"
    "- Replace duties with specific, measurable achievements; weave in truthful ATS keywords from the JD.\n"
    "- Do NOT use dashes or hyphens in prose (rephrase). Keep proper nouns and technology names exact.\n"
    "- ANTI-FABRICATION (critical): never add employers, titles, dates, degrees, metrics, or skills/tools not in "
    "the base resume, even when the JD asks for them. Do not claim a years figure beyond what the resume states. "
    "Every value must trace to the base resume.\n"
    "Return ONLY the JSON object."
)


def _extract_json_object(text: str) -> dict | None:
    """Parse the first JSON object out of a model reply (tolerating fences / surrounding prose)."""
    if not text:
        return None
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # strict=False tolerates literal newlines/tabs inside string values, which models often
    # emit in multi-line bullets and which the default decoder rejects as control chars.
    try:
        parsed = json.loads(cleaned, strict=False)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    depth = 0
    for i in range(start, len(cleaned)) if start != -1 else []:
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start : i + 1], strict=False)
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


# Fixed layout matching the user's Caladea (Cambria-compatible) one-page format: centered
# 15pt name, dot-separated contact, justified summary, uppercase section headings with a
# full-width rule, flex rows so dates/locations align right. Format is OURS, not the model's.
_RESUME_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Caladea:ital,wght@0,400;0,700;1,400&display=swap');
@page { size: Letter; margin: 0.5in 0.7in; }
* { box-sizing: border-box; }
body { font-family: 'Caladea','Cambria','Georgia',serif; font-size: 9pt; line-height: 1.25; color: #000; margin: 0; }
a { color: #000; }
.name { text-align: center; font-size: 15pt; font-weight: bold; letter-spacing: 0.4px; }
.contact { text-align: center; font-size: 8.5pt; margin: 2px 0 6px; }
.summary { text-align: justify; margin: 0 0 4px; }
h2 { font-size: 10.5pt; font-weight: bold; text-transform: uppercase; margin: 8px 0 3px;
     border-bottom: 0.75pt solid #000; padding-bottom: 1px; letter-spacing: 0.3px; }
.skill { margin: 1px 0; }
.row { display: flex; justify-content: space-between; gap: 12px; margin-top: 4px; }
.row .r { white-space: nowrap; }
.row .l { font-weight: bold; }
.subrow { display: flex; justify-content: space-between; gap: 12px; font-style: italic; }
.stack { font-style: italic; font-weight: normal; }
ul { margin: 1px 0 2px; padding-left: 15px; }
li { margin: 1px 0; }
"""


def render_resume_html(name: str, contact: str, certs: str, data: dict) -> str:
    """Render grounded resume CONTENT (the model's JSON) into the fixed one-page template.
    ``contact`` and ``certs`` are trusted pre-built HTML; all model content is escaped."""
    def esc(value: object) -> str:
        return _html.escape(str(value or "").strip())

    skills = "".join(
        f'<div class="skill"><b>{esc(s.get("category"))}:</b> {esc(s.get("items"))}</div>'
        for s in data.get("skills", []) if s.get("category")
    )
    experience = ""
    for e in data.get("experiences", []):
        bullets = "".join(f"<li>{esc(b)}</li>" for b in e.get("bullets", []) if str(b).strip())
        experience += (
            f'<div class="row"><span class="l">{esc(e.get("company"))}</span>'
            f'<span class="r">{esc(e.get("dates"))}</span></div>'
            f'<div class="subrow"><span>{esc(e.get("title"))}</span>'
            f'<span>{esc(e.get("location"))}</span></div><ul>{bullets}</ul>'
        )
    projects = ""
    for p in data.get("projects", []):
        url = str(p.get("repo_url") or "").strip()
        link = f'<a href="{esc(url)}">GitHub</a>' if url.startswith("http") else ""
        projects += (
            f'<div class="row"><span><span class="l">{esc(p.get("name"))}</span> '
            f'<span class="stack">{esc(p.get("stack"))}</span></span>'
            f'<span class="r">{link}</span></div><ul><li>{esc(p.get("bullet"))}</li></ul>'
        )
    education = ""
    for ed in data.get("education", []):
        education += (
            f'<div class="row"><span><span class="l">{esc(ed.get("school"))}</span> '
            f'{esc(ed.get("degree"))}</span><span class="r">{esc(ed.get("dates"))}</span></div>'
        )
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{_RESUME_CSS}</style></head><body>"
        f'<div class="name">{esc(name)}</div>'
        f'<div class="contact">{contact}</div>'
        f'<div class="summary">{esc(data.get("summary"))}</div>'
        f"<h2>Technical Skills</h2>{skills}"
        f"<h2>Experience</h2>{experience}"
        f"<h2>Key Projects</h2>{projects}"
        f"<h2>Education</h2>{education}"
        f"<h2>Certifications</h2><div>{certs}</div>"
        f"</body></html>"
    )


@dataclass
class TailorResult:
    company: str
    role: str
    ats: dict
    keywords: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    package: str = ""  # Markdown: tailored bullets + cover letter + interview pack
    grounding: dict = field(default_factory=lambda: {"flagged": [], "ok_count": 0})
    used_llm: bool = False
    ok: bool = True


class JobCopilot:
    """Tailor a resume to a job description: a deterministic ATS score + missing
    keywords, plus (with a model) grounded resume bullets, a cover letter, and an
    interview pack. The model is injected as ``decide`` so it's unit-tested offline."""

    def __init__(self, decide: Decide | None = None, max_resume_chars: int = 12000, max_jd_chars: int = 8000) -> None:
        self._decide = decide
        self._max_resume = max_resume_chars
        self._max_jd = max_jd_chars

    def tailor(self, resume_text: str, job_text: str, company: str = "", role: str = "") -> TailorResult:
        resume_text = (resume_text or "").strip()
        job_text = (job_text or "").strip()
        if not resume_text or not job_text:
            return TailorResult(company, role, ats={}, ok=False,
                                package="Provide both your resume text and the job description.")
        keywords = extract_keywords(job_text)
        claims = extract_resume_claims(resume_text)
        ats = ats_score(keywords, resume_text)
        missing = ats["misses"][:20]
        header = (
            f"**ATS match: {ats['score']}%** — {ats['hit_count']}/{len(keywords)} keywords covered.\n\n"
            + (f"**Missing keywords to weave in:** {', '.join(missing)}\n" if missing else "Great keyword coverage.\n")
        )
        if self._decide is None:
            return TailorResult(company, role, ats, keywords[:40], missing,
                                package=header + "\n_Connect a language model for tailored bullets, a cover letter, and an interview pack._",
                                used_llm=False)
        try:
            reply = (self._decide(self._build_prompt(resume_text, job_text, company, role, keywords, missing, claims)) or "").strip()
        except Exception as exc:  # injected brain — surface, don't crash
            return TailorResult(company, role, ats, keywords[:40], missing,
                                package=header + f"\n_Model error: {exc}_", used_llm=False)
        if not reply:
            return TailorResult(company, role, ats, keywords[:40], missing,
                                package=header + "\n_No language model available for the written sections._", used_llm=False)
        bullets = [ln.strip() for ln in reply.splitlines() if ln.strip()[:1] in {"-", "*", "•"}]
        grounding = check_grounding(bullets, claims)
        return TailorResult(company, role, ats, keywords[:40], missing,
                            package=header + "\n" + reply, grounding=grounding, used_llm=True)

    def _build_prompt(self, resume_text, job_text, company, role, keywords, missing, claims) -> str:
        target = " ".join(p for p in [role, ("at " + company) if company else ""] if p).strip() or "this role"
        return (
            f"You are J.A.R.V.I.S helping Jeevan tailor an application for {target}. Work ONLY from the resume "
            "evidence below — never invent employers, titles, metrics, or skills he doesn't show. If the resume "
            "lacks something the job wants, suggest honest framing, not fabrication.\n\n"
            "Produce GitHub-flavored Markdown with these sections:\n"
            "## Tailored resume bullets\n"
            "6-8 strong bullets (each starting with '- '), rephrasing his real experience toward the job and "
            f"naturally working in the missing keywords where truthful: {', '.join(missing) or '(none)'}\n"
            "## Cover letter\n"
            "A concise, specific 150-200 word letter — no fluff, grounded in the resume and the job.\n"
            "## Interview pack\n"
            "3 STAR stories drawn from his resume + 5 likely interview questions for this role.\n\n"
            f"RESUME:\n{resume_text[: self._max_resume]}\n\nJOB DESCRIPTION:\n{job_text[: self._max_jd]}\n\nYour tailored package:"
        )

    def tailor_resume(
        self,
        resume_text: str,
        job_text: str,
        company: str = "",
        role: str = "",
        repos: list[dict] | None = None,
        contact: str = "",
        certs: str = "",
        name: str = "",
    ) -> TailorResult:
        """Generate a JD-tailored one-page resume. The model returns grounded CONTENT as JSON;
        render_resume_html() lays it out in the fixed template (so the format is exact). The
        name, contact line and certifications come straight from the caller, never the model."""
        resume_text = (resume_text or "").strip()
        job_text = (job_text or "").strip()
        if not resume_text or not job_text:
            return TailorResult(company, role, ats={}, ok=False,
                                package="Provide both the base resume and the job description.")
        keywords = extract_keywords(job_text)
        ats = ats_score(keywords, resume_text)
        if self._decide is None:
            return TailorResult(company, role, ats, ok=False,
                                package="A language model is required to generate a tailored resume.")
        try:
            raw = self._decide(self._build_resume_prompt(resume_text, job_text, company, role, repos or [])) or ""
        except Exception as exc:
            return TailorResult(company, role, ats, ok=False, package=f"Model error: {exc}")
        data = _extract_json_object(raw)
        if not data or not data.get("experiences"):
            return TailorResult(company, role, ats, ok=False,
                                package="The model did not return valid resume content. Try again.")
        if not name:
            name = next((ln.strip() for ln in resume_text.splitlines() if ln.strip()), "")
        html = render_resume_html(name, contact, certs, data)
        grounded_text = [data.get("summary", "")] + [b for e in data.get("experiences", []) for b in e.get("bullets", [])]
        grounding = check_grounding(grounded_text, extract_resume_claims(resume_text))
        return TailorResult(company, role, ats, keywords[:40], ats["misses"][:20],
                            package=html, grounding=grounding, used_llm=True)

    def _build_resume_prompt(self, resume_text, job_text, company, role, repos) -> str:
        target = " ".join(p for p in [role, ("at " + company) if company else ""] if p).strip() or "this role"
        repo_lines = "\n".join(f"  {r.get('name','')}: {r.get('url','')}" for r in repos if r.get("url")) or "  (none provided)"
        return (
            f"{RESUME_RULES}\n\n"
            f"TARGET ROLE: {target}\n\n"
            f"CANDIDATE GITHUB REPOSITORIES (use these exact URLs for matching projects; never invent one):\n{repo_lines}\n\n"
            f"BASE RESUME (ground everything in this; do not fabricate):\n{resume_text[: self._max_resume]}\n\n"
            f"JOB DESCRIPTION:\n{job_text[: self._max_jd]}\n\n"
            "Return ONLY the JSON object:"
        )

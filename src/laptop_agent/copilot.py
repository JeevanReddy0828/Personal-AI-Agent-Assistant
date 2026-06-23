from __future__ import annotations

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


# Style rules for the one-page HTML resume generator (rendered to PDF via Chromium).
# Instructions only — no personal data (contact links / certs live in the stored resume
# profile and are injected per call).
RESUME_RULES = (
    "Act as a senior technical recruiter who screens 200 resumes a day. Rewrite the candidate's resume into "
    "an ATS-optimized resume targeting the role and company below, output as ONE self-contained HTML document "
    "with embedded CSS, following EXACTLY this structure and order:\n"
    "1. HEADER: the candidate's full name in uppercase, bold, centered. Directly under it, one centered CONTACT "
    "line with items separated by ' · ' (use the provided contact links plus the phone and city from the resume).\n"
    "2. SUMMARY: a 2 to 3 line professional summary directly under the header (no section heading), tailored to "
    "the target role, leading with the candidate's level and 2+ years and degree, then key strengths.\n"
    "3. TECHNICAL SKILLS: heading, then several 'Category: comma list' lines (e.g. Generative AI & LLMs / "
    "Python & APIs / Web & Frontend / Cloud / Data & ML). Order categories and items by relevance to the JD.\n"
    "4. EXPERIENCE: heading, then EVERY role from the base resume, newest first. For each role: a row with the "
    "company in bold on the left and the dates on the right, a second row with the job title on the left and the "
    "location on the right, then EXACTLY ONE concise achievement bullet (about 25 words, one or two lines).\n"
    "5. KEY PROJECTS: heading, then the 4 projects most relevant to the JD. For each: the project name in bold "
    "followed by its tech stack, a matching GitHub <a href> link from the provided repository list, then ONE "
    "concise achievement bullet (about 25 words).\n"
    "6. EDUCATION: heading, then each degree (school + dates, degree line). NEVER include GPA.\n"
    "7. CERTIFICATIONS: heading, then one line of the provided certification links separated by ' · '.\n"
    "Hard rules:\n"
    "- INCLUDE EVERY EXPERIENCE from the base resume. Do not drop, merge, or omit any role.\n"
    "- Replace duties with specific, measurable achievements; cut generic filler; weave in truthful ATS keywords "
    "from the JD.\n"
    "- Do NOT use dashes or hyphens in your prose (rephrase). Keep proper nouns and technology names exact.\n"
    "- Use only the provided repository links for projects; never invent a URL; omit the link if no repo matches.\n"
    "- ANTI-FABRICATION: never add employers, titles, dates, degrees, or metrics not in the base resume, and do "
    "not claim a years-of-experience figure beyond what it states. Skills present in the base resume may be "
    "emphasized; do not invent new ones. Every line must trace to the base resume.\n"
    "LAYOUT: it MUST fit on ONE Letter page — be concise enough that everything fits. Compact, clean, "
    "professional, with embedded <style>: about 0.4in @page margin, ~9pt body font, ~1.2 line-height, small "
    "vertical margins between items, a system sans-serif stack, uppercase section headings with a thin bottom "
    "border, company/title rows laid out with flexbox (justify-content: space-between) so dates and locations "
    "align right, links in a dark teal. Output a COMPLETE HTML document starting with <!DOCTYPE html> and "
    "nothing else (no commentary, no markdown fences)."
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
    ) -> TailorResult:
        """Generate a one-page, JD-tailored HTML resume (see RESUME_RULES) suitable for PDF
        rendering. Project GitHub links are grounded in ``repos`` (only real matches embedded);
        ``contact`` is the fixed contact + certification link block injected per call."""
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
            html = (self._decide(self._build_resume_prompt(resume_text, job_text, company, role, repos or [], contact)) or "").strip()
        except Exception as exc:
            return TailorResult(company, role, ats, ok=False, package=f"Model error: {exc}")
        html = self._unfence(html)
        if "<" not in html or "html" not in html.lower():
            return TailorResult(company, role, ats, ok=False,
                                package="The model did not return an HTML document. Try again.")
        grounding = check_grounding([html], extract_resume_claims(resume_text))
        return TailorResult(company, role, ats, keywords[:40], ats["misses"][:20],
                            package=html, grounding=grounding, used_llm=True)

    def _build_resume_prompt(self, resume_text, job_text, company, role, repos, contact) -> str:
        target = " ".join(p for p in [role, ("at " + company) if company else ""] if p).strip() or "this role"
        repo_lines = "\n".join(f"  {r.get('name','')}: {r.get('url','')}" for r in repos if r.get("url")) or "  (none provided)"
        contact_block = contact.strip() or "(no contact block provided — use the links in the resume text)"
        return (
            f"{RESUME_RULES}\n\n"
            f"TARGET ROLE: {target}\n\n"
            f"CONTACT AND CERTIFICATION LINKS (render each as an <a href> element):\n{contact_block}\n\n"
            f"CANDIDATE GITHUB REPOSITORIES (match projects to these for <a href> links; never invent URLs):\n{repo_lines}\n\n"
            f"BASE RESUME (ground everything in this; do not fabricate):\n{resume_text[: self._max_resume]}\n\n"
            f"JOB DESCRIPTION:\n{job_text[: self._max_jd]}\n\n"
            "Return ONLY the complete one-page HTML document:"
        )

    @staticmethod
    def _unfence(text: str) -> str:
        """Strip a leading/trailing ``` fence if the model wrapped its output."""
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        return stripped.strip()

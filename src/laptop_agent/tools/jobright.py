from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

logger = logging.getLogger(__name__)

JOBRIGHT_BASE = "https://jobright.ai"
JOBRIGHT_LOGIN_URL = "https://jobright.ai/onboarding-v3/signup?login=true"

# Roles worth surfacing; titles must hit one of these and none of the excludes.
TARGET_KEYWORDS = [
    "software engineer", "software developer", "software development",
    "backend engineer", "backend developer", "server-side",
    "machine learning", "ml engineer", "ai engineer", "applied ai", "applied ml",
    "deep learning", "nlp engineer", "natural language", "computer vision",
    "research engineer", "research scientist", "applied scientist",
    "ml researcher", "ai researcher",
    "data engineer", "data scientist", "analytics engineer", "data platform",
    "platform engineer", "infrastructure engineer", "mlops", "llmops",
    "devops engineer", "site reliability", "sre", "cloud engineer",
    "fullstack", "full stack", "full-stack",
    "python engineer", "python developer",
    "solutions architect",
]
EXCLUDE_KEYWORDS = [
    "staff engineer", "principal engineer",
    "director", "vp of", "vp,", "senior staff",
    "manager,", "head of", "chief",
    "sales engineer", "support engineer", "customer success",
]


def is_relevant(title: str) -> bool:
    t = (title or "").lower()
    if not any(kw in t for kw in TARGET_KEYWORDS):
        return False
    return not any(kw in t for kw in EXCLUDE_KEYWORDS)


def parse_jobright_item(item: dict) -> dict | None:
    """Parse a Jobright recommendation item ({jobResult, companyResult, ...})."""
    jr = item.get("jobResult", {})
    cr = item.get("companyResult", {})
    if not isinstance(jr, dict) or not isinstance(cr, dict):
        return None

    title = str(jr.get("jobTitle") or jr.get("jobNlpTitle") or "").strip()
    company = str(cr.get("companyName") or "").strip()
    if not title or len(title) < 4 or not company:
        return None

    job_id = str(jr.get("jobId") or "").strip()
    job_url = (
        str(jr.get("originalUrl") or jr.get("applyLink") or "").strip()
        or (f"{JOBRIGHT_BASE}/jobs/{job_id}" if job_id else "")
    )

    location = str(jr.get("jobLocation") or cr.get("companyLocation") or "").strip()
    if jr.get("isRemote") or str(jr.get("workModel", "")).lower() == "remote":
        if "remote" not in location.lower():
            location = f"{location} (Remote)" if location else "Remote"

    description = str(jr.get("jobSummary") or "").strip()
    responsibilities = jr.get("coreResponsibilities", [])
    if isinstance(responsibilities, list) and responsibilities:
        description += "\n\nResponsibilities:\n" + "\n".join(f"- {r}" for r in responsibilities[:10])

    return {
        "title": title,
        "company": company,
        "location": location,
        "job_url": job_url,
        "date_posted": str(jr.get("publishTime") or jr.get("publishTimeDesc") or "").strip(),
        "job_id_ext": job_id,
        "description": description[:8000],
    }


def try_parse_job_object(obj: object) -> dict | None:
    """Return a normalised job dict if obj looks like a generic job posting, else None."""
    if not isinstance(obj, dict) or len(obj) < 3:
        return None

    title = (
        obj.get("title") or obj.get("jobTitle") or obj.get("job_title") or
        obj.get("positionTitle") or obj.get("position") or obj.get("name") or ""
    )
    if not isinstance(title, str) or len(title) < 4 or len(title) > 200:
        return None

    company_raw = (
        obj.get("company") or obj.get("companyName") or obj.get("company_name") or
        obj.get("employer") or obj.get("organizationName") or obj.get("org") or ""
    )
    if isinstance(company_raw, dict):
        company = (
            company_raw.get("name") or company_raw.get("companyName") or
            company_raw.get("displayName") or ""
        )
    else:
        company = str(company_raw).strip()
    if not company or len(company) < 2:
        return None

    job_url = (
        obj.get("url") or obj.get("jobUrl") or obj.get("job_url") or
        obj.get("applyUrl") or obj.get("apply_url") or obj.get("link") or
        obj.get("externalUrl") or ""
    )
    if job_url and not str(job_url).startswith("http"):
        job_url = f"{JOBRIGHT_BASE}{job_url}"

    job_id_raw = obj.get("id") or obj.get("jobId") or obj.get("job_id") or ""
    if not job_url and job_id_raw:
        job_url = f"{JOBRIGHT_BASE}/jobs/{job_id_raw}"

    location_raw = (
        obj.get("location") or obj.get("jobLocation") or obj.get("city") or
        obj.get("locationName") or obj.get("workLocation") or ""
    )
    if isinstance(location_raw, dict):
        location = (
            location_raw.get("city") or location_raw.get("name") or
            location_raw.get("displayName") or ""
        )
    else:
        location = str(location_raw).strip()
    is_remote = (
        obj.get("isRemote") or obj.get("remote") or
        "remote" in str(obj.get("workType", "")).lower() or
        "remote" in str(obj.get("locationType", "")).lower()
    )
    if is_remote and "remote" not in location.lower():
        location = f"{location} (Remote)" if location else "Remote"

    description = str(
        obj.get("description") or obj.get("jobDescription") or
        obj.get("job_description") or obj.get("details") or ""
    )

    return {
        "title": title.strip(),
        "company": company.strip(),
        "location": location,
        "job_url": str(job_url).strip(),
        "date_posted": str(
            obj.get("datePosted") or obj.get("date_posted") or obj.get("postedAt") or
            obj.get("posted_at") or obj.get("createdAt") or obj.get("publishDate") or ""
        ),
        "job_id_ext": str(job_id_raw).strip(),
        "description": description[:8000],
    }


def extract_jobs_from_api_data(data: object) -> list[dict]:
    """Recursively walk any JSON structure, extracting job-like objects."""
    if isinstance(data, list):
        jobs: list[dict] = []
        for item in data:
            if isinstance(item, dict) and "jobResult" in item and "companyResult" in item:
                job = parse_jobright_item(item)
                if job:
                    jobs.append(job)
                continue
            job = try_parse_job_object(item)
            if job:
                jobs.append(job)
            elif isinstance(item, (dict, list)):
                jobs.extend(extract_jobs_from_api_data(item))
        return jobs
    if isinstance(data, dict):
        if "jobResult" in data and "companyResult" in data:
            job = parse_jobright_item(data)
            return [job] if job else []
        job = try_parse_job_object(data)
        if job:
            return [job]
        jobs = []
        for value in data.values():
            if isinstance(value, (list, dict)):
                jobs.extend(extract_jobs_from_api_data(value))
        return jobs
    return []


def deduplicate(listings: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for j in listings:
        key = f"{j.get('title', '').lower()}|{j.get('company', '').lower()}"
        if key not in seen and j.get("title"):
            seen.add(key)
            unique.append(j)
    return unique


class JobrightTool:
    """Scrapes Jobright.ai recommendations into normalized lead dicts. Ported from the
    Jobright ingestion agent of the job-agent--Jarvis repo, stripped of its database and
    websocket coupling. The browser drive lives behind the optional ``browser`` extra; the
    parsing helpers above are pure and unit-tested offline. Login reuses a persisted
    storage-state session (a credential — keep ``session_path`` private) and falls back to
    email/password when present."""

    def __init__(
        self,
        approval_gate: ApprovalGate,
        *,
        email: str = "",
        password: str = "",
        session_path: Path | None = None,
        headless: bool = True,
    ) -> None:
        self.approval_gate = approval_gate
        self.email = email or ""
        self.password = password or ""
        self.session_path = session_path
        self.headless = headless

    async def pull(self, max_scrolls: int = 120) -> ToolResult:
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            return ToolResult.failure(
                "Jobright pull requires the browser extra: pip install playwright && playwright install chromium"
            )

        if not self._has_session() and not (self.email and self.password):
            return ToolResult.failure(
                "No Jobright session and no credentials. Set JOBRIGHT_EMAIL / JOBRIGHT_PASSWORD in .env, "
                "or provide a saved session file."
            )

        self.approval_gate.require(
            ApprovalRequest(
                action="Pull job leads from Jobright.ai",
                risk=RiskLevel.MEDIUM,
                reason=(
                    "Read-only scrape: logs into a third-party site with a stored session/credential "
                    "and reads recommendations (no data is submitted). Subject to Jobright's terms of service."
                ),
            )
        )

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            try:
                context = await self._make_context(browser)
                page = await context.new_page()
                if not await self._login(page, context):
                    return ToolResult.failure(
                        "Jobright login failed. Check JOBRIGHT_EMAIL / JOBRIGHT_PASSWORD or refresh the saved session."
                    )
                listings = await self._scrape_with_interception(page, max_scrolls)
                if not listings:
                    listings = await self._scrape_via_dom(page)
            finally:
                await browser.close()

        leads = [j for j in deduplicate(listings) if j.get("title") and j.get("company") and is_relevant(j["title"])]
        return ToolResult.success(
            f"Scraped {len(leads)} relevant Jobright lead(s).",
            leads=leads,
            scraped=len(listings),
        )

    # --- browser drive (live; not unit-tested) -------------------------------

    def _has_session(self) -> bool:
        return bool(self.session_path and self.session_path.exists())

    async def _make_context(self, browser):  # pragma: no cover - needs a live browser
        kwargs: dict[str, object] = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1280, "height": 900},
            "locale": "en-US",
        }
        if self._has_session():
            kwargs["storage_state"] = str(self.session_path)
        return await browser.new_context(**kwargs)

    async def _save_session(self, context) -> None:  # pragma: no cover - needs a live browser
        if not self.session_path:
            return
        try:
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(self.session_path))
        except Exception as exc:
            logger.warning("Could not save Jobright session: %s", exc)

    async def _login(self, page, context) -> bool:  # pragma: no cover - needs a live browser
        try:
            await page.goto(JOBRIGHT_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            if any(x in page.url for x in ("/jobs", "/dashboard", "/home")):
                return True

            if not (self.email and self.password):
                return False

            for sel in ("input[type='email']", "input[name='email']", "input[placeholder*='email' i]", "#email"):
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.fill(self.email)
                    break
            else:
                return False

            for sel in ("input[type='password']", "input[name='password']", "#password"):
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.fill(self.password)
                    break
            else:
                return False

            for sel in ("button:has-text('SIGN IN')", "button:has-text('Sign In')", "button:has-text('Log In')"):
                el = page.locator(sel).first
                if await el.count() > 0:
                    text = (await el.inner_text()).strip().lower()
                    if any(bad in text for bad in ("sign up", "join", "register")):
                        continue
                    await el.click()
                    break
            else:
                await page.keyboard.press("Enter")

            try:
                await page.wait_for_url(
                    lambda u: "onboarding" not in u.lower() and "signup" not in u.lower(),
                    timeout=15000,
                )
            except Exception:
                await page.wait_for_timeout(8000)

            if "onboarding" in page.url.lower() or "signup" in page.url.lower():
                return False
            await self._save_session(context)
            return True
        except Exception as exc:
            logger.warning("Jobright login error: %s", exc)
            return False

    async def _scrape_with_interception(self, page, max_scrolls: int) -> list[dict]:  # pragma: no cover - needs a live browser
        captured: list[dict] = []
        seen_urls: set[str] = set()

        async def on_response(response):
            try:
                if response.status != 200 or "json" not in response.headers.get("content-type", ""):
                    return
                url = response.url
                path = urlparse(url).path.lower()
                if any(path.endswith(ext) for ext in (
                    ".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2", ".ttf", ".ico", ".webp",
                )):
                    return
                if any(x in url for x in (
                    "/static/", "/favicon", "__nextjs", "analytics", "sentry",
                    "google-analytics", "facebook", "hotjar", "mixpanel", "segment.io", "amplitude", "clarity.ms",
                )):
                    return
                if url in seen_urls:
                    return
                seen_urls.add(url)
                try:
                    data = await response.json()
                except Exception:
                    return
                captured.extend(extract_jobs_from_api_data(data))
            except Exception:
                return

        page.on("response", on_response)
        try:
            if "/jobs" in page.url:
                await page.reload(wait_until="load", timeout=30000)
            else:
                await page.goto(f"{JOBRIGHT_BASE}/jobs", wait_until="load", timeout=30000)
            await page.wait_for_timeout(5000)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            stall, last = 0, 0
            for _ in range(max_scrolls):
                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(600)
                if len(captured) > last:
                    stall, last = 0, len(captured)
                else:
                    stall += 1
                if stall >= 15:
                    break
            await page.wait_for_timeout(3000)
        except Exception as exc:
            logger.warning("Jobright interception error: %s", exc)
        finally:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass
        return deduplicate(captured)

    async def _scrape_via_dom(self, page) -> list[dict]:  # pragma: no cover - needs a live browser
        try:
            await page.goto(f"{JOBRIGHT_BASE}/jobs", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(4000)
            for _ in range(6):
                await page.evaluate("window.scrollBy(0, 700)")
                await page.wait_for_timeout(700)
            result = await page.evaluate(
                """() => {
                    const SEL = ['[role="listitem"]','article','[class*="job-card"]','[class*="JobCard"]',
                        '[class*="jobCard"]','[class*="card"]','li:has(a[href*="/job"])','[data-job-id]'];
                    let cards = [];
                    for (const s of SEL) {
                        try {
                            const f = Array.from(document.querySelectorAll(s)).filter(e => e.textContent.trim().length > 20);
                            if (f.length >= 2) { cards = f; break; }
                        } catch(e) {}
                    }
                    const jobs = [];
                    for (const card of cards.slice(0, 80)) {
                        let title = '';
                        for (const h of card.querySelectorAll('h1,h2,h3,h4,[class*="title"],[class*="role"],[class*="position"]')) {
                            const t = h.textContent.trim().replace(/\\s+/g, ' ');
                            if (t.length >= 5 && t.length <= 120) { title = t; break; }
                        }
                        let company = '';
                        for (const el of card.querySelectorAll('[class*="company"],[class*="employer"],[class*="brand"],[class*="org"]')) {
                            const t = el.textContent.trim().replace(/\\s+/g, ' ');
                            if (t && t !== title && t.length < 80) { company = t; break; }
                        }
                        let location = '';
                        for (const el of card.querySelectorAll('[class*="location"],[class*="city"],[class*="remote"]')) {
                            const t = el.textContent.trim().replace(/\\s+/g, ' ');
                            if (t) { location = t; break; }
                        }
                        const link = card.tagName === 'A' ? card : (card.querySelector('a[href*="/job"]') || card.querySelector('a'));
                        if (title) jobs.push({title, company, location, job_url: link ? link.href : '', date_posted: ''});
                    }
                    return jobs;
                }"""
            )
            return [
                {**j, "job_id_ext": "", "description": ""}
                for j in (result or [])
                if j.get("title") and j.get("company")
            ]
        except Exception as exc:
            logger.warning("Jobright DOM fallback error: %s", exc)
            return []

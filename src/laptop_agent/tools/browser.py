from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult


@dataclass(frozen=True)
class FormField:
    index: int
    tag: str
    field_type: str
    name: str
    field_id: str
    label: str
    placeholder: str
    required: bool


class BrowserAutomationTool:
    def __init__(self, approval_gate: ApprovalGate) -> None:
        self.approval_gate = approval_gate

    async def inspect_page(self, url: str) -> ToolResult:
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            return ToolResult.failure("Browser automation requires: pip install playwright && playwright install chromium")

        self.approval_gate.require(
            ApprovalRequest(
                action=f"Inspect page with browser automation: {url}",
                risk=RiskLevel.MEDIUM,
                reason="Browser automation loads a web page and can interact with active sessions.",
            )
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=False)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            title = await page.title()
            text = await page.locator("body").inner_text(timeout=5000)
            await browser.close()
        return ToolResult.success("Inspected page.", url=url, title=title, text=text[:4000])

    async def inspect_forms(self, url: str) -> ToolResult:
        normalized = self._normalize_url(url)
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            return ToolResult.failure("Form inspection requires: pip install playwright && playwright install chromium")

        self.approval_gate.require(
            ApprovalRequest(
                action=f"Inspect forms with browser automation: {normalized}",
                risk=RiskLevel.MEDIUM,
                reason="Browser automation loads a web page and reads form fields. It does not submit anything.",
            )
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(normalized, wait_until="domcontentloaded")
            title = await page.title()
            fields = await page.locator("input, textarea, select").evaluate_all(
                """
                elements => elements.map((element, index) => {
                  const labelFor = element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`) : null;
                  const wrappingLabel = element.closest("label");
                  const ariaLabel = element.getAttribute("aria-label") || "";
                  return {
                    index,
                    tag: element.tagName.toLowerCase(),
                    field_type: (element.getAttribute("type") || element.tagName).toLowerCase(),
                    name: element.getAttribute("name") || "",
                    field_id: element.id || "",
                    label: (labelFor?.innerText || wrappingLabel?.innerText || ariaLabel || "").trim(),
                    placeholder: element.getAttribute("placeholder") || "",
                    required: element.required || element.getAttribute("aria-required") === "true",
                  };
                })
                """
            )
            await browser.close()

        visible_fields = [field for field in fields if self._is_user_field(field)]
        return ToolResult.success(
            f"Inspected {len(visible_fields)} form fields.",
            url=normalized,
            title=title,
            fields=visible_fields,
        )

    async def preview_form_fill(self, url: str, profile: dict[str, object]) -> ToolResult:
        if not profile:
            return ToolResult.failure("No profile details stored. Use: remember name = Your Name")
        normalized = self._normalize_url(url)
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Create form fill preview: {normalized}",
                risk=RiskLevel.HIGH,
                reason="This prepares personal data for a web form review. It does not type, click, or submit.",
                preview="\n".join(f"{key}: {value}" for key, value in profile.items()),
            )
        )
        inspection = await self._inspect_forms_without_extra_approval(normalized)
        if not inspection.ok:
            return ToolResult.failure(
                "Could not inspect forms for fill preview.",
                inspection=inspection.message,
                url=normalized,
            )
        fields = inspection.data.get("fields", [])
        field_mappings = self.map_profile_to_fields(fields, profile)
        fill_preview = self.build_fill_preview(fields, field_mappings)
        warnings = self._fill_preview_warnings(fill_preview)
        return ToolResult.success(
            "Prepared form fill preview. No fields were changed and nothing was submitted.",
            url=normalized,
            title=inspection.data.get("title"),
            fields=fields,
            field_mappings=field_mappings,
            fill_preview=fill_preview,
            warnings=warnings,
            next_steps=[
                "Review every mapped value",
                "Add missing required profile details",
                "Use a future explicit fill command only after approval",
                "Use a future explicit submit command only after final approval",
            ],
        )

    async def prepare_job_application(self, url_or_description: str, profile: dict[str, object]) -> ToolResult:
        if not profile:
            return ToolResult.failure("No profile details stored. Use: remember name = Your Name")
        url = self._extract_url(url_or_description)
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Prepare job application workflow: {url_or_description}",
                risk=RiskLevel.HIGH,
                reason="Job applications use personal data. This prepares a review map only and never submits.",
                preview="\n".join(f"{key}: {value}" for key, value in profile.items()),
            )
        )
        fields: list[dict[str, object]] = []
        field_mappings: list[dict[str, object]] = []
        fill_preview: list[dict[str, object]] = []
        warnings: list[str] = []
        inspection_message = "No URL detected; created a workflow plan only."
        if url:
            inspection = await self._inspect_forms_without_extra_approval(url)
            inspection_message = inspection.message
            if inspection.ok:
                fields = inspection.data.get("fields", [])
                field_mappings = self.map_profile_to_fields(fields, profile)
                fill_preview = self.build_fill_preview(fields, field_mappings)
                warnings = self._fill_preview_warnings(fill_preview)

        return ToolResult.success(
            "Prepared job application review package. Submission is intentionally not automated.",
            url=url,
            source=url_or_description,
            profile_keys=sorted(profile.keys()),
            form_inspection=inspection_message,
            fields=fields,
            field_mappings=field_mappings,
            fill_preview=fill_preview,
            warnings=warnings,
            next_steps=[
                "Review extracted fields",
                "Confirm mapped profile values",
                "Add any missing required details",
                "Review fill-preview selectors before any future typing action",
                "Require final approval before any future submit action",
            ],
        )

    async def _inspect_forms_without_extra_approval(self, url: str) -> ToolResult:
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            return ToolResult.failure("Form inspection skipped. Install browser extras and run: playwright install chromium")

        normalized = self._normalize_url(url)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(normalized, wait_until="domcontentloaded")
            title = await page.title()
            fields = await page.locator("input, textarea, select").evaluate_all(
                """
                elements => elements.map((element, index) => {
                  const labelFor = element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`) : null;
                  const wrappingLabel = element.closest("label");
                  const ariaLabel = element.getAttribute("aria-label") || "";
                  return {
                    index,
                    tag: element.tagName.toLowerCase(),
                    field_type: (element.getAttribute("type") || element.tagName).toLowerCase(),
                    name: element.getAttribute("name") || "",
                    field_id: element.id || "",
                    label: (labelFor?.innerText || wrappingLabel?.innerText || ariaLabel || "").trim(),
                    placeholder: element.getAttribute("placeholder") || "",
                    required: element.required || element.getAttribute("aria-required") === "true",
                  };
                })
                """
            )
            await browser.close()
        visible_fields = [field for field in fields if self._is_user_field(field)]
        return ToolResult.success(f"Inspected {len(visible_fields)} form fields.", url=normalized, title=title, fields=visible_fields)

    @staticmethod
    def map_profile_to_fields(fields: list[dict[str, object]], profile: dict[str, object]) -> list[dict[str, object]]:
        normalized_profile = {BrowserAutomationTool._normalize_key(key): value for key, value in profile.items()}
        mappings: list[dict[str, object]] = []
        for field in fields:
            field_text = " ".join(
                str(field.get(key, ""))
                for key in ("name", "field_id", "label", "placeholder")
                if field.get(key)
            )
            candidates = BrowserAutomationTool._profile_candidates(field_text)
            matched_key = next((candidate for candidate in candidates if candidate in normalized_profile), None)
            mappings.append(
                {
                    "field_index": field.get("index"),
                    "field_label": field.get("label") or field.get("name") or field.get("field_id"),
                    "required": bool(field.get("required")),
                    "matched_profile_key": matched_key,
                    "value_preview": str(normalized_profile[matched_key]) if matched_key else None,
                    "status": "mapped" if matched_key else "needs_review",
                }
            )
        return mappings

    @staticmethod
    def build_fill_preview(fields: list[dict[str, object]], mappings: list[dict[str, object]]) -> list[dict[str, object]]:
        fields_by_index = {field.get("index"): field for field in fields}
        preview: list[dict[str, object]] = []
        for mapping in mappings:
            field = fields_by_index.get(mapping.get("field_index"), {})
            selector = BrowserAutomationTool._selector_for_field(field)
            value_preview = mapping.get("value_preview")
            preview.append(
                {
                    "field_index": mapping.get("field_index"),
                    "selector": selector,
                    "field_label": mapping.get("field_label"),
                    "field_type": field.get("field_type"),
                    "required": bool(mapping.get("required")),
                    "matched_profile_key": mapping.get("matched_profile_key"),
                    "value_preview": value_preview,
                    "would_fill": bool(selector and value_preview),
                    "status": "ready" if selector and value_preview else "needs_review",
                }
            )
        return preview

    @staticmethod
    def _profile_candidates(text: str) -> list[str]:
        normalized = BrowserAutomationTool._normalize_key(text)
        candidates = [normalized]
        synonym_groups = {
            "name": ["name", "full_name", "fullname", "candidate_name"],
            "first_name": ["first_name", "firstname", "given_name"],
            "last_name": ["last_name", "lastname", "family_name", "surname"],
            "email": ["email", "email_address", "e_mail"],
            "phone": ["phone", "phone_number", "mobile", "telephone"],
            "address": ["address", "street_address", "mailing_address"],
            "city": ["city"],
            "state": ["state", "province", "region"],
            "zip": ["zip", "zipcode", "postal_code", "postcode"],
            "linkedin": ["linkedin", "linkedin_url", "linkedin_profile"],
            "github": ["github", "github_url", "github_profile"],
            "portfolio": ["portfolio", "website", "personal_website"],
            "resume": ["resume", "cv"],
            "cover_letter": ["cover_letter", "coverletter"],
        }
        for canonical, synonyms in synonym_groups.items():
            if any(synonym in normalized for synonym in synonyms):
                candidates.extend([canonical, *synonyms])
        seen: set[str] = set()
        return [candidate for candidate in candidates if not (candidate in seen or seen.add(candidate))]

    @staticmethod
    def _is_user_field(field: dict[str, object]) -> bool:
        field_type = str(field.get("field_type", "")).lower()
        return field_type not in {"hidden", "submit", "button", "reset", "image"}

    @staticmethod
    def _fill_preview_warnings(fill_preview: list[dict[str, object]]) -> list[str]:
        warnings: list[str] = []
        missing_required = [
            str(item.get("field_label") or item.get("selector") or item.get("field_index"))
            for item in fill_preview
            if item.get("required") and not item.get("would_fill")
        ]
        if missing_required:
            warnings.append("Missing required values for: " + ", ".join(missing_required))
        unmappable = [item for item in fill_preview if not item.get("selector")]
        if unmappable:
            warnings.append(f"{len(unmappable)} field(s) need manual review because no stable selector was found.")
        return warnings

    @staticmethod
    def _selector_for_field(field: dict[str, object]) -> str | None:
        field_id = str(field.get("field_id") or "").strip()
        name = str(field.get("name") or "").strip()
        tag = str(field.get("tag") or "input").strip().lower()
        placeholder = str(field.get("placeholder") or "").strip()
        if field_id:
            return f"#{BrowserAutomationTool._css_escape(field_id)}"
        if name:
            return f'{tag}[name="{BrowserAutomationTool._css_attr_escape(name)}"]'
        if placeholder:
            return f'{tag}[placeholder="{BrowserAutomationTool._css_attr_escape(placeholder)}"]'
        return None

    @staticmethod
    def _extract_url(text: str) -> str | None:
        match = re.search(r"https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?", text, re.IGNORECASE)
        return match.group(0) if match else None

    @staticmethod
    def _normalize_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme:
            return url
        return "https://" + url

    @staticmethod
    def _normalize_key(value: object) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")

    @staticmethod
    def _css_escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("#", "\\#").replace(".", "\\.")

    @staticmethod
    def _css_attr_escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

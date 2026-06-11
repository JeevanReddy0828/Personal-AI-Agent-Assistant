from __future__ import annotations

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult


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

    async def prepare_job_application(self, url: str, profile: dict[str, object]) -> ToolResult:
        if not profile:
            return ToolResult.failure("No profile details stored. Use: remember name = Your Name")
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Prepare job application workflow: {url}",
                risk=RiskLevel.HIGH,
                reason="Job applications submit personal data. This MVP prepares and reviews only.",
                preview="\n".join(f"{key}: {value}" for key, value in profile.items()),
            )
        )
        return ToolResult.success(
            "Prepared job application plan. Submission is intentionally not automated in this MVP.",
            url=url,
            profile_keys=sorted(profile.keys()),
            next_steps=[
                "Open posting",
                "Extract form fields",
                "Map stored profile details",
                "Show filled preview",
                "Ask for final submit approval",
            ],
        )

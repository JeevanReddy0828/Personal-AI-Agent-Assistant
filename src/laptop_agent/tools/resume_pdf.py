from __future__ import annotations

from pathlib import Path

from laptop_agent.tools.base import ToolResult


async def render_html_to_pdf(html: str, out_path: Path) -> ToolResult:
    """Render a self-contained HTML resume to a Letter-size PDF via headless Chromium.

    Uses the same Playwright browser as the scraper (the ``browser`` extra), so no LaTeX
    engine is required. Returns the output path in ToolResult.data on success."""
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        return ToolResult.failure(
            "PDF export requires the browser extra: pip install playwright && playwright install chromium"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = await browser.new_page()
                await page.set_content(html, wait_until="networkidle")
                await page.pdf(
                    path=str(out_path),
                    format="Letter",
                    print_background=True,
                    margin={"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"},
                )
            finally:
                await browser.close()
    except Exception as exc:  # pragma: no cover - depends on a live browser
        return ToolResult.failure(f"PDF render failed: {exc}")
    return ToolResult.success(f"Wrote {out_path.name}.", path=str(out_path))

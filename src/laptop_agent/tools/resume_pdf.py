from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile

from laptop_agent.tools.base import ToolResult


async def render_html_to_pdf(html: str, out_path: Path, single_page: bool = True) -> ToolResult:
    """Render HTML to a Letter PDF with no network access.

    ``single_page`` keeps the resume contract — publish only a verified one-pager — and is
    off for ordinary documents, which are expected to run long."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ToolResult.failure("PDF export requires: pip install playwright && playwright install chromium")
    temporary = None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(java_script_enabled=False)
                await page.route("**/*", lambda route: route.abort())
                await page.set_content(html, wait_until="domcontentloaded")
                # Never silently crop, shrink to unreadable text, or publish extra pages.
                pdf = await page.pdf(format="Letter", print_background=True,
                                     prefer_css_page_size=True,
                                     margin={"top": "0.5in", "bottom": "0.5in", "left": "0.7in", "right": "0.7in"})
                pages = len(re.findall(rb"/Type\s*/Page\b", pdf))
                if single_page and pages != 1:
                    return ToolResult.failure(f"Resume needs {pages or 'an unknown number of'} pages. Shorten its source excerpts and export again; the previous file was preserved.", pages=pages)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(suffix=".pdf", dir=out_path.parent)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(pdf)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, out_path)
            finally:
                await browser.close()
    except Exception as exc:
        return ToolResult.failure(f"PDF render failed: {exc}")
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return ToolResult.success(f"Wrote {out_path.name} ({pages} page(s)).", path=str(out_path), pages=pages)

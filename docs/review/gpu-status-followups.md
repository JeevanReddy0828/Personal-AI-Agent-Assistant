# GPU status follow-up — 2026-10-02

A null adapter name stopped the drawer refresh before the connection row rendered.
Blank names left the adapter unidentified. The drawer and connection row now use a
trimmed adapter name or a numbered GPU fallback. VRAM rows include the full adapter
name, escaped for HTML, so each memory percentage can be identified on its own.

Validation: two isolated Chromium regressions pass, covering null, empty, whitespace
and non-string names, multiple adapters, and HTML-like names. Reverting the connection
fallback, VRAM identity, or VRAM escaping independently fails the relevant regression.
Ten page-asset checks pass. The branch starts at main 0ce6847; #175 separately improves
utilization labels. Both changes touch the drawer's compact loadMetrics line; retain
#175's gpuLabel and this change's adapter-labelled VRAM and safe connection name when
integrating them. No metrics backend or health behavior changes.

## Integration with #175 — 2026-10-05

Merged main 4b41cdd into this branch with Jeevan's relayed authorization. The original
integration watch-out above is now resolved: one plain-text `gpuAdapterName` helper
provides trimmed names or a 1-based GPU fallback. `gpuLabel` only escapes that name
and adds the optional `(3D)` suffix. Overview and drawer utilization rows use that
label; VRAM rows and the connection status reuse the same adapter name. This preserves
#175's distinct utilization labels and #179's missing-name handling and VRAM identity.

All 91 opt-in Chromium checks pass. Changing the fallback back to 0-based makes the
Overview/drawer identity regression fail; the source was restored byte-for-byte afterward.
Existing desktop/mobile screenshot artifacts were preserved during the browser run.

The integrated tracked-file suite also passed: 1,783 tests, 89 optional skips. This used
the existing isolated runner, with the prefix-fuzz metrics call stubbed to avoid repeated
hardware probes; the opt-in browser suite was run separately as reported above.

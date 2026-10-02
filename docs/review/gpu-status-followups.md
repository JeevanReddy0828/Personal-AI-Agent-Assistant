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

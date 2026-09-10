# ERRORS.md — failure log

Mistakes and their root cause + fix, so they don't recur. Append after any real bug or
near-miss. Newest first.

## Live-testing pass (2026-09-09)

- **Chat brain pointed at retired models.** Every chat failed: `meta/llama-3.1-8b-instruct`
  and `nvidia/llama-3.3-nemotron-super-49b-v1` returned HTTP **410 Gone** (NVIDIA EOL'd them
  2026-08-26). A new key can't revive a retired model. Fix: probe `/models` + a ping per
  candidate to find models the account can actually call (404 = "not for this account"), then
  point `.env` tiers at provisioned ones (`nvidia/nemotron-3-super-120b-a12b`). Model IDs live
  in `.env` (per-user), never in code. Rule: when "the model is down", check for 410/404 first.
- **Chronic "fast model busy" note.** The fast tier (`nemotron-3-nano-omni-30b …reasoning`) was
  a slow reasoning model, so nearly every reply timed out and degraded to smart → the honest
  "busy" note showed on *every* answer. Fix: a fast tier must be reliably fast; pointed it at
  `super-120b` (0.4–5 s), which is faster than the "fast" model was.
- **Image attachment dead-ended on OCR.** A bare image upload was composed as `process file`,
  which the file processor maps to OCR (Tesseract) → "Tesseract not found" even though a vision
  model was configured. Fix: route bare image uploads to `describe image <path>` (vision-first,
  OCR fallback) in `webui._bare_attachment_command`.
- **Multi-query message misrouted to IMAP + raw bytes error.** A message containing "summarize"
  and "email" in *unrelated* sentences matched the digest trigger (both words anywhere) and hit
  IMAP, whose `imaplib.IMAP4.error` propagated as a raw `b'[AUTHENTICATIONFAILED] …'`. Fix:
  require the summarise verb *near* the inbox noun on one line; catch `IMAP4.error`/`OSError` in
  `search_inbox` and return a friendly `ToolResult.failure` with an app-password hint.
- **Replies cut off mid-sentence.** The advisor brain and streaming chat both capped at 900
  tokens, truncating long comparisons/designs. Fix: advisor `_build_agent_brain(answer_max_tokens=4000)`,
  `stream_answer` default 900 → 2048.
- **"Connection error: Reload J.A.R.V.I.S".** The per-process API token rotates on restart, so an
  open tab's token goes stale → same-origin 403. Fix: the page reloads once on a same-origin 403
  to pick up a fresh token (5 s guard against loops).
- **Logic/puzzles rambled.** Jug puzzles, "show your reasoning", "find the flaw" scored as simple
  chat and answered on the non-reasoning tier, talking in circles. Fix: `_complexity` escalates
  those cues to the reasoning (ultra) tier so the model thinks first. Tradeoff: slower — the new
  model+latency line makes that visible.
- **Voice failures were silent.** A blocked/absent mic or offline speech service only logged to a
  hidden debug HUD, so voice "just didn't listen". Fix: `rec.onerror` surfaces an actionable
  message (allow mic / no mic / offline) and exits voice mode cleanly.
- **C++/C# ATS scored 0.** `\bc\+\+\b`/`\bc#\b` never match (a `\b` can't sit after `+`/`#`), and
  the `len<3` filter dropped `c#`. Fix: a token-boundary matcher over `[A-Za-z0-9+#]` (not `.`, so
  "AWS." still matches), and keep short symbol skills. Also: malformed model JSON crashed the
  resume renderer (non-dict list entries) — render now skips non-dict/non-list shapes and
  `tailor_resume` rejects experiences with no usable objects.
- **Review batch left the branch red.** Codex's uncommitted batch over-corrected multi-task `ok`
  to `all(...)` (broke partial-success tests) and missed a token header on one webui test. Fix:
  `ok = any-succeeded` (all-failed → False, per user), and add the missing `X-Jarvis-Token`.

- **Nearly rebuilt an existing feature.** Started building an "inbox digest" that already
  existed on `main` (`_email_digest`). Root cause: trusted a stale backlog note over the
  code. Fix/rule: `git grep` the repo for a feature before building it.
- **window_fx targeted any window by title.** `FindWindow("J.A.R.V.I.S")` matched and
  modified an unrelated third-party app with the same title. Fix: enumerate only top-level
  windows owned by *our* process AND matching the title.
- **Compact layout collapsed the UI.** Hid two grid columns with `display:none` while
  keeping a `0 0 1fr` track list, so CSS grid auto-placed the chat column into a 0-width
  track. Fix: collapse to a single `minmax(0,1fr)` track and span the chat column.
- **Ported keyword extractor kept trailing dots.** `"aws."`/`"kubernetes."` tokens broke
  whole-word ATS matching. Fix: strip stray leading/trailing dots, keep `c++`/`c#`/`node.js`.

- **Review regressions (2026-09-08).** Unit-only coverage missed executable Markdown
  attributes, cross-origin mutations, duplicate due jobs, chat ownership and mobile
  overflow. Keep security/concurrency/browser tests in CI. Browser conditions use CDP
  evaluation because string-based Playwright wait predicates can conflict with nonce CSP.
- **False resume confidence.** Lexical overlap approved invented employers and metrics.
  Export now requires exact source excerpts and grounded factual fields; generated
  application drafts explicitly require user review.

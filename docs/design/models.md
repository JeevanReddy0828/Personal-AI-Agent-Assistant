# Model tiers and the chat prompts

Tiers, reply length, failure classification, persistence of broken tiers, and what the chat tier is told.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

## LLM brain — tiered models

Configured via env / `.env` (auto-loaded by `config.py`). Pick by task complexity:
- `OPENAI_MODEL` — fast/simple (e.g. `meta/llama-3.1-8b-instruct`)
- `OPENAI_SMART_MODEL` — complex (`nvidia/llama-3.3-nemotron-super-49b-v1`)
- `OPENAI_ULTRA_MODEL` — very complex (`nvidia/nemotron-3-ultra-550b-a55b`)
- `OPENAI_VISION_MODEL` — screen/images (`meta/llama-3.2-11b-vision-instruct`)
- `OPENAI_IMAGE_MODEL` — text-to-image (`black-forest-labs/flux.2-klein-4b`), plus
  `OPENAI_IMAGE_KEY`, `OPENAI_IMAGE_BASE_URL` (default
  `https://ai.api.nvidia.com/v1/genai`) and an optional second model tried when the
  first is queued: `OPENAI_IMAGE_FALLBACK_MODEL` / `OPENAI_IMAGE_FALLBACK_KEY`. The
  model id is part of the **path**, not the body, and this is a **different host from
  chat** — never point `OPENAI_BASE_URL` at it, or every chat turn breaks. The fallback
  gets half the primary's timeout so a double failure doesn't double the wait.
  Measured on the free tier: klein answers in ~2s, `flux.1-schnell` times out at 90s on
  its own key, and `nemotron-3.5-lightning-30b-a3b` takes 6-14s per chat turn against
  0.5-1.5s for `nemotron-3-super-120b-a12b` — so super stays on the fast tier.
- `OPENAI_BASE_URL` (NVIDIA: `https://integrate.api.nvidia.com/v1`), `OPENAI_API_KEY`

The ultra tier is treated as an NVIDIA **reasoning** model: its provider is built with
`reasoning=True` so `answer()`/`stream_answer()` send `chat_template_kwargs.enable_thinking`
and read the separate streamed `reasoning_content` (kept internal — only the final answer is
surfaced). Routing and narration stay thinking-OFF for speed/clean JSON. `answer()` takes a
`max_tokens` param so long outputs (a full resume, 8000) aren't truncated at the chat default.

**How long a reply may run** (`OPENAI_MAX_OUTPUT_TOKENS`, `max_output_tokens` on the provider).
Streamed chat was capped at 2,048 tokens: a long answer stopped after 701 words, mid-table, and
the stream never read `finish_reason`, so nothing said it had been cut. Every NVIDIA model here
accepts `max_tokens` up to 65,536 (measured on super, ultra and vision), so on NVIDIA's host the
cap is 16,384 for streamed chat and 4,096 for `answer()` and agent turns. A **streamed** reply
that still ends on `length` says so, once, after a real answer only (a reply that was all hidden
reasoning stays empty, so the tier ladder still falls back), with an open code block closed
first; `answer()`, documents and agent turns still end silently on `length`, and no note ever
goes into a generated file. Elsewhere, including the OpenRouter fallback, the old caps stay unless
the variable is set: a cap the endpoint rejects is a 400, which marks the tier broken. A
non-streamed call waits `max(timeout, 15 + max_tokens/40)` s, capped at 300: at 66 tokens a
second a 4,096-token reply outlasts the fast tier's 45 s, and a timeout reads as busy. That is
the socket timeout of each attempt, not a total wall-clock deadline, and ultra keeps its 420 s
(Codex's review of #177).

**Never send `reasoning_budget`.** NVIDIA's endpoint moved to the V2 model runner and
rejects it — `HTTP 400 ValueError: thinking_token_budget is not yet supported by the V2
model runner` — on *every* ultra turn. Since an empty answer counts as congestion, the tier
degraded down on every request and health reported `ultra: degraded`, so an invalid
parameter was indistinguishable from a busy model. `OPENAI_REASONING_BUDGET` (default 16384)
now only sizes `max_tokens` locally, which is all it was ever needed for. Measured: with the
parameter every call 400s; without it the same question answers correctly and still returns
`reasoning_content`. `kimi-k3` rejects it too, with a different message, so this holds for
any future ultra model.

**`/v1/models` is a catalog, not an entitlement list.** It advertises 80 models on this
account and most are not callable: `llama3-chatqa-1.5-70b`, `codestral-22b`, `gemma-3-12b`,
`nemotron-4-340b`, `llama-3.1-nemotron-ultra-253b`, `nemotron-nano-3-30b`, `gemma-3-4b`,
`mistral-nemo-12b`, `minitron-8b`, `nemotron-51b`, `zamba2-7b`, `cosmos-reason2` and
`phi-3-vision` return **404**; `llama-3.2-90b-vision`, `llama-guard-4-12b` and
`mistral-nemotron` time out. Reachable and measured: `nemotron-3-super-120b` 1.7s,
`nemotron-3-ultra-550b` 20s, `nemotron-parse` 2.6s, `nemotron-3.5-content-safety` 0.2s,
`kimi-k3` 2.9s short / 61s hard, `deepseek-v4-pro` 10–21s. Call a model before wiring it in.
There are **no rerankers** on this account, and `riva-translate-4b-instruct-v2` answers in
0.5s but ignores its target language through this endpoint (four conventions produced
Japanese, Russian, an echoed tag and Dutch for a Telugu request) — it needs Riva gRPC like
Parakeet does. Pace live measurements ~12s apart: twelve turns back to back trip the 60s
degradation cooldown on all four tiers, after which `_route` stops consulting the LLM and
the measurement describes the throttle instead of the change.

Chat escalates fast→smart→ultra by `_complexity`, and **degrades gracefully**: if a
higher tier is congested/unreachable (its `answer`/`stream_answer` yields nothing)
the orchestrator falls back to the next tier down, tags the reply
(`degraded=True` in data, plus `planner.requested_model` vs `planner.model`) with a
short "_my smart model was busy_" note, and records the outcome in
`orchestrator.model_status` (`model_status.py`, thread-safe per-tier ok/degraded).
`health.system_health` surfaces this as `llm.tiers` + `llm.degraded_tier`, and the
web pill shows "smart/ultra model busy" while the fast tier stays healthy.

**A tier that is loaded and a tier that is misconfigured are different facts.** The whole
fallback ladder used to decide from `bool(reply)`, and a retired model id (HTTP 410), a
rejected key (401), a model the account cannot call (404), a refused parameter (400) and a
genuinely overloaded endpoint (503) all arrive as the same empty reply. So a permanent
misconfiguration was retried every 60s forever and reported as *busy* - advice to wait, for
something that never recovers. ERRORS.md records that costing real time twice.
`classify_failure` splits them: `DEGRADED` (429/503/timeout/network, 60s cooldown) from
`BROKEN` (400/401/403/404/410/422, 900s, and wording that names the model to change).
`ModelStatus.record(tier, ok, reason=, detail=)` keeps the reason, `broken_tiers()` and
`reason()` read it back, and `/api/health` exposes `broken_tiers` + `tier_reasons` beside
the existing `degraded_tier`. Three rules this must keep: an **unexplained** failure stays
`DEGRADED`, because guessing "broken" would stop trying a tier that was only having a bad
minute; `BROKEN_COOLDOWN` is long but **not** forever, since a key can be fixed while the
app runs and a tier never retried can never be seen to recover; and the state string stays
`"degraded"` - health, the web pill and the tests all read it, and `broken` is new
information rather than a rename. The provider reports why through an optional
`on_failure` **callback argument**, never a field on the provider: one provider serves
every request thread. A caller that passes no sink behaves exactly as before, which is why
the advisor, the document tool and the copilot needed no change. A caller that **records**
the outcome must pass one: the keep-warm `ping` did not, so every failed ping was recorded
as busy and demoted a tier a chat turn had found broken. `plan()` reports why on the
decision it returns (`PlanDecision.failure`; a decision belongs to one call, so this is
safe where a provider field is not), and the non-streaming fallback records it: that
branch runs only when the route failed, and it marked a retired model id busy.

**Broken tiers survive a restart; busy ones do not.** `ModelStatus(path)` writes
`data_dir/model_status.json`, so a retired model id or a rejected key is still known at
startup instead of being rediscovered by failing a real chat turn while the user waits.
Four decisions hold it together. Only `BROKEN` is written - reachability is ephemeral, and
persisting "busy" would skip, at tomorrow's startup, a tier that was merely loaded for a
minute yesterday. The file stores **wall clock, never `time.monotonic()`**, which counts
from a point that restarts with the process: a persisted monotonic stamp compared against a
fresh clock puts the cooldown anywhere between instantly-expired and centuries, so `_load`
reconstructs the *remaining* wait from elapsed real time. The knowledge persists but the
blocking does not outlive its cooldown, so a key fixed while the app was closed is proved
on the next turn and the record clears on the first success. And the write happens only
when the broken set changes, so an ordinary chat turn costs no IO.

**The orchestrator has one `data_dir`, and everything it persists goes through it** -
traces, tier health, generated images, generated documents. It is a constructor parameter,
not `load_config()` at each use: that reads the process-wide config, so under the test
runner every orchestrator shared one directory. A tier one test recorded as broken was
still broken for the next, and generated files landed wherever the running app keeps its
own - which is how 300 traces from a test run ended up in the live `.agent_data`. Persisted
state that ignores its caller's own config is shared state. One handle means the next store
added needs no parameter of its own, and
`test_everything_persisted_lands_in_the_given_data_dir` sweeps the process-wide directory
for anything that escaped, so the guard covers stores that do not exist yet rather than
today's four.

After all primary (e.g. NVIDIA) tiers, an optional **cross-provider fallback** is
tried: `OPENROUTER_API_KEY` (+ `OPENROUTER_MODEL`, default a free model;
`OPENROUTER_BASE_URL`) builds an OpenRouter planner (`app._build_openrouter_planner`,
passed as `AgentOrchestrator(..., fallback_planner=…)`). Since OpenRouter is a
different backend, it can answer when NVIDIA is throttled; its reply is tagged
`model="openrouter"` / degraded with a "_backup model_" note and tracked as the
`openrouter` tier in `model_status`/health. Absent the key it's simply skipped.

**Why the chat tier is told what it CAN do.** `_NO_TOOL_CLAIMS` said only what the model
must not claim, so it filled the gap by guessing and guessed low: "can you download
something for me" was answered "I can't directly download files from the internet or
access external resources", and "is it safe to run risky commands" with "I do not have
direct access to your system's shell or file system". Both false. `_CAPABILITIES` now
states what the tools actually do, that risky ones ask first, and that the app is
`python -m laptop_agent.webui` on port **8770** — the persona previously asserted it was
always a desktop window, which is how "how do I start the app in a browser tab" became
"try http://localhost:3000". Two rules that wording has already broken once each: it must
say what to ask for **only** for pictures and documents, because applied to a diagram it
produced a loop ("I'll provide the Mermaid syntax for you to request the actual drawing.
To draw this flowchart, please ask me to: draw a flowchart…" — handing the request back);
a diagram is now explicitly the exception, drawn in that reply.

**Why the chat tier is told it cannot make files.** A tool result reaches the next turn as
part of the transcript — the web client appends a bounded digest of `result.data` to the
assistant turn it sends back — so the model learned the tool's own output shape and
reproduced it. After one generated picture it answered the next question with "Here is a
diagram..." plus a Markdown image link to the *previous* turn's file and a fabricated JSON
block: the page then showed a broken image and a Save control with nothing behind it, and
the traces proved no image command ever ran (`kind=chat`). `_NO_TOOL_CLAIMS` in the chat
system prompt (both `answer` and `stream_answer`) forbids claiming a file was made, writing
an image link, or emitting tool JSON, and says a diagram belongs in a fenced code block.
The digest is labelled in `dataDigest` for the same reason. The **routing** prompt is
deliberately left alone — it must keep emitting JSON. Two things that wording must keep
getting right, both learned by breaking them: it must **not** say the assistant cannot make
images (the first version did, and the model started telling users so — it is false, the
image tool exists), and it must say there is **no follow-up turn** (without that the model
answered "Let me create that for you now" and then never did). `_referent_topic` also skips
an assistant turn that only talks about itself, which is how a back-reference once resolved
to `I read "this" as I can't generate or attach images directly...`.

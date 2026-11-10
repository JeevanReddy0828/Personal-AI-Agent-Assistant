# Proposal: a Laya decision model in the tier choice

**Status: proposal, plus the CPU half of its phase 0 measurement.** Nothing in the app has been wired
up or installed; the measurement ran in a throwaway environment outside the repository. Written 2026-11-09 against
`main` at `e251f50`. It answers one question: can the Laya typed-decision model
([`JeevanReddy0828/laya-mlx`](https://github.com/JeevanReddy0828/laya-mlx), a fork of
`mizorewww/laya-mlx`) help the brain choose between the fast, smart and ultra tiers? The model and
the runtime that runs it get different answers, so they are kept apart below.

## The short answer

| Question | Answer |
|---|---|
| Can Laya, the model, choose fast / smart / ultra? | **Possibly.** It answers the same kind of question `_complexity` answers, a score over a few fixed levels, and it ships a `router_questions()` preset with a `difficulty` score meant for this. Nobody has measured whether it beats the keyword heuristic on our requests. Upstream's own "Honest limits" call the base checkpoints weak zero-shot. |
| Can laya-mlx, this runtime, run it on this laptop's GPU? | **Not on Windows.** laya-mlx targets Apple silicon: it only installs `mlx` on `darwin`/`arm64`, its CI runs on macOS arm64 alone, and its 7–14 ms figures come from an M3 Max GPU. On Windows, MLX 0.32.4 publishes CPU-only wheels; its CUDA backend ships only for Linux. The one route to the RTX 3050 Ti is **WSL2** (Ubuntu is installed) with `mlx[cuda12]`, which laya-mlx has never been tested on. |
| What would run it on the GPU here? | **Upstream Laya** (PyTorch, `NandhaKishorM/laya`). It documents Windows 11 and picks CUDA automatically, with mixed precision. The fp16 weights (644–843 MB) fit the 3050 Ti's 4 GB. The best shape is a separate `laya-serve` process on loopback, so this app keeps zero required dependencies. |
| Can it replace a tier or the LLM router? | **No.** It generates no text and cannot fill in a command's arguments. It can only pick among labels we give it. |

**Recommendation:** run a short measurement (phase 0) before writing any code, and adopt Laya only if
it clears the bars in [What would make it worth it](#what-would-make-it-worth-it). Phase 0 needs
the NVIDIA driver working, which you can check by seeing the GPU listed in `nvidia-smi`.

### Phase 0 so far: the CPU half (2026-11-09)

The CPU half ran on the same day. Full write-up: [`evals/tier_choice_experiment.md`](evals/tier_choice_experiment.md).

- **The test fixed in advance failed.** Laya plus the explicit cues scored 52.3–54.2% on 155
  requests, against 52.3% for `_complexity`. Its scores rank the tiers sensibly, but they crowd
  between 1.25 and 1.95, so the guessed 1.0 / 2.2 cut-offs sent nearly everything to smart.
- **A re-fit made afterwards reached 69.0%.** Cross-validated thresholds with the English
  checkpoint sent 1 fast request to ultra, against the heuristic's 3. That would pass the
  accuracy bars, but it was fitted after the fact, on prompts and labels Claude wrote.
- **The CPU is too slow.** The English checkpoint takes 486 ms at the median and 635 ms at P95, over
  250 ms. The multilingual one takes 142 / 189 ms, but it is the weaker checkpoint.
- **Still open:** the GPU timing, which needs the NVIDIA driver, and a confirmation run on a fresh
  set labelled by Jeevan with thresholds `(1.55, 2.05)` frozen in advance.

## What Laya is

Laya is by Convai Innovations, under Apache-2.0. It is a bidirectional encoder with decision heads,
not a chat model. One forward pass answers typed questions about a piece of text, which it calls
the *state*:

- `choice`: probabilities over named options.
- `score`: probabilities over ordered rubric levels, plus the expected level.
- `noul`: P(true) for a proposition.

There are three checkpoints:

- `laya`: ModernBERT-large, 421M parameters, 512 tokens, English.
- `laya-multilingual`: mmBERT-base, 322M parameters, 1,024 tokens.
- `laya-typed-decisions`: 421M parameters, fine-tuned on four synthetic workflows. Never a default.

laya-mlx reimplements the forward pass in MLX and keeps upstream's prompt format, calibration and
output schema. Its prompt and calibration code (`common.py`, `tokenizer.py`) needs only NumPy; the
encoder (`model.py`, `agent.py`) needs MLX.

This is the preset meant for the job:

```python
router_questions()["difficulty"] == {
    "type": "score",
    "instructions": "How hard is `request` for a language model?",
    "criteria": ["trivial: a lookup or one-liner", "easy: short answer, no reasoning",
                 "moderate: several steps", "hard: long multi-step reasoning or specialist knowledge"],
}
```

The same preset also has a `domain` choice and two `noul` questions, `needs_tools` and `is_sensitive`.

## Where it would plug in

This is how a tier is chosen today, on `main` at `e251f50`:

- `AgentOrchestrator._complexity` (`src/laptop_agent/agents/orchestrator.py:617`) returns 0, 1 or 2
  from the word count (over 35, over 70) and two substring lists. "explain", "why" and "debug" go
  to smart; "step by step", "prove", "puzzle" and "use the big model" go to ultra.
- `_pick_chat_model` and `_higher_chat_tiers` (`:642`, `:652`) turn that level into a ladder.
  `_handle` calls it at `:2950`, after routing has already decided the turn is chat.
- Falling down the ladder when a tier is busy or broken does not depend on how the level was
  chosen, and it stays as it is.

### 1. Tier choice (the proposal)

Only the *source* of `level` changes:

```
an explicit cue ("use the big model", "ultra", "think hard", "step by step", ...) -> 2, as today
else Laya's difficulty, if the decision service answered within its deadline       -> mapped to 0/1/2
else _complexity(text)                                                             -> today's answer
```

- The explicit cues keep the final say. When the user asks for the big model, a classifier must
  not overrule them.
- The mapping from expected score (0–3) to a tier is fitted on the labelled set, not guessed. A
  starting point to test: fast below 1.0, smart from 1.0 to 2.2, ultra above 2.2.
- A wrong guess costs time in one direction and quality in the other. The fast tier answers in
  about 0.5–1.7 s a turn and ultra in about 20 s (`docs/design/models.md`). A simple question sent
  to ultra therefore costs about 18 s of waiting, and a hard question sent to fast costs answer
  quality. The evaluation reports the two errors separately, never folded into one accuracy number.
- Tools, approval levels and account rules are untouched: this decision is reached only after a
  turn is already chat.

### 2. Skipping the routing call (later, separately evaluated)

`_route` already skips the LLM routing call, which has a 2.5 s deadline, for confident small talk
(`:793`) and for `is_plain_question` (`:800`). A `noul` question could skip it for more chat turns:
"Does `request` ask the assistant to do something on the computer or online, rather than to answer
or talk?"

- This is acceptable in one direction only. A low P(true) may send a turn to chat, and it must
  never send a turn to a command. Missing a command means the request is answered instead of done,
  which is the failure direction `docs/design/conventions.md` item 5 allows.
- Upstream reports negation failures: issue #377 shows `no_action` versus `cancel_account` going
  wrong. That is another reason it may only ever choose chat.
- It needs its own evaluation and its own PR, outside this proposal.

### Not proposed

- **Choosing a tool or a command.** Laya cannot produce arguments, and its own limits rule out
  trusting it with actions.
- **Replacing a chat tier.** It generates no text.
- **Later candidates.** Inbox triage (`email_questions()`: phishing, needs a reply, urgency) and a
  prompt-injection check on fetched pages (`guard_questions()`) may be worth it, each with its own
  evaluation.

## Runtimes on this laptop

The laptop has an 8-core Zen 2 CPU, an RTX 3050 Ti Laptop GPU with 4 GB, and Windows 11. WSL2
Ubuntu is installed.

| Option | Runs here? | What it adds to this app | Expected speed | Verdict |
|---|---|---|---|---|
| A. Upstream `laya-serve` on the 3050 Ti (CUDA torch), loopback HTTP | Yes, once the NVIDIA driver works | Nothing. It runs in its own venv and process, and the app reaches it over `urllib`, as it reaches the embeddings service | T4 reference: 32.8 ms per question (multilingual), 39.5 ms (English). The 3050 Ti Laptop is a similar class of Ampere part. **Not measured.** | **Recommended**, if phase 0 passes |
| B. The same sidecar on CPU | Yes | Same as A | **Measured here:** 486 ms P50 / 635 ms P95 (English), 142 / 189 ms (multilingual), fp32, 8 threads | Too slow for the English checkpoint. The automatic fallback when the GPU is off: upstream picks CUDA, then CPU. |
| C. laya-mlx in WSL2 with `mlx[cuda12]` | Unknown. laya-mlx only installs `mlx` on macOS and pins `mlx<0.33`, so `mlx` is installed by hand. It has never been run on CUDA. | A WSL process the app must reach | Unknown | Try once in phase 0. It is the only way this fork runs on this GPU. |
| D. laya-mlx on Windows MLX (CPU) | Unknown: `win_amd64` wheels exist, nobody has run them | `mlx`, `numpy`, `tokenizers`, `huggingface-hub` | Unknown. This is MLX's CPU backend, not its fast path. | Not worth it with a GPU present |
| E. Upstream `laya` inside the app's own process | Yes | `torch` (about 3 GB with CUDA), `transformers`, `vegaml`, `cryptography`, ... into the app's environment and `JARVIS.exe` | Same as A | **No.** It breaks the zero-dependency rule and bloats the packaged app. |

Other upstream facts that matter here:

- **Loading.** A cold checkpoint build took a 7.4 s median on CPU and 10.3 s on a T4. Load at
  startup in the background, never on a request. Until the model is loaded, the heuristic answers.
- **Memory.** In fp16 on the GPU, the weights take 644 MB (multilingual) or 843 MB (English), plus
  activations. On CPU it runs in fp32, about twice that in RAM.
- **Non-Latin scripts.** The English checkpoint collapses on them while staying confident:
  upstream measured Khmer at 0.000 accuracy with 0.952 confidence before calibration clamping.
  The app takes requests in other languages (translation covers 34), so the multilingual
  checkpoint is the default to test. Upstream's script-based `Router` chooses between the two.
- **Battery.** A resident CUDA process keeps the discrete GPU awake. Decide whether the sidecar
  uses the GPU on battery or only when plugged in.

## Latency budget

`_complexity` costs microseconds. Laya's decision lands before the chat call, so whatever it costs
delays the first token:

- **GPU.** An estimated 30–60 ms is small next to a 0.5–1.7 s fast-tier turn. If so, the question
  is accuracy alone.
- **CPU.** Measured: 486 ms at the median for the English checkpoint, a third more wait on a 1.5 s
  fast-tier turn, and 142 ms for the multilingual one. It is worth it only if enough turns move to
  the right tier.
- **Overlap.** When the routing call runs (up to 2.5 s), the decision can run beside it and add
  nothing. Only turns that skip routing pay its cost in full.
- **Deadline.** A hard deadline (proposed: 250 ms) hands the turn to the heuristic, so a slow or
  absent service never delays a turn by more than that.

## What would make it worth it

Phase 0 decides against a labelled set, with the bars written down before the run, as
`docs/evals/` does:

1. **Labelled set.** Write about 150 prompts of the kinds this app gets, each labelled fast, smart
   or ultra by Jeevan, and freeze the set before the first run. The traces keep timings only,
   never prompts, so the set is written by hand, not mined.
2. **Measure.** Run `_complexity` and Laya (multilingual and English) on it and record:
   - the confusion matrix;
   - the *simple sent to ultra* rate (the 18 s mistake);
   - the *hard sent to fast* rate;
   - P50 and P95 decision time on this laptop, on both the GPU and the CPU;
   - GPU memory in use.
3. **Adopt** only if all three hold:
   - Laya is at least 10 points more accurate than the heuristic;
   - it sends no more simple prompts to ultra than the heuristic does;
   - its P95 is under 250 ms on whichever device it will actually run on.

   Otherwise stop, and record the result in `docs/evals/` so it is not argued again.
4. **If it comes close but misses,** what remains is fine-tuning on our own labels. Upstream ships
   an RLCD notebook for two T4s on Kaggle; a 4 GB laptop GPU is tight for training a 322M encoder.
   That is a separate, larger decision.

## If it passes: the integration

The change is small and reversible: one new module and one call site.

- **`src/laptop_agent/decisions.py`.** `DecisionBackend = Callable[[str, dict], dict | None]`,
  injectable as in `embeddings.py`, with an HTTP backend to `laya-serve` over `urllib`.
  `tier_level(text) -> int | None` returns `None` on any error or timeout and records each reason
  with `failures.record_failure`.
- **Config.** `LAYA_URL`: unset means off, which is exactly today's behaviour. `LAYA_DEADLINE_MS`
  defaults to 250.
- **`orchestrator.py`.** The one line at `:2950` becomes `level = self._chat_level(command)`, which
  applies the order above.
- **Traces.** Record which source chose the level and how long it took: timings and labels only.
- **Health and Setup.** Show the decision model as on, off or unreachable, and on which device.
- **Tests,** offline with a fake backend:
  - unset gives today's levels exactly;
  - a backend that says "hard" gives ultra;
  - a timeout gives the heuristic;
  - "use the big model" stays ultra when the backend says trivial;
  - a command route is never changed.
- **The page.** `claude/tier-guess-sync` (uncommitted when this was written) copies `_complexity`'s
  cue lists into `app.js`, so the page can colour the wait, and adds a test that the lists match.
  Once a model chooses the tier, the page's guess would disagree with it, so the server should
  report the chosen tier when the stream starts. Settle that with the branch before phase 1.

## Phases

| Phase | What | Changes this repo? |
|---|---|---|
| 0 | Install the NVIDIA driver and check that `nvidia-smi` lists the GPU. In a throwaway venv outside the repo, install CUDA torch (about 3 GB) and `laya`. Download the multilingual (678 MB) and English (848 MB) checkpoints. Try laya-mlx once in WSL2. Run the labelled set on the GPU and the CPU, and write up the results. **Done:** the venv, the downloads and the CPU run. **Waiting on the driver:** the GPU run. **Not tried:** WSL2. | Only `docs/evals/`: the set, the script and the results |
| 0b | A confirmation set, labelled by Jeevan, tested with the thresholds frozen in `evals/tier_choice_experiment.md` | `docs/evals/` |
| 1 | The `laya-serve` sidecar, `decisions.py` and the one call site, behind `LAYA_URL` | Yes: one PR, with the full suite run |
| 2 | The routing-skip `noul`, evaluated separately | A separate PR |

## Open questions for Jeevan

1. Which matters most: fewer slow ultra turns, better answers to hard questions, or skipping the
   2.5 s routing call?
2. Should the sidecar use the GPU on battery, or only when plugged in?
3. Is a separate `laya-serve` process, started by you or by the app, acceptable, or must
   everything live in one process?

## Sources

- laya-mlx v0.3.0: README, `pyproject.toml`, `laya_mlx/agent.py`, `laya_mlx/presets.py`,
  `laya_mlx/router.py`, `benchmarks/results/platform-support.json`.
- Upstream `NandhaKishorM/laya` v0.4.30: `pyproject.toml`, and the README sections "Installation
  details", "Honest limits", the CPU/MPS device table and the T4 benchmark.
- PyPI metadata for `mlx` 0.32.4 and `mlx-cpu` 0.32.4 (wheel tags and dependency markers), and
  the Hugging Face file listing for `convaiinnovations/laya`, both read 2026-11-09.
- This repository at `e251f50`: `docs/design/models.md`, `src/laptop_agent/agents/orchestrator.py`
  and `src/laptop_agent/embeddings.py`.

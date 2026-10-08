# Architecture map, tool by tool

The long-form map: each tool and subsystem with the decisions that shaped it.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

## Architecture (map)

```
Interfaces: CLI (cli.py) · Tkinter dashboard (dashboard.py/gui.py) · web app (webui.py)
        |
AgentOrchestrator (agents/orchestrator.py) — routes text -> one tool or a chat reply
        |
Router: planner/heuristic.py (instant)  +  planner/openai_compatible.py (LLM)
        |
Tools (tools/): files (`scan files <path> by size` lists the largest across the whole tree; "largest
            files in my downloads" routes there instantly, and a place that is not a folder on this
            machine is left to the model), file_processor (universal "process file" dispatcher),
        web, websearch, research, browser, desktop, email ("find the email from Alex about
            the budget" is a sender plus topic search, not an inbox digest; IMAP combines
            FROM and TEXT criteria. A sender at `@gmail.com` does not select Gmail OAuth:
            only an explicit "in Gmail/Outlook" suffix does. "Search my email for X" is
            a search too; the possessive before "email" must not hide the topic, and
            a spoken "and then tell me what you find" is not part of the query. A date
            span after "from" asks for an inbox period, not a person; merely mentioning
            "new email" must not read the mailbox),
        music (a personal voicemail/messages request is not a YouTube song search;
            the shared router/tool guard leaves actual songs named "Voicemail" playable.
            "set the volume to fifty" uses the same spoken-number grammar as the
            calculator, then sends numeric `media volume 50`; keep questions such as "how do I
            set the volume" out of the action route),
        weather (Open-Meteo, real forecast — no key),
        translate (`translate <text> to <language> [from <language>]`, `translate to
            <language>: <text>`, "how do you say X in Y", "how to say X in Y", "say X in Y"
            and "can you say that in Y", "in Y, how do you say X", "what's the Y word for X".
            The last takes a word or a short phrase only (`_WORD_FOR_WORDS`), but a short
            relative-clause definition such as "a man who sings" still needs the model to
            name a word. "Say something in French" / "say that again" stay chat, while
            literal "say never again in French" translates. NVIDIA's hosted `riva-translate-1.6b` over the Riva gRPC host, chosen by
            `RIVA_NMT_FUNCTION_ID`. **The service cannot detect a source language** - an empty
            or "auto" source is refused - so the tool names it: `from <language>` when said,
            else the script (kana, Hangul, Han, Devanagari, Thai, Arabic, Greek, Cyrillic),
            else English, and only Latin-script text bound *for* English asks the fast tier
            which language it is; with no answer it asks the user rather than guess.
            `LANGUAGES` holds exactly the codes the model's own config reported; Telugu,
            Tamil and the other languages it lacks are named so the reply says so instead of
            reaching the router as prose. "translate that to Spanish" takes the reply above,
            without the tool-data digest the web client appends to it. `riva-translate-4b-
            instruct-v2` ignores its target language through this endpoint and
            `megatron-1b-nmt` is not callable on this account (measured 2026-10-06).
            MEDIUM, like the other network reads; everyday for a personal account),
        news (`news [topic]` — real headlines, free and key-less. A generic web search for
            "latest news" returns cnn.com and foxnews.com with their taglines, which is not
            the news. Google News RSS gives breadth and arbitrary topic search; **its own
            links are consent pages that fetch to 0 chars**, so publisher feeds (BBC) lead —
            they carry real summaries and their article pages do fetch, and the top few are
            enriched with `research.fetch_page_text`. Measured: 8 headlines, 3 with article
            text, in ~0.7s. A topic search is Google-only, so it gives headline + source +
            age without article text — still the story rather than a homepage.
            **Asking for the news is the whole sentence** (`heuristic._NEWS_ASK`, fullmatch):
            matched anywhere, "good news, i got the job" got the day's top stories and "fake
            news is a problem" a search for "is a problem"; and a topic counted only with
            nothing before it, so "latest tech news" lost "tech". A word only talk puts beside
            the noun (`_NOT_A_TOPIC`: pronouns, verbs, good/bad/fake, prepositions) sends the
            sentence to the router instead, which is the direction to err in),
        document (`document <request> [as pdf|word|powerpoint|markdown]` — the model writes
            Markdown, we render it: PDF through the same offline Chromium path as the resume
            export (`render_html_to_pdf(..., single_page=False)`), Word through python-docx,
            PowerPoint through python-pptx, or the Markdown itself. Saved under
            `data_dir/documents/`, downloaded via `/api/document?name=`. Note: the abandoned
            PyPI package named `docx` shadows python-docx and fails on import — the failure
            message says so.
            In "write up a one pager on remote work as a word doc", `write up` is one
            verb: the heuristic strips both words before handing the topic to this tool.
            **A deck names its format at the FRONT**, a document at the end. `split_format`
            only ever looked for a tail ("… as a pdf"), so "create a ppt for sun and planets"
            matched nothing, fell through to the default, and shipped a **PDF** for a request
            that said PPT. `_DECK_HEAD` ("a ppt for X", "slides on X", "a deck for X") is
            checked after the tail, and the same phrasing is mirrored in
            `heuristic._DECK_ASK` — whose document route also required a trailing format, so
            a deck request never routed instantly either. Keep its courtesy forms aligned
            with `document._DECK_HEAD`: "could you make a slide deck about Mars" once routed
            correctly but saved a PDF because only the tool missed `could you`. The heuristic
            passes the **whole sentence** through as `document <text>` so the tool can read the format
            off it. The heuristic and tool also recognize spoken verbs such as "throw
            together slides about X" and "whip up slides on X"; a miss in either leaves
            a chat answer or a PDF. A deck also gets its own prompt (`_DECK_PROMPT`): asked for slides
            against the document prompt, the model writes essay paragraphs. `deck_outline`
            turns `#` into the title slide and each `##` + bullets into a slide, drops a
            heading with no body, and keeps a stray prose line as a bullet rather than
            emitting an empty slide),
        imagegen (text-to-image via NVIDIA's hosted FLUX endpoint. Two guards live in
            `orchestrator._repair_image_command`, because the router does not resolve image
            subjects reliably: it emitted a users/orders/products **ERD** for "create an
            image for this" in a conversation about TCP congestion control — and again in
            one about foxes, so it is copying its own few-shot example, not reading the
            context. (1) A back-reference takes its subject from the latest assistant turn
            (`context.topic_of`), never from the router. (2) A technical diagram
            (`is_diagram_subject`) never reaches a diffusion model at all — it answers in
            the reply as Mermaid or text, because FLUX renders diagram-shaped nonsense.
            (3) A resolved referent is prose, not a prompt: handing the sentence "TCP
            congestion control is a fundamental mechanism..." to FLUX produced a picture of
            unreadable text, so `_visual_prompt` asks the fast tier to rewrite it as a
            concrete scene, or to answer NONE when the idea cannot be drawn at all. A NONE
            reply is returned **verbatim** (`_VERBATIM`), bypassing the chat ladder — asked
            to phrase the refusal itself, the model claimed the app cannot generate images,
            which is false. The repair runs on every route, not just the LLM one, because
            the instant router turns "draw me a picture of this" into `image this`.
            `_repair_target_command` is the same idea for every command that names a
            concrete target (`open url`, `download`, `read file`, `process file`, …): the
            router answered "how do I start the app in a browser tab" with
            `open url http://localhost:3000`, a port nobody mentioned. A target whose
            identifying token — host without www/TLD, or filename **stem**, never the
            extension — appears in neither the message nor the last six turns is refused and
            answered as chat. "open youtube" still expands to youtube.com, because the name
            is in the request.
            `image <description>`
            plus a heuristic route for "draw me a picture of …"; the trailing word
            square/landscape/portrait/wide/tall picks the resolution. Saves under
            `data_dir/images/`, returns Markdown that embeds the picture, and the chat
            renders it inline through `/api/image?name=`),
        calculator (`calculate <expression>` - exact arithmetic, because a language model is
            the wrong tool for it: `solve - 67458363*37834872` produced a decision framework
            and never reached 2,552,278,529,434,536. A hand-written recursive-descent parser,
            **never `eval`** (that would be arbitrary code execution on user text); integers
            stay exact and division uses `Fraction`, so `1/3*3` is 1 and `754/86982` keeps
            its exact form - the model's own answer to that was wrong from the 8th digit.
            `looks_like_arithmetic` is strict on purpose so "should I use 2 or 3 replicas"
            still reaches the advisor, and `solve` hands a sum straight to the calculator.
            Note the grammar: unary minus sits **above** power, so `-2**2` is -4; putting it
            inside power gave 4),
        forecast (`forecast <column> in <file.csv> [by <date column>] [for N]` - a column of
            the user's own CSV projected forward by `analytics/forecast.py` (Codex's
            ANALYTICS-01, contract in `docs/forecasting.md`), never by a model.
            `tools/forecast.py` owns what the core leaves to the tool: dates, spacing, gaps
            and odd cells, each refused with a reason rather than guessed - a missing month is
            named, two rows in one period are not added up, "1,5" is not fifteen. The period
            comes from the **smallest** gap between dates: the median gap of Jan, Feb, Jun is
            75.5 days and named no period at all. Quarters and years are counted on the
            calendar the labels use (Codex's review): counting from the first date's month
            took Mar 31, Jun 30, Oct 1, Dec 31 as four quarters in a row, labelled Q1, Q2,
            Q4, Q4. The answer says what the core established and nothing more: **no number**
            when it could not test a forecast (its points are then the last value repeated),
            **no band** unless every lower *and* upper bound exists, and accuracy only from
            the later stretches that played no part in choosing the method (`holdout_mae`) -
            never the selection-block MASE, since the winner is chosen on that block and
            beating the baseline there is guaranteed. A kept baseline is "not improved on by
            enough", never "unbeaten": on few tests a smoother must win by a margin. In the
            web reply `forecastChart` (app.js) draws it from the result's data, never the
            text: recent history (eight times the steps ahead, 12-48 points - with all 48
            behind three steps the band had 6% of the width), the dashed forecast, and the
            band under the same every-bound rule, built as DOM nodes. One step is a capped
            whisker (as a polygon its corners shared an x and it had no area), and a flat
            series is padded by its own magnitude (1e20 + 1 is 1e20, which drew NaN). Only a
            `.csv`/`.tsv` makes it a data
            forecast: "forecast", "boston forecast" and "forecast for tomorrow" stay the
            weather. The reverse holds too (review of #159): "forecast Revenue in sales.csv."
            missed the grammar and got the weather at a place called sales.csv, so a sentence
            that starts with "forecast" and names a table is answered with the usage when it
            cannot be followed (`forecast_command`), and the weather heuristic declines any
            sentence naming a `.csv`/`.tsv`. The clauses after the file come in either order,
            a sentence may end in "." or "please", and `season N` states a cycle the user knows
            - never inferred from the calendar, which the contract rules out; the reply suggests
            it when none was found and the history holds two cycles. Numbers under 1 show three
            significant figures: two decimals called an error rate's average miss 0. One reader
            and one number parser serve this and `analyze spreadsheet` (`tools.files.read_rows`,
            `parse_number`). Developer-only by default-deny, since it reads a file),
        diagnostics (`what drives <column> in <file.csv> [using <col>, <col>] [by <date column>]`
            and `anomalies in <column> in <file.csv> [by <label column>]` - Codex's ANALYTICS-04
            core, contract in `docs/analytics.md`, over a CSV read exactly as `forecast` reads
            one. Drivers ranks features by standardized association, says "association", never
            cause, and gives accuracy only from the held-out last rows against the training
            average; without `by` the rows are taken in file order and the reply says so, and a
            column that is not all numbers is left out by name rather than silently. With MAD
            zero a value that differs is listed as unscored - not an anomaly, not nothing - as
            the contract requires. One dispatcher branch (`diagnostics_command`) serves both,
            and like `forecast` a sentence naming a table it cannot follow gets the usage.
            Developer-only by default-deny),
        windows (`window <name> <position>` / `windows` - arrange the desktop by voice:
            "put WhatsApp on the left and Chrome on the right". Positions: left/right/top/
            bottom, the four corners, thirds, centre, full. `parse_placements` finds the
            POSITIONS first and reads the gaps between them as names, because splitting on
            "and" cannot parse how this is actually said out loud - by voice it arrived as
            "left side WhatsApp right side Chrome", position before name with no
            conjunction.
            **The parser was never the gap; the router was.** `parse_placements` read
            "whatsapp on the left and chrome on the right" correctly all along, but with no
            verb in it nothing routed there, so it went to the LLM. `_ARRANGE_PLACEMENTS`
            takes the verb-less form, and matching a bare `<name> on the <position>` is
            exactly as dangerous as it sounds - four rules hold it in, each chosen against a
            sentence that breaks without it: every clause needs an explicit **preposition**
            ("turn left and then right" is two placements otherwise); there must be **two or
            more** clauses (a lone placement is where ordinary prose lives - "my keys are on
            the right"); the pattern must consume the **whole sentence** ("the chrome finish
            on the right handle is worn" leaves words over); and a clause whose **name ends
            in a copula** is refused, because "the value is in the middle and the key is on
            the left" satisfies the other three and no window is called "the value is".
            Questions and decisions are refused outright - "should i put the legend on the
            right" is a clean fullmatch and belongs to the advisor. This narrows the class
            rather than closing it ("the answer lies in the middle..." still slips through),
            and a false positive costs one harmless, self-reporting tool call.
            The looser layout-feature phrase fallback also rejects definition questions;
            those belong to chat, while a spoken imperative using the phrase can still
            arrange a named window.
            **`_POSITION_WORD` is derived from `LAYOUTS`/`_ALIASES`, never hand-written.**
            The planner's copy had already drifted - `left` and `third` but no `top left`,
            `bottom right`, `left third` or `right half` - so "notepad on the top left"
            could not route against a parser that handles it perfectly. Same failure as
            `_TOOL_SIGNALS`: a hand-maintained copy of a list fails by omission from the
            copy. `windows.py` is stdlib-only at import, so the planner can import it.
            A window matches on its title *or* its executable, since neither
            alone is enough (Chrome is titled after the page it shows; WhatsApp's process is
            `WhatsApp.Root.exe`). Ranked, not just filtered: a window matching in **both**
            title and executable beats one matching in only one of them, then shortest
            title. A plain substring test arranged **Live Caption** - a Chrome-hosted widget
            also running as `chrome.exe`, whose title is shorter than "J.A.R.V.I.S - Google
            Chrome" - when asked for "chrome"; the same rule picks the real
            `WhatsApp.Root.exe` over the `msedgewebview2.exe` window of the same name.
            Rects come from
            `SPI_GETWORKAREA`, not the screen, so `full` does not hide behind the taskbar.
            DWM-cloaked windows and three named shells are filtered - enumerating the real
            desktop returned "Windows Input Experience", "NVIDIA GeForce Overlay" and
            "Program Manager" alongside the six real apps. **MEDIUM, not HIGH**: moving a
            window is local, reversible and sends nothing anywhere, and a HIGH would put an
            approval click in front of every spoken "snap Chrome left", which is the point
            of the feature. The ctypes layer is behind an injectable backend so the whole
            success path is tested off-Windows; verified on the real desktop by snapping
            Chrome to (0,0,960,1032) and restoring its exact original rect),
        travel (maps: OSRM driving distance/ETA, multi-stop `trip` chaining legs +
            totals, IP-geolocated "around me", `map` -> OpenStreetMap embed for the
            web Map panel, + OpenStreetMap hotels/places — no key),
        youtube (transcript -> summary, indexed for Q&A; `youtube` extra),
        transcribe (OCR + STT. **OCR** prefers hosted `nvidia/nemotron-parse` when a key is
            present and falls back to Tesseract — same shape as the speech path, chosen by
            `LAPTOP_AGENT_OCR=auto|parse|tesseract`, reported as `ocr.engine` in
            `/api/health`. Tesseract returns characters; parse returns a laid-out page as
            typed, positioned regions, so a heading survives extraction as a heading
            (measured: 453KB screenshot, 2.6s, 54 regions). The request carries the **image
            alone** — a text part is rejected with "The model does not support text input" —
            and the result arrives as a `markdown_bbox` **tool call** with `content: null`.
            `task_prompt` from the API snippet is ignored here; it belongs to the self-hosted
            NIM. `Caption` regions are **dropped because the model invents them**: that same
            screenshot returned 37 captions for 2 pictures, one reading "Figure 1: The
            S-color image of the alpha-ray diffraction pattern...", fabricated from training
            data — dropping them took the extraction from 2121 characters to 901, all real.
            Any failure, including a blank extraction, falls through to Tesseract.
            **STT**: Vosk lightweight or Whisper, see voice.md), webcam (vision extra),
        obsidian (vault memory: metadata-weighted search [title/alias/summary > body],
            alias-aware resolve, link-aware `context_for` for `ask vault`, and `audit`
            for orphans/broken-links/missing-summary — Obsidian best-practice patterns)
Subsystems: tracing.py (per-turn latency: route_ms/tool_ms/ttft_ms/total_ms, tier and
        fallback, ok — timings only, never prompts or replies; the only text kept is the
        resolved command's verb. `AgentOrchestrator.handle` is a thin wrapper that opens a
        `TurnTrace` in a ContextVar so nested frames and concurrent worker threads mark the
        right turn. Read it with `latency` or `/api/traces`. **History outlives the 300-turn
        ring** (ANALYTICS-02): every turn also appends one line to `traces_timings.jsonl` -
        when, kind, the tier *asked for* (so a fallback counts against the tier that failed
        it), ok, fallback, total and first-token ms, no verb - pruned to 90 days once a day,
        and `TraceStore.hourly()` folds it into hours with fixed latency buckets. An append,
        not a rewrite: rewriting a 90-day rollup file on every turn measured 122ms at its
        worst; the append is 0.4ms, against 19.8ms for the ring's own rewrite. A line that
        cannot be read is skipped, since the log is read inside a turn whose caller guards
        only `OSError`. That includes bytes that are not UTF-8 (Codex's review): the log is
        read as bytes and decoded line by line, and the prune keeps no backup, because both
        a whole-file decode and the backup's re-read raised `UnicodeDecodeError` - a
        `ValueError` - for one bad byte anywhere. So is a time that parses but cannot be put
        in UTC, which raises `OverflowError` or, on Windows, `OSError` instead),
        embeddings.py (semantic retrieval: `nvidia/nemotron-3-embed-1b` on the chat host and
            key — `OPENAI_EMBED_MODEL` / `OPENAI_EMBED_KEY` override. The model is
            **asymmetric**: a document embeds as `passage`, a question as `query`; using one
            type for both quietly costs accuracy. Every method returns None instead of
            raising, so a dead network drops back to keyword scoring. Measured on six short
            documents and five paraphrased questions: lexical 1/5 (three returned *nothing* —
            no word overlapped), vectors 5/5),
        knowledge.py (TF-IDF index + Q&A, fused with vectors when an `Embedder` is passed.
            **The agent's own output does not outrank the user's documents, and does not
            grow without limit.** `solve` files its analysis as `advice: …`, research files
            the scraped page, a transcript lands as `youtube:…` — and measured on the real
            store that had taken over: **30 of 31 documents were generated against ONE real
            file**, with the scrapes averaging 20k characters to the advice dumps' 5k, so a
            scrape outranked the README on any word they shared. `document_kind()` reads the
            source prefix (inferred, so no migration) and `KIND_WEIGHTS` discounts generated
            text — `file` 1.5, `advice` 0.9, `research`/`youtube` 0.8. Deliberately gentle,
            and swept over 15 queries with a known answer: those values took top-1 from
            12/15 to 13/15 and top-3 to 15/15, while a heavy hand (`file` x3) dropped top-1
            back to 12/15. It only moves the **secondary** sort key — distinct terms matched
            still decides first — so it breaks ties rather than overruling relevance.
            `GENERATED_CAPS` (advice 12, research 8, youtube 12) trims the oldest of each
            kind on every `add`, and `knowledge prune` applies it on demand and reports what
            went. **A document the user indexed is never pruned.** Note when writing an eval
            here: resolve the expected document by source substring, never by id. The README
            was re-indexed and its id moved 47 -> 52, which made a harness report the right
            answer as a miss and nearly bought a wrong conclusion ("kind weighting does
            nothing" at 8/15, when the truth was 12/15 rising to 13/15).
            **Nothing here re-reads or re-tokenizes the corpus per query.** A search parsed
            1.6MB of JSON *and* tokenized all 282k characters every time: 41ms, of which
            `_term_counts` was 72% and `_load` 21%. `_load` caches the parsed store keyed on
            `(st_mtime_ns, st_size)` — the file's own identity, so an edit by Codex or
            another process is still picked up — and `_counts_for` caches per-document term
            counts, dropped whenever the store reloads. Every mutator calls `_invalidate()`
            **before** touching anything, so a mutator that fails part way cannot leave a
            dirty store for a reader. Three layers cover staleness (explicit invalidation,
            `_save`, the mtime key), which is why removing any one of them does not show up
            in the tests — break the mtime key to see the guard fire. `answer` also skips a
            passage whose text contains no query term as a *substring* before tokenizing it
            (a term cannot match as a token if it is absent as a substring, so the same
            windows are skipped, just without paying for them). Measured: search 41ms ->
            1.7ms, answer 102ms -> 24-38ms. `_prose_weight` uses `map(str.isalpha, …)`
            rather than a genexpr calling two methods per character — that line alone was
            46% of an answer; a regex was measured both slower *and* wrong on 1931 of 2000
            passages, so do not "simplify" it back.
            Documents embed once in `add()` and the vector is stored beside the text, so
            search costs one query embedding and never re-embeds. The two rankings are
            merged by **reciprocal rank fusion**, not by adding scores: a TF-IDF score and a
            cosine are not comparable, and normalising them makes the blend depend on
            whichever spread is wider. Keyword hits still win on exact tokens — filenames,
            model ids, error codes. Documents saved before this existed have no vector:
            `knowledge reindex` backfills them in batches, skipping a failed batch rather
            than aborting, and is idempotent.
            **A follow-up is looked up in its standalone form**, like every other path:
            `ask knowledge which models does it use` queried the index with the pronoun and
            answered out of an NVIDIA RAG scrape, because the README says "model" 26 times
            and "models" once while the scrape says "models" 36. `_dispatch_knowledge`
            already received `history_turns` and ignored them. But the rewritten query
            carries the referent, which matches the README's opening blurb almost verbatim
            — scoring passages with it answered the *referent* instead of the question — so
            `answer(question, retrieval_query=…)` splits the two: **the referent picks the
            document, the question picks the passage inside it.**
            The answer is quoted from the highest-**ranked** document that has a usable
            passage, not the one holding the highest-scoring passage; `pool` is built in
            ranked order because `doc_index` decides that, and it was carrying document ids.
            Ranking changes measured over 13 queries with a known-correct document and
            **rejected**: BM25 length normalisation drops top-1 from 10/13 to 8/13, sorting
            by score instead of matched-term count drops it to 9/13, and plural folding plus
            a bigger stopword list is a wash (fixes one query, breaks another). The
            `matched`-first sort key is right — do not "improve" it without re-measuring.
            Known limit: "models" does not match `OPENAI_MODEL`, so the passage chosen
            inside the README is not the model table; closing that needs stemming, which
            measured as the wash above), tasks.py (parallel + retry),
        workflows.py, autopilot.py (safe allowlist), reasoning.py (autonomous
        agent loop — plan/act/observe/replan over any tool),
        advisor.py (ProblemSolver: `solve <problem>` — web-grounded structured
        analysis: framing, options w/ trade-offs, committed recommendation, action
        plan; injected decide+research, indexed for recall. The LLM planner
        auto-routes decision/problem questions here — no manual command needed —
        via system-prompt guidance + a few-shot turn in planner/openai_compatible.py),
        scheduler.py
        (recurring jobs), copilot.py (JobCopilot: ports the Agentic-AI-JOB-CoPilot
        logic — ATS scoring, keyword/claims extraction, grounding — onto our LLM
        provider; `tailor_application()` → grounded bullets/cover letter/interview pack
        via `/api/copilot`, PLUS `tailor_resume()` → a grounded one-page resume: the
        model returns CONTENT as JSON, `render_resume_html()` lays it out in a FIXED
        Caladea template so the format never drifts; name/contact/certs come from the
        stored profile, project links are grounded in the candidate's real GitHub repos),
        jobs.py (JobTracker: job pipeline — stages incl. a sourced `lead` stage,
        funnel/response-rate stats, base-resume + tailoring persistence, JSON-persisted;
        `job add/list/stage/remove` + `/api/jobs`. Every stage a job enters is kept as
        `events` [{stage, at}], 50 per job, because `reached` keeps only the furthest stage
        and the time to a reply cannot be read back from it. Records saved before 0.40.0
        (2026-09-08) carry neither `events` nor `applied_at`: the fields did not exist when
        they were made, so their history starts at their next change instead of being made up),
        tools/jobright.py (JobrightTool: Playwright scraper ported from job-agent--Jarvis,
        behind the `browser` extra — session-first login, API-interception + DOM-fallback
        scrape, JD enrichment, then filters to early-career fit: seniority/years/PhD/
        clearance/no-sponsorship + resume-relevance; returns leads + a `dropped`-with-reasons
        list. `jobright pull` command → `import_leads` at the `lead` stage; schedule daily
        via `schedule command "jobright pull"`),
        tools/resume_pdf.py (renders the tailored HTML resume to a Letter PDF via the
        `browser` Chromium — no LaTeX toolchain needed), reminders.py, metrics.py, health.py,
        agents/control_room.py (specialist roster), safety.py, audit.py,
        failures.py (FailureLog: every `except` that swallows also records here -
            `failures` command, `/api/failures`. This exists because graceful
            degradation hid two outages: a tier returning HTTP 400 on every request
            reported itself as *busy* (the provider caught URLError, returned None, and
            the orchestrator reads None as congestion), and the same None became "the
            model returned an empty document" when the API 503'd. The transport now
            retries 5xx/429/timeouts three times and **never retries a 4xx** - the
            request itself is wrong, and the ultra bug was a 400 on every attempt, so a
            blind retry would have tripled its cost while hiding it just as well.
            **Do not add an `except` that only returns a fallback: record the reason.**),
        approvals.py (ApprovalBroker: bridges the blocking approval gate to an HTTP
            answer so a risky action can be approved **in the web app**. The browser could
            not answer the gate, so `_guarded_approval` auto-denied everything above
            MEDIUM and downloads, shell commands, opening apps and sending mail simply did
            not work there. A HIGH/CRITICAL request is registered, pushed to the page as an
            SSE `approval` event, and the worker thread waits. Nothing is auto-approved,
            an answer must name the exact request id, an id is single-use (so approving a
            download cannot authorise the command behind it), `/api/approve` is a
            token-checked mutation, and a **timeout denies** - silence is never consent.
            With no listener attached it denies immediately rather than waiting: nobody
            could answer, and waiting once took the test suite from 18s to 138s),
        memory.py, token_vault.py (DPAPI. Like accounts and sessions it keeps no `.bak` and is
            read strictly: `forget` left the token in the backup, and a damaged vault was read
            back from it - the token the user removed, returned (Codex's review of #160). A
            damaged vault is recorded and gives way to the next store or forget, so it cannot
            refuse a reconnect for good), config.py,
        terms.py (the one word splitter the retrieval paths share — `knowledge`, `context`,
            `tools.obsidian`, `tools.files`. Four near-identical tokenizers meant a fix
            applied to one never reached the others: `knowledge` learned to keep
            "J.A.R.V.I.S" whole, while a vault search for that exact title returned
            **nothing** — and phrased as "what is J.A.R.V.I.S" it fell back to ranking on
            "what"/"is" and matched an unrelated note. Session retrieval had no term at all
            (`terms()` returned `[]`), and a file Q&A came back empty. Only the *splitting*
            is shared: each caller keeps its own stopword list and minimum length, which
            are tuned differently on purpose. `tools.files` borrows `collapse_acronyms`
            alone and keeps its `[A-Za-z']+` pattern — measured over this repo's prose,
            moving it onto `words()` changed 53 of 134 paragraphs, gaining 130 kinds of
            version number ("2026", "120b", "404") and losing 52 contractions, because
            "can't" splits at the apostrophe. Collapsing also turns "e.g." into "eg",
            which every 3+ character caller drops and which carries no ranking weight.
            **It is also the one sentence splitter** (`sentences`), for the same reason: the
            file summarizer and the knowledge base each flattened every line break and then
            cut at full stops, which Markdown's badges, tables and code do not have — the
            README summarized as 15 KB of one paragraph, and asked how to start the app the
            knowledge base quoted 16,000 characters of image links and a table of contents.
            Lines are read as lines: images, link targets, real HTML tags and rows of links
            (navigation) go; headings, rules and blank lines end paragraphs; a run-on is cut
            into 400-character pieces, none of it dropped. `structure=True` (knowledge) keeps
            headings, table rows (as `cell: cell`) and code lines but never a ```mermaid
            source, because the answer is often a row or a command; a summary skips them.
            Measured on the repo's own docs over ten questions: answers went from 3,000-16,000
            characters with up to 398 markup tokens to 700-1,700 with none. Two defects found on
            the way. Terms start at two letters but the stopwords stop at three, so "do" and
            "in" weighed like "browser" - and with one document indexed every term weighs the
            same, so live, the README answered "how do i start the app in a browser tab" with
            Google sign-in. `_FUNCTION_WORDS` now weigh `FUNCTION_WEIGHT` (0.2) of a word in
            passage scoring only; document search is untouched. Not zero: as stopwords, a
            follow-up's own document had no matching passage left and the answer came out of a
            scrape (`test_the_referent_picks_the_document`). Swept over twelve questions whose
            answer must OPEN the reply: README alone 4/12 at 1.0, 9/12 at 0.2 (8 at 0.1 and 0.3);
            three documents 6/12 -> 7/12. And the substring pre-check before tokenizing read raw
            text, where "jarvis" is not in "J.A.R.V.I.S". Left alone, as ranking: "what is
            jarvis" finds the title's window, but a shorter one-mention window outscores it),
        context.py (session context: chunks the chat transcript by Markdown structure, ranks
            chunks against the new message, budgets one block for every model-facing prompt)
Everyday layer (see "Everyday requests" in routing.md): tools/units.py (conversions),
        tools/dates.py (days until / holidays / "what's today"), tools/clock.py (the time in a
        zone, a time read in another zone - "convert 9am pst to ist" - and the gap between two,
        from the zone database for the day asked, as a whole sentence with both zones known;
        a bare "9" is refused as morning-or-night), tools/chance.py (coin, dice,
        numbers via `secrets`), timers/alarms/repeats in the orchestrator over reminders.py +
        scheduler.py (`days` for weekdays / named days), lists and facts in memory.py
```

# Voice

Speech output, barge-in, the meter and dock, speech-to-text engines, recordings and the Riva deadline.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

**Voice, and why it used to answer itself.** `clean_for_speech` (server) and `speakable()`
(client, same rules) must drop embedded images, code fences and bare URLs *before* the
punctuation strip breaks those constructs apart. Reading an image URL aloud produced
"slash api slash image question mark name equals…", which the echo guard could not match,
so the microphone heard it, counted it as a spoken interruption, and drew the picture
again — one request became four. The echo guard compares against the **last six utterances
individually, and each pair said back to back** (the microphone hears no sentence
boundaries; never one accumulating blob, which matched almost any real sentence and ate the
user's own interruptions), a barge-in needs three words, listening reopens only 800ms after
a reply ends (Bluetooth speakers are still playing it), what is heard in an utterance's tail
is ignored (400ms in the browser path, 800ms on the server-STT path), and a third spoken
interruption inside 25s turns spoken barge-in off for the session. The server-STT listening turn also
needs a quarter second of sound, with no quiet gap over 250ms, before it counts as the
user: one loud frame, and later clicks seconds apart, were transcribed and answered. With
open speakers full duplex is never fully reliable; Space is the manual fallback (the
Interrupt button is in the hidden `#voice` panel, see below).

**Stopping has to stop the turn, not just the sentence.** `stopSpeaking()` cleared the queue
but the request was still streaming, and every later `tts` event was enqueued and spoken —
so pressing Space silenced one sentence and the reply carried straight on with the next.
Two things fix it and both are needed: `interruptNow()` now calls `stopGen()` as well (the
spoken-barge-in path always did; the manual one never did), and a `ttsEpoch` counter,
bumped by every stop, is captured when a turn starts streaming — `tts` events and
`voiceTurnDone` from a superseded turn are dropped instead of spoken.

**Barge-in in server-STT mode listens to level, not words.** `bargeStart` used to return
immediately when `useServerStt()` was true, and since `setSttEngine` turns server STT on by
default as soon as the server has an engine, *talking could not interrupt at all* — the gear
note even promised "it cannot hear itself. Press Space to cut in." `serverBargeStart` now
holds the microphone open (echoCancellation + noiseSuppression + autoGainControl) while
J.A.R.V.I.S speaks, spends the first ~6 frames **of playback** learning how loud our own
output still leaks through (learning before the audio arrived learned silence, and our own
voice then cleared the bar), and treats **220ms of sustained sound above
`max(bargeFloor, floor*2.2)`** as the user.
On trigger it only **pauses** playback: until the words are heard, a loud moment may be a
cough, the room or our own voice, and stopping outright cut a recipe off at "cilant". The
capture keeps running — deliberately *not* `stopSpeaking()`, which would tear it down — and
after ~1s of quiet is transcribed from ~0.6s before the trigger, not the 12s of our own
reply the buffer held (that was once sent as a question). Only three words or more that are
not our own speech commit: cancel speech, clear the queue, bump `ttsEpoch`, `stopGen()`,
answer. Anything else resumes where it paused. The three-in-25s switch counts only
interruptions that commit — counting every loud moment let three coughs switch spoken
barge-in off for the session, silently — and false pauses get their own limit, since every
sentence re-arms barge-in: after two in one reply the rest of it plays through, and
`voiceTurnReset()` starts the next reply fresh. It works in the pywebview window too, which
has no Web Speech API at all. Known gap: in a browser tab the pause is
`speechSynthesis.pause()`, which some platforms ignore; only the app window's audio path has
been tried on the laptop.

`bargeFloor` (default 0.045) is the one number worth re-tuning from real rooms: too low and
the app hears itself, too high and a quiet voice cannot cut in. It was a constant in a
closure, and that is why the feature could be "fixed" twice and still reported as not
working — nobody could see what the microphone was hearing or what it had to beat. Both are
now on screen: a meter in `.stagedock` (at the foot of the presence panel, or above the
composer wherever that panel is hidden) shows **peak / learned leak / threshold** live while
barge-in is armed (square-rooted, because 0-0.15 is the whole interesting range and linearly
it occupies the first eighth of the bar; repainted at most every 80ms, which is one paint
per 4096-sample frame and keeps the audio callback cheap), and **Voice cut-in level** in the
gear popover sets the floor, persisted in `localStorage`. Tune it against the meter, not
against the source. Note the threshold is a `max`, so raising the slider below the learned
leak changes nothing — that is deliberate, a threshold under our own echo would fire on
every sentence we speak.

**And for three months it metered into a hidden element.** The meter shipped inside
`#voice`, but `f6a145d` had already dropped the written overlay in June: it removed
`voice.classList.add('on')` from `startVoice`/`endVoice` and left `.voice.on{display:flex}`
behind, so **`#voice` has been `display:none` ever since** — along with `vstate`, `vtrans`,
`vdbg` and the Interrupt / End voice buttons, which are still in there and still dead. The
test passed throughout, because it asserted `#vmeter.hidden` is false, and `hidden` is
false on an element inside a `display:none` parent. **An element's own visibility
attribute says nothing about whether it is on screen** — assert a box:
`getBoundingClientRect().height > 0`. The meter now lives in `.stagedock`, a flex column
holding it above the orb-focus voice toggle, so neither has to know whether the other is
there. The panel stays hidden: the violet shift is the design f6a145d chose, and this
restores the one piece of it that has to be readable, not the overlay.

**The dock is fixed to the viewport, not parked in `.stage`.** Put at the foot of the
presence panel it was still unreadable wherever that panel is `display:none` — under
`body.compact`, below 1100px and below 700px — which is to say on a small laptop, on a
phone, and for anyone using the compact-layout toggle: the same "fixed but still not
visible" shape as the three months above, one level up. `.stagedock` is a sibling of
`.stage` now and `--dock-x/-r/-w/-y` say where it lands: over the presence panel's own
grid cell by default, spanning the window in orb focus, and 12px above the composer
whenever the panel is off screen. `app.js` asks the **stage itself** whether it is
displayed (`syncDock`, toggling `.app.stageless`) rather than restating the breakpoints in
JS, so a breakpoint moved in the CSS alone cannot strand the meter again — and it
publishes the composer's measured height as `--composer-h`, because the textarea grows as
you type and a constant offset would put the meter over the box it is meant to sit above.
Three things learned by breaking them: the class goes on `.app`, not `body`, because three
orb-focus tests read `document.body.className` **whole**; the dock needs `z-index:40`,
since orb focus makes `.stage` a `z-index:30` overlay and at the old 6 a sibling dock was
painted over — a click on the voice toggle landed on the canvas; and the dock must be
hidden off the chat view (`body:not([data-view="chat"])`), which hides the composer too,
or it floats over the Overview page anchored to a composer of height 0. Docked above the
composer the meter draws its own hairline-and-blur panel so it is readable against chat
text; over the orb it stays bare. Verified at 1440 compact, 1000, 700 and 390 in headless
Chromium, asserting a real box and that it clears the composer.

**Voice notices go to the reminder tray.** The same hidden panel swallowed every voice
notice written to `#vtrans`: voice interruption switching itself off, a blocked or missing
microphone, the recognizer's errors — which end voice mode, so the pill just turned off —
and a failed transcription. `voiceNotice()` puts them in `#remtray`, which is fixed to the
window and so visible in every layout, orb focus and a phone included: one at a time, never
chimed or spoken since the microphone may be listening, cleared by Dismiss, a voice restart
or Space. Subtitles stay hidden; that was f6a145d's design. `/api/transcribe` answers
`failed` on its own, because its `ok: false` also means "heard nothing" — the old code
wrote that "nothing found" message as an error too, and only the hidden panel kept it from
putting a card up after every quiet moment.

Speech-to-text has three engines, chosen by `LAPTOP_AGENT_STT` (default `auto`):
**Riva** (hosted NVIDIA Parakeet, `riva` extra) is the accurate one — ~1s against Whisper's
~10s on the same clip, with punctuation. It is **gRPC, not REST**: the API catalog's
`/v1/audio/transcriptions` returns 404 on both hosts, so it needs `nvidia-riva-client`
against `grpc.nvcf.nvidia.com:443` with a `function-id` metadata header (that id selects
the model; `RIVA_SERVER` / `RIVA_ASR_FUNCTION_ID` / `RIVA_API_KEY` override, and the key
falls back to `OPENAI_API_KEY`). It takes PCM WAV only, so `auto` skips it for other media,
and a failed cloud call falls through to a local engine — losing the network costs quality,
not the transcription. `/api/health` reports the chosen engine as `stt.engine`, and the web
page uses that to record-and-post instead of trusting the browser's recognizer (a gear
toggle overrides; server speech has no recognizer running while we talk, so it barges in on
microphone **level** instead — see below). The two local engines:
**Vosk** (lightweight — ~50MB model, no PyTorch/ffmpeg; reads the 16kHz mono WAV the
browser encodes via Web Audio) and **Whisper** (accurate, heavy). `auto` prefers Vosk
when a model is present in `models/` (or `VOSK_MODEL`), else Whisper. `build_app_small.ps1`
bundles the Vosk path for a far smaller `JARVIS.exe`.

**A packaged app searches `sys._MEIPASS` too.** `--onefile` extracts `--add-data
"models;models"` into the temporary `_MEIPASS` directory, *not* next to the executable, so
`_resolve_vosk_model_path` looked only beside the .exe and never found the model the build
had just bundled. Since the small build ships Vosk **instead of** Whisper/PyTorch, that
left it with no working speech-to-text at all — and it is invisible to the unit suite,
because it only exists in a frozen build. Verified against a real artifact: the model is an
entry *inside* the exe and `dist/` holds nothing but `JARVIS.exe`. Order matters — a model
the user drops beside the .exe still wins over the bundled one. Measured on a build with
`torch`/`whisper` excluded: 3m40s to build, 162MB, boots and serves `/api/health` in 3s.
Use `LAPTOP_AGENT_PORT` to test a packaged build without colliding with a running app.

Riva selects its model by **function id**, never by a model name — an `OPENAI_SPEECH_MODEL`
style variable reaches nothing. `parakeet-1.1b-rnnt-multilingual-asr`
(`71203149-d3b7-4460-8231-1be2543a1fca`) is available and works, but measured on an English
clip it is *worse* than the English default: "comm music" for "calm music", and it drops
proper-noun casing ("youtube", "readme" where English gives "YouTube", "README"). Both ran
in ~0.9s. So English stays the default and `RIVA_ASR_FUNCTION_ID` / `RIVA_ASR_LANGUAGE`
switch to multilingual for dictating in another language. It has **not** been tested on
non-English audio — this machine has English-only voices to synthesise a clip with, so
someone needs to record themselves before claiming it helps.

## Recorder integration (REC-01, 2026-09-28)

- `recordings.py` parses requested durations and validates saved WAV bytes (16 kHz,
  mono, 16-bit, nonempty, at most 120 seconds). `recording_enabled` is a client capability;
  webui enables it, CLI/Tkinter do not. `record <seconds>` returns `data.record`.
- `/api/recordings` saves only; `/api/recordings/transcribe` explicitly requests speech
  processing; `/api/recording?name=...` serves same-origin private/no-store audio.
  Preserve the shared token/origin gate and filename confinement for these routes.
- Kept recordings are not disposable `/api/transcribe` uploads or retention artifacts.
  Browser capture owns its own microphone lifecycle and releases voice-chat resources.
  Save and transcript results stay with the original session across chat changes.
- The planner strips `_POLITE` and turns spoken numbers into digits before
  `recording_seconds`, so "can you record my voice for up to twenty seconds?" routes; the
  chat prompt names recording as a tool, or the model asks permission it cannot act on.
- AUTH-01 integration: keep recording commands/routes developer-only until artifacts
  have account ownership. A recording filename is not an authorization boundary.
- Tests: `test_recordings.py`, routing contract in `test_everyday_requests.py`/selfcheck,
  and `RecordingBrowserTests` in the existing opt-in browser CI suite.

## Riva deadline (VOICE-03, 2026-09-28)

`_riva_asr_backend` uses the SDK's `offline_recognize(..., future=True)` because its
blocking helper accepts no timeout. Poll `result(timeout=...)` in at most 100 ms slices,
check operation cancellation, and always cancel the future/close its channel. Do not
replace this with a background Python thread that leaves the RPC running after timeout.
`_riva_timeout` scales with WAV duration and accepts `RIVA_ASR_TIMEOUT_SECONDS` in (0,600].
A real deadline becomes TimeoutError, which auto mode can pass to the local fallback;
OperationCancelled bypasses ordinary errors, including a Stop racing an RPC failure.
Tests use a fake SDK/future; a separate real-SDK silent-loopback probe validates teardown.
This is independent of VOICE-02's broader grpc exception fallback and does not import
those stacked commits. It does not edit REC-01, health, auth, reminders or approval code.

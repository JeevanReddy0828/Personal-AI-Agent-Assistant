# Accounts, sessions and the personal role

Sign-in, sessions, what a personal account may do, account management, Google identity.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

**Accounts switch sign-in on (`accounts.py`, `sessions.py`).** With no accounts nothing
changes. Once any account exists, a disabled one included, every request needs a session,
loopback too: disabling the last account must not reopen the app. The first account is
made from this machine only (the settings popover or `python -m laptop_agent.accounts`), is
always `dev`, and `create(first=True)` decides "none yet" under the file lock, so two
set-up requests cannot both win. The CLI is OS trust and the way back in for a locked-out
owner. Decisions that each exist for a reason:
- Passwords are stdlib scrypt at OWASP's N=2^17, r=8, p=1 (0.6s here). hashlib's default
  `maxmem` of 32 MiB **refuses** those parameters, which a cheap-cost test never notices;
  `test_the_real_cost_hashes_and_verifies` runs the real one. Hashing happens outside the
  file lock: every request reads `accounts.json`, and a 0.6s hash under the lock stalled
  them all. Every refusal runs exactly one hash (a dummy for an unknown user), so timing
  does not say who exists.
- **At most two hash at once** (`HASH_SLOTS`). A hash holds 128 MiB outside the GIL and
  the server runs a thread per request: measured, four at once took the peak working set
  from 21 MiB to 534 MiB, so fifty sign-in attempts from a phone on the same wifi would ask
  for 6.4 GB. The backoff cannot stop that, because it counts a failure only once its hash
  has finished. A request that gets no turn within `HASH_WAIT` is answered 503 with
  `Retry-After`, and is **not** counted as a failure: nothing was checked, so treating it
  as a wrong password would lock the owner out because someone else was flooding.
- **Damaged sign-in storage fails closed** (Codex's review). `accounts.json` is read
  strictly (`storage.read_json_strict`): only a *missing* file means "no accounts". Read the
  generic way, a damaged file came back empty, which switched sign-in off and served the
  app and its API token to anyone; a damaged or invalid one now answers every request 503
  with how to recover. Neither store keeps a `.bak` or is ever read from one: a backup can
  bring back a deleted account, an old password or role, or a session revoked since it was
  written. A damaged `sessions.json` signs everyone out.
- **Saved chats are kept per account** (`jarvis_sessions:<account id>` in the browser),
  loaded only once `/api/me` says who is signed in: one origin-wide key let a personal
  account reopen the owner's chats on the same browser. Keyed by id, not name, since a
  name can be reused. Chats from before sign-in go to the first developer only, and a tab
  reloads when another tab signs in as someone else. Separation, not secrecy: whoever uses
  the browser profile can read its storage.
- On `http://localhost` a cookie is sent to **every port** of the host, so any other web
  server you run locally receives the session cookie. That is HTTP, not this code: the fix
  is HTTPS with a `__Host-` cookie (TLS-01), which the browser scopes to one origin.
- Sessions are server-side and persisted, keyed by the SHA-256 of the token, so a restart
  does not sign the desktop window out and the file holds nothing usable as a cookie. The
  store reloads when the file's stamp changes, because the CLI revokes from its own
  process. Every request re-reads the account, so a disabled account or a new role applies
  to the next request, not when the session ends.
- **A session is bound to the credentials it was granted under.** A new password or a
  disable moves the account's `epoch` on, every session records the epoch it was created
  with, and `_principal` refuses an older one. Revoking alone could not close the race the
  review found at the real hash cost: a sign-in checked against the old password finishes
  its 0.6s hash after `revoke_account` has run, then creates its session, which also came
  back after disable-then-enable. `create()` defaults to epoch 0, so a caller that leaves it
  out fails closed; changing your own password rebinds only the session that proved it.
  Every route reads the signed-in account through one check, `_signed_in_account()`: the
  Google routes (#145) were written before the epoch and carried their own copy, which
  merged cleanly and treated a session `/api/me` refused as signed in.
  Work already running stops too (REVOKE-01). `_handle` asks the request's own session again
  beside `check_cancelled()` (`access.ensure_signed_in`, bound by the web server with the
  principal), so every turn and every step of an agent run, a workflow or a `multi` is
  checked where Stop is; one that has ended raises `SignedOut`, a cancellation, and a JSON
  request answers 401. Not in `_account_limits`: prose never reaches it, so a workflow step
  that reads as prose was still routed and answered. `_run_many` asks again after its
  `gather`, which turns a stopped subtask into a bare `CancelledError('')` (3.11 to 3.14),
  or a batch answers "0 succeeded" instead of stopping. A loop that marks its steps in the
  control room finishes the step on `OperationCancelled` before re-raising: the workflow and
  autopilot loops caught only `Exception`, so a step that never ran stayed `working` for good
  (Codex's review); a routed command does the same, since a session that ends during the
  routing call stops the routed turn. A GET that dispatches (`/api/schedule`,
  `/api/agent-runs`, `/api/vault`) answers 401 like a POST: unhandled, `SignedOut` killed
  the worker thread and the client got no answer. Known limits: a command already inside a
  tool finishes, and scheduled jobs have no owner to check.
- `/auth/login` runs before the API-token check, like `/api/pair` (a new device has no
  token until it has the page), behind the Origin checks, a 4 KB body cap and a backoff
  per client and per username. The username key is scoped `local`/`lan`, so failures from
  the wifi cannot lock the owner out of the laptop.
- A signed-out `/` gets `signin.html`, a separate document, so the API token inside
  `PAGE` never reaches anyone who has not signed in.
- The page's fetch wrapper reloads on a **bare** 403 (a stale token after a restart), so a
  final refusal must say so: role and wrong-password 403s carry `X-Jarvis-Denied`, or a
  `personal` account would reload-loop on every developer route. A 401 now reloads too,
  and the server answers with the sign-in page.
- Tests swap `webui.ACCOUNTS`, `SESSIONS` and `_SIGNIN_LIMIT` for their own. One account
  written into the data directory the runner shares would put every other web test
  behind a sign-in page.

**A `personal` account is the assistant, not the machine (`access.py`).** It never acts on
the laptop itself (files, the screen, the camera, apps, windows, the shell, the browser,
music), never reaches the owner's mail, notes, indexed documents or job search, never sees
the internals, and never starts anything that acts on its own. The web server sets the
principal per request (`acting_as`, a ContextVar, which `asyncio.to_thread` carries into the
task runner). Four checks, because each covers a path the others miss:
- **The gate** refuses it HIGH and CRITICAL before anyone is asked: an approval card it could
  click through is no control. `ApprovalRequest(everyday=True)` marks the one HIGH action it
  may still take (clearing several reminders at once), asked as for anyone.
- **The orchestrator** checks the command *about to be dispatched* (`_account_limits`),
  inside the branch that dispatches. The first draft checked the top of `_handle` and was
  wrong twice. `_follow_up` rebuilds the command afterwards from history the *client* sends,
  so `[user: "email unread", assistant: "I could not find a time in that."]` plus "5pm"
  became `email unread 5pm` — and an inbox read is MEDIUM, which the gate lets through. And
  it refused prose the prose guard sends to the router ("schedule a meeting with bob").
  Routed and split commands come back through the same line as commands of their own. A
  developer form is refused with a reason; past that it is **default-deny where a command
  is claimed**, Codex's review of #140: only what is marked everyday (`access.EVERYDAY_*`,
  plus the pattern-chosen branches in `AgentOrchestrator._everyday`) is dispatched. Free
  text that matches none of it goes to the router, and a command the router made that is
  not everyday is refused — so a command added later without a decision is refused where
  it runs, not only in CI.
- **The approval broker** gives an account only its own cards. It broadcast every card to
  every open stream, so another account read the command, recipient or path and could
  answer it. A card the machine asked for itself (the ticker) goes to a developer.
- **The web server** lets it use an allow-list of routes (`_PERSONAL_ROUTES`), so a route
  added later is closed to it until decided. The deny-list it replaced missed
  `/api/pipeline`, whose resume loader reads any path.

Both lists are copies of what the dispatchers match, and a copy fails by omission.
`DispatchMirrorTests` reads every literal form out of `_DISPATCH` with `ast`: each must be
refused, or listed everyday *as itself* — never merely covered by a broader everyday prefix,
or `list secrets` added under `list ` would be dispatched for a personal account — and every
listed form must be one the dispatchers match (a phantom `knowledge` prefix would refuse
"knowledge is power"). It counts the branches chosen by a pattern, which it cannot read, and
`PersonalContractTests` plus one phrase per pattern branch hold `_everyday` to them: each of
its eight patterns was removed in turn and caught, the routing contract alone missed two.
Every rule here was broken on purpose and every break was caught. `read file` is LOW, which
is why files are on the list at all: without it a personal account could `read file .env`.
Data is shared on purpose: the personal account is the owner in a safer everyday mode, not
another person (Jeevan's answer, 2026-09-28). So reminders, timers, lists, remembered facts,
generated pictures and documents stay one store, the chat prompt carries the owner's facts,
and per-account data is not planned; what it is refused limits scope, not privacy. The
page hides `.devonly` controls under `body[data-role="personal"]` and greets the account by
its own name; the server is the enforcement. Known limits: the gate's prompt lock serialises
approvals across accounts, and attachments are developer-only, because every use of one is a
file command.

**Accounts are managed from this computer** (`GET`/`POST /api/accounts`, the Accounts panel in
the System status drawer, "Manage accounts" in the settings popover). Developer-only by the
route allow-list and again in `_account_admin`, and loopback-only like setting sign-in up, so
a session carried to a phone cannot add a developer. Every change asks for the developer's
own password again (the same backoff as sign-in), so a session left signed in cannot mint
another account. Nobody demotes, disables or deletes themselves here; the command line
stays the way back in. The store refuses, under its lock, any web change that would leave
no enabled developer (`keep_developer=True`), since two developers demoting each other at
once would otherwise both succeed; the command line does not pass it. Disabling, resetting
or deleting ends that account's sessions: a disabled account is refused on its next request
anyway, but without the revoke a cookie taken before the disable came back to life when the
account was enabled again, which is the one test that could tell.

### AUTH-01 phase 2a — Google identity handoff (Codex, 2026-09-28)

Branch `codex/google-signin` starts at auth-admin `1327dea`; it is intentionally stacked
on #142, not main. `google_oidc.py` owns at most 32 in-memory, ten-minute flows and four
concurrent code exchanges. Each has PKCE S256, nonce, single-use launch/state, a separate
external-browser Lax cookie, and an initiating-window HttpOnly Strict proof cookie.
Callback only verifies identity and marks a result ready. Completion in the original
window checks its proof, session and entire account snapshot before issuing a session.
Only our fixed HTTPS token exchange supplies an ID token; never accept a browser JWT.
Tokens are discarded after claim checks; no Google refresh/access token is persisted.

`/auth/google/start|complete|cancel|unlink` use small JSON bodies and existing origin
checks before sign-in. Only launch/callback GETs accept cross-site navigation, on the
canonical loopback host, with one-time tickets or state plus browser binding. Existing
API token checks stay in place. Link/unlink require a password step-up under
`_SIGNIN_LIMIT`; account-only Google recovery uses the documented local CLI password reset.
Failures are recorded, never the request (Claude, landing #145): the token request carries
the authorization code and the client secret, and a reply can carry tokens, so
`google/token` keeps only the HTTP status and the OAuth `error` code, `google/id-token` the
name of the claim check that refused (`time` is a wrong laptop clock), and
`google/callback` / `google/browser` an exception's type, never its text. A refused client
(`invalid_client`, `unauthorized_client`, `redirect_uri_mismatch`) names the settings to
check; "Start again" would be advice to retry something that cannot work.
No Gmail policy is widened. Phase 2b must add account-scoped encrypted credentials,
fail-closed revocation and narrow mailbox approvals before exposing personal mail.

UI integration is in Settings and the sign-in document, leaving Claude's health/status
drawer regions untouched. `google_auth.js` is inlined into both documents under the
existing CSP nonce. Provider opener isolation can sever a popup reference; `popup.closed`
is not proof of cancellation. Use bound completion, explicit Cancel and expiry instead.
The browser CI entry now runs `test_browser_*.py`, including auth/account suites and the
new fake-Google suite. Shared review and decisions remain in `claude/pair-log`.

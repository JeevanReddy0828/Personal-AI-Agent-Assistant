# Isolated app exploration — 2026-10-02

Base: main 0ce6847. Reproduction: install Playwright/Chromium, then run
`python -B docs/evals/isolated_app.py <this-laptop-LAN-IPv4>`.
The script sets BOTH LAPTOP_AGENT_PORT and LAPTOP_AGENT_DATA_DIR before importing the
server, overrides uploads into the same scratch tree, disables dotenv and credentials,
and restricts network connections. It stops its own server and removes only its own
temporary store when done. The persisted JSON records the outcome of each scenario.
No production data, accounts, resumes, browser profiles or firewall settings are changed.

## Results

- `image a brass compass on a desk` and `draw me a picture of a green kite` reach the
  image backend with the expected prompt, write separate images, and display decoded
  images in chat. A simulated backend timeout is shown and does not create a third file.
  The image backend is a fixture, not a hosted-service test. The real web approval gate
  is retained: MEDIUM risk is automatically approved by current web policy; a visible
  Approve/Deny dialog is not expected for these image requests.
- Jobs: add a company containing HTML-like text, confirm it is escaped, change stage
  to interview, reload and verify persistence. Empty tailoring input explains what is
  missing. Remove the scratch job through the page.
- Pipeline: save a synthetic resume and contact profile, reload, and verify the job card
  and profile persist. No document overflow at 390 or 1440 pixels. Clear leads retains
  the tracked application.
- LAN: an independent Chromium client uses the laptop's actual LAN address; the server
  reports `local=false`. The unpaired page/API return 401, a wrong passcode is rejected,
  the correct passcode unlocks the API (200), and a third fresh client remains denied.
  This is a second client on the same laptop, not a second physical device. Plain HTTP
  is not a secure browser context; microphone/HTTPS behavior was not validated.
- No JavaScript page errors in the local or LAN client during the checked scenarios.

No product defect was reproduced in these paths. Initial harness failures came from
Playwright's eval-based polling under CSP and incorrectly expecting a MEDIUM-risk
approval dialog. The final harness uses the repository's evaluate/poll pattern and
checks actual policy. Hosted FLUX availability/quality, real Jobright access, model-backed
resume tailoring, cross-device Wi-Fi/firewall reachability, and microphone permissions
remain outside the evidence from this run. Do not call this full production validation.

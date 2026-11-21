# `docs/screenshots/` — images for the front page

## Purpose

The pictures in the repository's root `README.md`, showing what the app looks like.

## How it connects

Only the root `README.md` uses these, by relative path (`docs/screenshots/<name>`). Nothing
in the app or the tests reads them, so renaming one means updating the README's reference.

## Usage

Take new screenshots from a running app with sample data (never your own mail, notes or
location: the repository is public), keep the names, and replace the files. Prefer JPEG for
large screenshots to keep the repository small.

## Contents

| File | Shows |
|---|---|
| `chat-home.jpg` | The chat workspace with the orb. |
| `chat-tools.jpg` | Instant local answers in the chat. |
| `diagram.jpg` | A diagram drawn inside a reply. |
| `system-status.jpg` | The System status drawer with the Setup list. |
| `orb-focus.jpg` | Orb focus mode. |
| `mobile.jpg` | The app on a phone. |
| `sign-in.png` | The sign-in page. |
| `approval-card.jpg` | A HIGH RISK approval card for a download, before anyone answers it. |
| `agents-off.jpg` | `agents off`, then an agent run refused while agents are off. |
| `progress-line.jpg` | The live status line while a document is written. |
| `document-ready.jpg` | The same request finished: a PDF linked in the reply. |
| `self-check.jpg` | `self check` passing on the real router. |
| `latency.jpg` | The `latency` report from the turns traced in that session. |

The last six were taken on 2026-11-20 from a throwaway instance (its own
`LAPTOP_AGENT_PORT` and `LAPTOP_AGENT_DATA_DIR`) in headless Chromium at 1440×900, with the
page allowed to reach only that instance. The approval card was denied, and the document
was written into the throwaway data folder.

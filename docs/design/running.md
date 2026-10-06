# Running instances safely

Start commands, and why a throwaway instance needs its own data directory.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

## Running it

```powershell
$env:PYTHONPATH="src"
python -m laptop_agent.cli                                              # terminal
python -m laptop_agent.webui --desktop                                  # desktop app window (or: laptop-agent-deck)
python -m laptop_agent.webui                                            # browser tab
```

**A throwaway instance needs `LAPTOP_AGENT_DATA_DIR`, not just `LAPTOP_AGENT_PORT`.** The
port is the only thing a second port isolates: the data directory is still the real one, so
anything the throwaway instance is told to remember, schedule or be reminded of lands in
the user's own store. This has now happened twice — 20 `loadtest_N` keys in "what do you
remember about me?", and three test reminders in the real reminder list. Always:

```powershell
$env:LAPTOP_AGENT_PORT="8791"; $env:LAPTOP_AGENT_DATA_DIR="$env:TEMP\jarvis-scratch"
```

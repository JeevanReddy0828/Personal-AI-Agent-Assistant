# `src/` — source code

## Purpose

Holds the one Python package, `laptop_agent`. The `src/` layout keeps the package from
being imported by accident from the repository root: you either install it or put `src`
on the path, so tests always exercise the code as it will be shipped.

## How it connects

- `pyproject.toml` (repository root) declares the package, its optional extras and its
  console commands (`laptop-agent`, `laptop-agent-deck`, `laptop-agent-dashboard`).
- `tests/run_tests.py` puts `src` on the path before running the suite.
- `packaging/` bundles this package into a Windows executable.

## Usage

```powershell
$env:PYTHONPATH="src"
python -m laptop_agent.webui      # or install it: pip install -e .   then: laptop-agent-deck
```

## Contents

| Path | What it is |
|---|---|
| `laptop_agent/` | The whole application; start with its README. |

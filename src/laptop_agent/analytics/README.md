# `analytics/` — forecasts and diagnostics, computed rather than guessed

## Purpose

Pure numerical cores for questions about your own data: "what will revenue be next
quarter?", "what drives churn?", "which months were unusual?". A language model would
produce plausible numbers; these functions compute them, test them on held-out data and
say plainly when there is too little data to claim anything. They are **pure**: standard
library only, no file access, no app state, so they are easy to test and reuse.

## How it connects

```
 "forecast revenue in sales.csv"            "what drives churn in data.csv"
        │                                            │
 tools/forecast.py                          tools/diagnostics.py
   reads the CSV, checks dates and gaps,      reads the CSV, picks the columns,
   then calls ─────────────┐                  then calls ─────────────┐
                           ▼                                          ▼
               analytics/forecast.py                     analytics/diagnostics.py
                           │                                          │
                           └─────── a result object ──────────────────┘
                                         ▼
               the tool words it (no number without a test, no band without bounds)
               and the web page draws the forecast chart from its data
```

Files, dates and odd cells are the **tools'** job; this folder only ever sees clean
sequences of numbers.

## Usage

```python
from laptop_agent.analytics.forecast import forecast
from laptop_agent.analytics.diagnostics import anomalies, drivers

result = forecast([112, 118, 132, 129, 121, 135, 148, 148, 136, 119, 104, 118] * 3, horizon=3)
print(result.enough_data, result.method)

print(anomalies([10, 11, 10, 12, 50, 11]).scores)          # signed modified z-scores

# One row per observation, one value per feature.
rows = [[1, 5], [2, 3], [3, 6], [4, 2], [5, 7], [6, 1], [7, 8], [8, 4], [9, 9], [10, 2]]
target = [2.1, 3.9, 6.2, 8.1, 9.8, 12.2, 13.9, 16.1, 18.0, 20.2]
result = drivers(rows, target, feature_names=["ads", "price"])
print(result.standardized_coefficients, result.out_of_sample_r2, result.warnings)
```

In the app, ask in words: `forecast <column> in <file.csv> [by <date column>] [for N]`,
`what drives <column> in <file.csv>`, `anomalies in <column> in <file.csv>`. These are
developer-only commands, since they read a file.

## Contents

| File | What it does |
|---|---|
| `forecast.py` | `forecast(values, horizon, season=, level=)`: detects a season on an early part of the history, picks a method against two baselines on later stretches, and calibrates intervals on a final stretch it never chose with. Returns a `Forecast`; `enough_data=False` when it cannot test anything. `detect_season()` is exposed on its own. |
| `diagnostics.py` | `drivers(features, target, …)`: least-squares associations fitted on the earlier rows and scored on the untouched later rows (association, never cause). `anomalies(values, threshold=3.5)`: robust modified z-scores; when the median deviation is zero, differing values are listed as unscored rather than given an infinite score. |
| `__init__.py` | Package marker ("deterministic, dependency-free analytics"). |

## Read next

- `docs/forecasting.md` and `docs/analytics.md`: the contracts these functions keep (read
  them before changing anything here).
- `tools/README.md` for the CSV-facing commands.
- Tests: `test_forecast.py`, `test_analytics_diagnostics.py`, `test_forecast_tool.py`,
  `test_diagnostics_tool.py`.

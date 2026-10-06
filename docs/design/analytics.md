# Analytics: forecasting and diagnostics

Notes on wiring the pure cores; the contracts are docs/forecasting.md and docs/analytics.md.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

## ANALYTICS-04 update — 2026-10-01

ANALYTICS-04 adds pure diagnostics in analytics/diagnostics.py. Read docs/analytics.md before wiring: prefix-only OLS, held-out R2/MAE, standardized associations (not causality), VIF/sample warnings, explicit singular refusals, and MAD-zero unscored deviations. No command or app-data prediction is added.

## ANALYTICS-01 forecasting core — 2026-10-01

Forecasting (ANALYTICS-01) lives in analytics/forecast.py: pure stdlib, no app data or IO.
Read docs/forecasting.md before integrating. Default season detection and parameter tuning
use only an initial prefix; separate chronological blocks select against both baselines
and calibrate horizon-specific empirical intervals. Unsupported data/intervals must be
shown as such, not narrated as a confident prediction. Calendar frequency is not a season.
CSV commands, charts, OLS/MAD and job/tier predictions remain separate work.

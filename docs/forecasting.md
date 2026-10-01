# Forecasting core (ANALYTICS-01)

`laptop_agent.analytics.forecast` is a Python 3.11+ standard-library module. It has no IO,
randomness, app-data access or third-party dependency. This PR provides the numeric core;
CSV/date parsing, commands, charts and narration belong to ANALYTICS-03.

```python
from dataclasses import asdict
from laptop_agent.analytics.forecast import forecast

result = forecast([3 + 2*i for i in range(90)], horizon=3, level=0.8)
assert result.method == "holt"
assert result.points == (183.0, 185.0, 187.0)
payload = asdict(result)  # json.dumps(payload, allow_nan=False) is supported
```

## Input contract

Pass an ordered sequence of finite Python ints/floats, equally spaced in time. Booleans,
strings, missing values and NaN/infinity are rejected with `ValueError`; the core never
sorts, fills gaps, resamples or coerces cells. Limits: 4,096 points, absolute magnitude
at most 1e100, integer horizon 1..48, and interval level strictly between 0 and 1.

- `season=None` (default): detect a candidate on the initial training prefix using
  detrended autocorrelation, periods 2..24, at least three complete prefix cycles.
  Near-flat or deterministic linear data has no detected season. Choose the shortest
  lag within 0.03 of the highest correlation, with correlation at least 0.6.
- `season=0`: disable season discovery and seasonal candidates.
- `season=2..120`: an explicit, user-known period. It needs at least two complete seasons
  in the fitting prefix and further observations for scoring. It adds candidates and
  a seasonal baseline; it does not force a seasonal method to win.

Calendar frequency determines labels, not a proven seasonal cycle. A CSV loader should
use `season=None` unless the user supplies a known cycle. Explicit annual weekly cycles
can use 52; automatic discovery intentionally stays bounded at 24.

## Result contract

`Forecast` is an immutable dataclass with tuple sequences. JSON serialization turns tuples
into arrays and missing values into null. The public fields are:

| Field | Meaning |
| --- | --- |
| `method` | `naive`, `seasonal_naive`, `ses`, `holt`, or `holt_winters` |
| `points` | Forecast in input units, one value for each requested future period |
| `lower`, `upper` | Horizon-specific empirical bounds, or null per point when unsupported |
| `level` | Requested nominal interval level; not a guarantee of future coverage |
| `season` | Candidate period, or null; a nonseasonal winner can still have a candidate period |
| `mase`, `baseline_mase` | Mean absolute scaled error on common selection origins; null if the scale is zero or numerically unstable |
| `enough_data` | Whether there were enough observations and origins for model comparison |
| `reason` | Why a smoothed model won, a baseline was retained, or comparison is unsupported |
| `baseline_method` | Better of the available naive and seasonal-naive competitors |
| `mae`, `baseline_mae` | Absolute errors in input units, on the same selection origins |
| `backtest_origins`, `calibration_origins` | Number of origins actually used in each block |
| `interval_reason` | Whether empirical bounds are available, and why |

Fewer than eight points, fewer than two explicit seasons, or too few full-horizon
selection origins gives `enough_data=False`, null scores and null bounds. With nonempty
input, points then contain only the repeated last observation, explicitly an unsupported
baseline, not a fitted forecast. Empty input returns empty points/bounds. A tool must
surface this state instead of narrating a successful prediction.

A supported point forecast can still have null bounds: insufficient interval calibration
is separate from insufficient model-comparison history. Display `interval_reason`; do
not draw a zero-width band, claim the nominal level, or silently shorten an explicitly
requested horizon. Default horizon 3 in the tool is reasonable.

## Model comparison and calibration

1. The initial prefix is the larger of four points, the first third of the series, and
   two explicit seasons. Season discovery and smoothing-parameter tuning use only it.
   SES, Holt and additive Holt-Winters tune a small fixed grid using one-step rolling
   validation inside this prefix. Settings remain frozen afterwards.
2. Later expanding training windows predict the full requested horizon. Selection ends
   around two-thirds of the series (moved later when needed for four complete origins).
   Every forecast sees exactly the observations before its origin. Selection targets
   end before the calibration block begins.
3. Compare mean MASE over the same horizons/origins, using each origin's own training
   mean absolute lag-difference as its scale (seasonal lag if a period exists, else 1).
   If any scale/result is undefined, use MAE consistently for every competitor and return
   null MASE. Choose the better baseline first. A smoother needs strict improvement
   (relative tolerance 1e-9); ties keep the baseline.
4. Freeze that choice. A separate later block supplies signed forecast errors for each
   horizon. Bounds are final point forecasts plus linearly interpolated empirical error
   quantiles at `(1-level)/2` and `(1+level)/2`. No Gaussian distribution is assumed and
   different horizons' errors are never pooled. At least `max(10, ceil(2/(1-level)))`
   calibration origins are required. Each evaluation block is deterministically thinned
   to at most 64 origins; tuning uses at most 16.
5. Refit the chosen method's states to all observations using the frozen settings.
   Naive repeats the last value; seasonal-naive repeats the last cycle; SES models level;
   Holt adds linear trend; Holt-Winters adds a fixed-amplitude seasonal component.

Backtest selection is evidence about the observed sample, not proof of future skill.
Winning against the baseline on the selection block is an invariant of this API, so
validation also measures untouched future observations. Empirical intervals assume the
calibration errors remain representative: regime changes, dependence, outliers and
multiplicative/changing seasonality can invalidate coverage. Overlapping origins have
correlated errors; these are empirical prediction bands, not conformal guarantees.
A deterministic constant series can legitimately have a zero-width interval and 100%
coverage. No app routing, job-search decision or financial decision uses this core yet.

## Validation and measured limits

`python -B tests/run_tests.py test_forecast.py` exercises synthetic fixtures only.
The fixed test ensemble uses seeds 1000..1029 for each of trend, season and noisy level,
180 input points and six untouched future points. It checks selection is no worse than
the best baseline, per-family 80% interval coverage is 70..90%, and aggregate future MAE
is no worse than the baseline. Other tests check exact deterministic structure, input
limits, JSON safety, zero scales, chronology, quantiles and sparse calibration.

A second independent measurement used seeds 5000..5099, 100 series per family, with the
same 180/6 split. Each series is `30 + trend + seasonal + N(0,1)` from a local seeded
stdlib generator; trend is 0.18 per step where enabled, and season is amplitude-6 sine
with period 7 where enabled. No licensed/external dataset or app data was used.

| Family | Backtest no worse | Held-out 80% coverage | Future MAE | Baseline MAE |
| --- | --- | --- | --- | --- |
| Trend | 100/100 | 77.0% | 0.966 | 1.306 |
| Trend + season | 100/100 | 78.3% | 0.971 | 1.537 |
| Noisy level | 100/100 | 79.3% | 0.835 | 1.153 |

The 4,096-point / 48-horizon limit took 0.358 s on the development laptop for a synthetic
seasonal series. This is a sample timing, not a guarantee for every machine.
Seventeen independent in-memory mutations were caught: minimum history, two-season guard,
prefix-only season/tuning, selection/calibration separation, better baseline, strict
improvement, origin-local MASE, no lookahead fit, empirical quantiles, per-horizon errors,
calibration minimum, Holt trend, Holt-Winters phase, SES smoothing and unit-scale normalization.

Method references: [rolling-origin evaluation](https://otexts.com/fpp3/tscv.html),
[scaled forecast errors](https://otexts.com/fpp3/accuracy.html),
[Holt-Winters components](https://otexts.com/fpp3/holt-winters.html), and
[prediction intervals](https://otexts.com/fpp3/prediction-intervals.html).
Implementation choices such as the fixed grid, thresholds and chronology are project
policies, not prescriptions from those references.

# Associations and robust anomalies (ANALYTICS-04)

This pure standard-library API complements the forecasting core. It performs no IO,
loads no app data, and adds no command or chart. Claude owns later tool integration.
Neither a large coefficient nor an anomaly label establishes causality or requires action.

## Regression contract

```python
from dataclasses import asdict
from laptop_agent.analytics.diagnostics import drivers
result = drivers(features, target, feature_names=("hours", "calls"), holdout=0.2)
```

`features` is an ordered sequence of equal-length numeric rows; `target` has one number
per row. Supply 1–24 features, at most 4,096 rows, and finite real numbers with absolute
value at most 1e100. Booleans, strings, missing values and ragged data are errors. Names
are optional (x1, x2, ... by default), unique, nonempty strings. No rows are sorted,
filled, dropped or sampled. `holdout` must be a finite number strictly between 0 and 1.

The last `ceil(n * holdout)` rows are untouched test observations. The prefix must have
at least `max(4, feature_count + 2)` rows, and the tail at least two. Rows must be ordered
as the intended use requires; random shuffling is not performed. Features must be
available at prediction time and must not encode future target values. The library
cannot detect semantic leakage in a caller's columns.

Fit an intercept and all supplied features, with no feature selection. Both X and Y use
training-only population means and deviations. Reorthogonalized modified Gram-Schmidt QR
solves the centered design without normal equations. Coefficients stay fitted to the
prefix: there is no subsequent full-data refit. Predictions are for the held-out tail,
not an invitation to forecast unseen future covariates.

`Drivers` is a frozen dataclass; sequences are tuples. Its JSON-safe fields are:

| Field | Meaning |
| --- | --- |
| feature_names | Input order, not a ranking. |
| standardized_coefficients | Change in training Y standard deviations per training X standard deviation, holding other supplied features fixed; null on refusal. |
| vif | Variance inflation factors from the training design, same feature order; null on refusal. |
| train_rows, test_rows | Exact chronological partition. |
| predictions | Prefix-fit predictions for the tail; empty on refusal. |
| out_of_sample_r2 | `1 - sum((actual - prediction)^2) / sum((actual - tail_mean)^2)` on the tail only. Negative values remain negative. |
| mae | Average absolute error on the tail, in target units. |
| baseline_mae | Tail MAE of the fixed training-target mean, in identical units. |
| enough_data, reason | Explicit support/refusal and explanation. |
| warnings | Association caveat, sample/collinearity warnings, or undefined R2 explanation. |

Warnings fire below **10 training rows per feature** (intercept excluded) and when any
**VIF >= 10**, including joint dependencies invisible to a pairwise correlation check.
These thresholds are caution heuristics, not significance tests. Rank deficiency or
numerical inseparability (QR residual norm <= 1e-10 times sqrt(training rows)) refuses
coefficients instead of choosing an arbitrary solution. Constant training features or
targets also refuse standardized associations. The caller should remove redundant
features deliberately, not silently drop them or interpret a singular fit.

Too few rows or an unfit design returns `enough_data=False`, null scores/coefficients and
empty predictions. Malformed inputs raise ValueError. An unrepresentable extrapolation
also returns an explicit refusal. Constant or numerically unresolved test targets return
null R2, while finite errors/predictions remain available. Never relabel null as zero or
clamp a negative R2. No in-sample R2, p-value, confidence interval, causal ranking or
promised predictive accuracy is exposed. Compare held-out errors with the baseline and
show sample counts and warnings, especially before calling anything a useful predictor.

## Anomaly contract

```python
from laptop_agent.analytics.diagnostics import anomalies
result = anomalies(values, threshold=3.5)
```

Input is an ordered numeric sequence under the same 4,096-row and magnitude limits.
This is a batch diagnostic: its center and scale use all supplied values. It is **not**
an online detector, a time-series residual model, or an out-of-sample forecast. Trend or
seasonality can be labelled unusual by this simple global measure.

`Anomalies` is immutable and contains `median`, raw `mad`, positive `threshold`, signed
`scores` aligned with every original row, zero-based `indices` for scored candidates,
`unscored_indices`, and `warnings`. The modified z score is
`0.6744897501960817 * (value - median) / MAD`; flag only `abs(score) > threshold`.
The default 3.5 is a screening convention, not a probability or automatic removal rule.
See [NIST's outlier guidance](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35h.htm).

When MAD is zero, values equal to the median get score zero. Other values get null and
are listed only in `unscored_indices`; no infinite score or confident anomaly flag is
invented. Nonrepresentable scores likewise become null with a warning. Always show
unscored deviations separately from scored candidates. Fewer than ten observations
carry a small-sample warning. Empty input returns null center/scale and empty tuples.
Input is never changed, sorted in place, filtered or imputed.

## Validation and interpretation

Fifteen deterministic tests cover a known orthogonal OLS solution, tail-only R2/MAE,
training-mean baseline, changed tail targets/features, negative skill, exact singularity,
joint collinearity, constant targets, thin samples, input rejection, immutable results,
very small/large units and extrapolation overflow. Anomaly fixtures cover signs, original
indices, strict thresholds, MAD zero, numeric overflow and seeded normal noise with two
injected spikes. Tests pass on Python 3.11 and 3.14.

Seventeen independent in-memory undo-and-fail mutations check target/feature chronology,
negative R2, both baseline definitions, VIF/collinearity/sample/rank safeguards,
coefficient signs, MAD definition, zero-MAD handling, two-sided strict flags, score signs
and overflow. The target-leak mutation initially survived a symmetric tail fixture;
changing tail variance made the test distinguish correct training-only standardization.
No mutation changed production files.

These synthetic checks verify behavior, not usefulness on the app's sparse job history
or timing data. No job-response, route/tier or financial prediction is claimed. Regression
validation follows the distinction between historical fit and future performance in
[Forecasting: Principles and Practice](https://otexts.com/fpp3/selecting-predictors.html).

Maximum-size synthetic check (seed 409624): 4,096 rows and 24 independent normal
features, coefficients (j+1)/24, noise standard deviation 0.1. One laptop run took
0.452 seconds; held-out R2 0.99884, MAE 0.07981 versus mean-baseline MAE 2.31560.
This is a workload/example measurement, not a latency or real-data accuracy guarantee.


Final local validation: Python 3.11 ran 1,633 tests with 80 optional skips in 204.9 seconds,
using the repository's isolated runner and an in-memory stub of system_metrics only
inside PrefixFuzzTests.test_no_command_word_raises_whatever_follows_it. The unmodified
local runs were interrupted after repeatedly collecting real Windows GPU counters in
that unrelated fuzz path. Metrics tests stayed unchanged. CI runs the unmodified suite.

An independent NumPy cross-check (validation environment only; no package dependency)
covered seeds 7300–7499, each with 120 rows and four standard-normal features. On odd
seeds X4=X1+X2+0.1*X4; Y=4+2*X1-X2+0.5*X3+0.75*X4 plus normal noise SD 0.3. Fit the first
96 rows and score the final 24. Against numpy.linalg.lstsq on training-standardized
columns, maximum absolute errors were 5.33e-15 for coefficients, 5.64e-14 for predictions
and 5.55e-16 for R2. Maximum relative VIF error against 96*diag(inv(Z.T@Z)) was 2.97e-13.

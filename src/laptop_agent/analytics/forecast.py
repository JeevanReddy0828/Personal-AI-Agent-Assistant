"""Forecast equally spaced numeric observations; never infer dates or fill gaps.

An initial prefix selects a season and smoothing settings. Later rolling origins
choose a method against both baselines; a disjoint final block calibrates errors
separately for each requested horizon. Nothing fits to an origin's future values.
The final chosen method is refitted to all observations, with its settings frozen.
See docs/forecasting.md for the public contract and empirical-interval limitations.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import fmean
from typing import Sequence

MAX_POINTS = 4096
MAX_HORIZON = 48
MAX_SEASON = 120


@dataclass(frozen=True)
class Forecast:
    method: str
    points: tuple[float, ...]
    lower: tuple[float | None, ...]
    upper: tuple[float | None, ...]
    level: float
    season: int | None
    mase: float | None
    baseline_mase: float | None
    enough_data: bool
    reason: str
    baseline_method: str
    mae: float | None
    baseline_mae: float | None
    backtest_origins: int
    calibration_origins: int
    interval_reason: str


@dataclass(frozen=True)
class _Spec:
    method: str
    season: int = 0
    alpha: float = 0.3
    beta: float = 0.1
    gamma: float = 0.1


def _values(values: Sequence[float]) -> tuple[float, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError("values must be an ordered numeric sequence")
    if len(values) > MAX_POINTS:
        raise ValueError(f"Use at most {MAX_POINTS} observations")
    if any(type(v) not in (int, float) or abs(v) > 1e100 or not math.isfinite(v) for v in values):
        raise ValueError("Observations must be finite numbers with magnitude at most 1e100; no missing values")
    return tuple(float(v) for v in values)


def _trend(y: Sequence[float]) -> float:
    center = (len(y) - 1) / 2
    mean = fmean(y)
    return sum((i - center) * (v - mean) for i, v in enumerate(y)) / sum(
        (i - center) ** 2 for i in range(len(y)))


def detect_season(values: Sequence[float]) -> int | None:
    """Conservative detrended autocorrelation, periods 2..24 with three cycles.

    This is a candidate, not proof of seasonality. forecast() calls it only on
    the initial prefix, before its model-selection and calibration blocks.
    """
    y = _values(values)
    if len(y) < 6:
        return None
    magnitude = max(abs(v) for v in y)
    if not magnitude:
        return None
    y = tuple(v/magnitude for v in y)
    slope, mean = _trend(y), fmean(y)
    residual = [v - mean - slope * (i - (len(y) - 1) / 2) for i, v in enumerate(y)]
    variance = sum((v - mean) ** 2 for v in y)
    if not variance or sum(v * v for v in residual) <= variance * 1e-12:
        return None
    scores = {}
    for lag in range(2, min(24, len(y) // 3) + 1):
        a, b = residual[:-lag], residual[lag:]
        am, bm = fmean(a), fmean(b)
        denominator = math.sqrt(sum((v-am)**2 for v in a)) * math.sqrt(sum((v-bm)**2 for v in b))
        scores[lag] = sum((x-am)*(z-bm) for x,z in zip(a,b)) / denominator if denominator else 0
    best = max(scores.values(), default=0)
    return next((lag for lag, score in scores.items() if score >= max(0.6, best - 0.03)), None)


def _predict(y: Sequence[float], horizon: int, spec: _Spec) -> tuple[float, ...]:
    if spec.method == "naive":
        return (y[-1],) * horizon
    if spec.method == "seasonal_naive":
        return tuple(y[-spec.season + h % spec.season] for h in range(horizon))
    a, b, g = spec.alpha, spec.beta, spec.gamma
    if spec.method == "ses":
        level = y[0]
        for value in y[1:]:
            level = a * value + (1-a) * level
        return (level,) * horizon
    if spec.method == "holt":
        level, trend = y[0], _trend(y[:min(8,len(y))])
        for value in y[1:]:
            previous = level
            level = a * value + (1-a) * (level + trend)
            trend = b * (level-previous) + (1-b) * trend
        return tuple(level + (h+1)*trend for h in range(horizon))
    if spec.method != "holt_winters":
        raise ValueError("Unknown forecasting method")
    period = spec.season
    first, second = fmean(y[:period]), fmean(y[period:2*period])
    trend = (second-first) / period
    level = first + trend * (period-1) / 2
    seasonal = [y[j] - (level + (j-period+1)*trend) for j in range(period)]
    for t in range(period, len(y)):
        old_level, old_season = level, seasonal[t % period]
        level = a * (y[t]-old_season) + (1-a) * (level+trend)
        seasonal[t % period] = g * (y[t]-old_level-trend) + (1-g)*old_season
        trend = b * (level-old_level) + (1-b)*trend
    return tuple(level + (h+1)*trend + seasonal[(len(y)+h) % period] for h in range(horizon))


def _origins(start: int, end: int, limit: int = 64) -> tuple[int, ...]:
    """End is exclusive; deterministic thinning bounds work without using future data."""
    count = end-start
    if count <= 0:
        return ()
    if count <= limit:
        return tuple(range(start,end))
    return tuple(start + i*(count-1)//(limit-1) for i in range(limit))


def _tune(y: Sequence[float], method: str, season: int) -> _Spec:
    start = 2*season if method == "holt_winters" else 2
    origins = _origins(start,len(y),16)
    candidates = [_Spec(method,season,a,b,g)
                  for a in (0.1,0.3,0.6,0.9)
                  for b in ((0.0,0.1,0.3) if method != "ses" else (0.0,))
                  for g in ((0.0,0.1,0.3) if method == "holt_winters" else (0.0,))
                  if g <= 1-a]
    if not origins:
        return _Spec(method,season)
    return min(candidates, key=lambda spec: fmean(
        abs(y[t]-_predict(y[:t],1,spec)[0]) for t in origins))


def _errors(y: Sequence[float], spec: _Spec, origins: Sequence[int], horizon: int) -> tuple[tuple[float, ...], ...]:
    errors = [[] for _ in range(horizon)]
    for t in origins:
        predicted = _predict(y[:t],horizon,spec)
        for h, point in enumerate(predicted):
            errors[h].append(y[t+h]-point)
    return tuple(tuple(row) for row in errors)


def _scores(errors: tuple[tuple[float, ...], ...], scales: Sequence[float]) -> tuple[float, float | None]:
    mae = fmean(abs(error) for row in errors for error in row)
    ratios = [abs(error)/scale for row in errors for error,scale in zip(row,scales)] if all(scales) else []
    try:
        mase = fmean(ratios) if ratios and all(math.isfinite(v) for v in ratios) else None
    except OverflowError:
        mase = None
    return mae, mase


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    index = (len(ordered)-1) * probability
    low = math.floor(index)
    fraction = index-low
    return ordered[low]*(1-fraction) + ordered[min(low+1,len(ordered)-1)]*fraction


def forecast(values: Sequence[float], horizon: int = 1, *, season: int | None = None,
             level: float = 0.8) -> Forecast:
    """Return a guarded forecast, or an explicitly unsupported baseline.

    season=None detects a period, 0 disables detection, 2..120 supplies a known
    period. Invalid inputs raise ValueError. Too little history is a result with
    enough_data=False. Missing interval bounds are None, never a zero-width claim.
    """
    y = _values(values)
    if type(horizon) is not int or not 1 <= horizon <= MAX_HORIZON:
        raise ValueError(f"horizon must be an integer in 1..{MAX_HORIZON}")
    if type(level) not in (int,float) or not 0 < level < 1:
        raise ValueError("level must lie strictly between 0 and 1")
    if season is not None and (type(season) is not int or season != 0 and not 2 <= season <= MAX_SEASON):
        raise ValueError(f"season must be None, 0, or an integer in 2..{MAX_SEASON}")

    def insufficient(reason: str) -> Forecast:
        points = (y[-1],)*horizon if y else ()
        unknown = (None,)*len(points)
        return Forecast("naive",points,unknown,unknown,float(level),season or None,None,None,
                        False,reason,"naive",None,None,0,0,"Insufficient backtest history")

    if len(y) < 8:
        return insufficient("Need at least 8 observations; returning only the last-value baseline")
    if season and len(y) < 2*season:
        return insufficient("Need at least two complete seasons")
    initial = max(4,len(y)//3,2*(season or 0))
    prefix = y[:initial]
    period = detect_season(prefix) if season is None else season or None
    split = max(2*len(y)//3,initial+4+horizon-1)
    origins = _origins(initial,split-horizon+1)
    calibration = _origins(split,len(y)-horizon+1)
    if split > len(y) or len(origins) < 4:
        return insufficient("Not enough rolling origins for this horizon and season; returning the last-value baseline")
    baselines = [_Spec("naive")]
    if period:
        baselines.append(_Spec("seasonal_naive",period))
    models = [_tune(prefix,"ses",0),_tune(prefix,"holt",0)]
    if period:
        models.append(_tune(prefix,"holt_winters",period))
    lag = period or 1
    scales = [fmean(abs(y[i]-y[i-lag]) for i in range(lag,t)) for t in origins]
    scored = {spec: _scores(_errors(y,spec,origins,horizon),scales) for spec in baselines+models}
    if any(mase is None for _,mase in scored.values()):
        scored = {spec: (mae,None) for spec,(mae,_) in scored.items()}
    def score(spec: _Spec) -> float:
        mae, mase = scored[spec]
        return mase if mase is not None else mae
    baseline = min(baselines,key=score)
    candidate = min(models,key=score)
    chosen = candidate if score(candidate) < score(baseline)*(1-1e-9) else baseline
    reason = ("Beat the best available naive baseline on rolling-origin backtests"
              if chosen != baseline else "No smoothing method beat the best naive baseline; kept the baseline")
    if scored[chosen][1] is None:
        reason += "; MASE is undefined for a zero or numerically unstable training scale, so compared MAE"
    points = _predict(y,horizon,chosen)
    # Model/parameter choices stop before this block. These errors cannot select a model.
    minimum = max(10, math.ceil(2/(1-level)-1e-9))
    lower = upper = (None,)*horizon
    interval_reason = f"Need {minimum} calibration origins for a {level:.0%} interval; have {len(calibration)}"
    if len(calibration) >= minimum:
        errors = _errors(y,chosen,calibration,horizon)
        tail = (1-level)/2
        lower = tuple(point+_quantile(row,tail) for point,row in zip(points,errors))
        upper = tuple(point+_quantile(row,1-tail) for point,row in zip(points,errors))
        interval_reason = "Empirical horizon-specific calibration errors; coverage is not guaranteed under distribution changes"
    mae,mase = scored[chosen]
    baseline_mae,baseline_mase = scored[baseline]
    return Forecast(chosen.method,points,lower,upper,float(level),period,mase,baseline_mase,True,
                    reason,baseline.method,mae,baseline_mae,len(origins),len(calibration),interval_reason)

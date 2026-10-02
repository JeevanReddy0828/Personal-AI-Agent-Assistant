"""Chronological OLS diagnostics and robust univariate anomalies. See docs/analytics.md."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
from numbers import Real
from statistics import fmean, median

MAX_ROWS = 4096
MAX_FEATURES = 24
MAX_ABS = 1e100
MAD_NORMAL = 0.6744897501960817


def _sequence(values, name):
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be an ordered sequence")
    return values


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or abs(value) > MAX_ABS:
        raise ValueError(f"{name} must contain finite numbers with magnitude <= 1e100")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must contain finite numbers with magnitude <= 1e100")
    return value


def _values(values, name):
    _sequence(values, name)
    if len(values) > MAX_ROWS:
        raise ValueError(f"{name} supports at most {MAX_ROWS} rows")
    return tuple(_number(v, name) for v in values)


@dataclass(frozen=True)
class Anomalies:
    median: float | None
    mad: float | None
    threshold: float
    scores: tuple[float | None, ...]
    indices: tuple[int, ...]
    unscored_indices: tuple[int, ...]
    warnings: tuple[str, ...]


def anomalies(values: Sequence[float], *, threshold: float = 3.5) -> Anomalies:
    """Signed modified z scores. Zero MAD deviations are unscored, never infinite."""
    values = _values(values, "values")
    threshold = _number(threshold, "threshold")
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    warnings = []
    if len(values) < 10:
        warnings.append("Fewer than 10 observations; treat anomaly labels as exploratory")
    if not values:
        return Anomalies(None, None, threshold, (), (), (), tuple(warnings))
    center = median(values)
    mad = median(abs(v-center) for v in values)
    if mad == 0:
        scores = tuple(0.0 if v == center else None for v in values)
        warnings.append("MAD is zero; deviations from the median have no calibrated score")
    else:
        scores = tuple(MAD_NORMAL*((v-center)/mad) for v in values)
        if any(not math.isfinite(s) for s in scores):
            warnings.append("Some scores exceed numeric range and are unscored")
        scores = tuple(s if math.isfinite(s) else None for s in scores)
    return Anomalies(center, mad, threshold, scores,
                     tuple(i for i,s in enumerate(scores) if s is not None and abs(s) > threshold),
                     tuple(i for i,s in enumerate(scores) if s is None), tuple(warnings))


@dataclass(frozen=True)
class Drivers:
    feature_names: tuple[str, ...]
    standardized_coefficients: tuple[float | None, ...]
    vif: tuple[float | None, ...]
    train_rows: int
    test_rows: int
    predictions: tuple[float, ...]
    out_of_sample_r2: float | None
    mae: float | None
    baseline_mae: float | None
    enough_data: bool
    reason: str
    warnings: tuple[str, ...]


def _standardize(values):
    # Normalize before squaring: units can be tiny or huge without under/overflow.
    unit = max(abs(v) for v in values) or 1.0
    scaled = [v/unit for v in values]
    center = fmean(scaled)
    sd = math.sqrt(fmean((v-center)**2 for v in scaled))
    return unit, center, sd


def _dot(a, b):
    return math.fsum(x*y for x,y in zip(a,b))


def _solve(r, rhs):
    beta = [0.0]*len(rhs)
    for j in reversed(range(len(rhs))):
        beta[j] = (rhs[j]-math.fsum(r[j][k]*beta[k] for k in range(j+1,len(rhs))))/r[j][j]
    return beta


def _fit(columns, response):
    """Reorthogonalized modified Gram-Schmidt QR; never form normal equations."""
    p, n = len(columns), len(response)
    q, r = [], [[0.0]*p for _ in range(p)]
    for j,column in enumerate(columns):
        residual = list(column)
        for _ in range(2):
            for k,axis in enumerate(q):
                projection = _dot(axis,residual)
                r[k][j] += projection
                residual = [v-projection*u for v,u in zip(residual,axis)]
        norm = math.sqrt(_dot(residual,residual))
        if norm <= 1e-10*math.sqrt(n):
            raise ValueError("Training features are rank deficient or numerically inseparable")
        r[j][j] = norm
        q.append([v/norm for v in residual])
    beta = _solve(r,[_dot(axis,response) for axis in q])
    # diag((X'X)^-1) = squared row norms of R^-1. X columns have squared norm n.
    inverse_columns = [_solve(r,[float(i==j) for i in range(p)]) for j in range(p)]
    vif = [n*math.fsum(col[j]**2 for col in inverse_columns) for j in range(p)]
    return tuple(beta), tuple(vif)


def drivers(features: Sequence[Sequence[float]], target: Sequence[float], *,
            feature_names: Sequence[str] | None = None, holdout: float = 0.2) -> Drivers:
    """OLS associations fitted on a prefix, evaluated on an untouched ordered tail.

    Coefficients standardize both X and Y using the TRAINING population deviations.
    A coefficient is an association conditional on the other supplied features, not causality.
    """
    target = _values(target,"target")
    features = _sequence(features,"features")
    if len(features) != len(target):
        raise ValueError("features and target must have the same number of rows")
    if not features:
        raise ValueError("features must contain at least one row")
    p = len(_sequence(features[0],"feature row"))
    if not 1 <= p <= MAX_FEATURES:
        raise ValueError(f"supply 1 to {MAX_FEATURES} features")
    rows = []
    for row in features:
        if len(_sequence(row,"feature row")) != p:
            raise ValueError("feature rows must have equal lengths")
        rows.append(tuple(_number(v,"features") for v in row))
    if feature_names is None:
        names = tuple(f"x{i+1}" for i in range(p))
    else:
        names = tuple(_sequence(feature_names,"feature_names"))
        if len(names) != p or any(not isinstance(n,str) or not n.strip() for n in names):
            raise ValueError("feature_names must name every feature")
        names = tuple(n.strip() for n in names)
        if len(set(names)) != p:
            raise ValueError("feature_names must be unique")
    holdout = _number(holdout,"holdout")
    if not 0 < holdout < 1:
        raise ValueError("holdout must be between zero and one")
    n = len(rows)
    test = math.ceil(n*holdout)
    train = n-test
    warnings = ["Coefficients describe associations, not causal effects"]
    if train < 10*p:
        warnings.append("Fewer than 10 training rows per feature; coefficients may be unstable")
    if test < 10:
        warnings.append("Fewer than 10 held-out rows; predictive scores may be unstable")
    def refused(reason):
        return Drivers(names,(None,)*p,(None,)*p,train,test,(),None,None,None,False,reason,tuple(warnings))
    if train < max(4,p+2) or test < 2:
        return refused("Need at least max(4, features+2) training rows and two held-out rows")
    transforms = [_standardize([row[j] for row in rows[:train]]) for j in range(p)]
    if any(sd <= 1e-12 for _,_,sd in transforms):
        return refused("A training feature is numerically constant; remove it before interpreting coefficients")
    yu, ym, ys = _standardize(target[:train])
    if ys <= 1e-12:
        return refused("Training target is numerically constant; standardized associations are undefined")
    columns = [[(row[j]/unit-center)/sd for row in rows[:train]]
               for j,(unit,center,sd) in enumerate(transforms)]
    response = [(v/yu-ym)/ys for v in target[:train]]
    try:
        beta,vif = _fit(columns,response)
    except ValueError as exc:
        return refused(str(exc))
    if any(v >= 10 for v in vif):
        warnings.append("Strong collinearity (VIF >= 10); individual coefficients are unreliable")
    try:
        predictions = tuple(yu*(ym+ys*_dot(beta,[(row[j]/unit-center)/sd
            for j,(unit,center,sd) in enumerate(transforms)])) for row in rows[train:])
        if not all(math.isfinite(v) for v in predictions):
            return refused("Held-out extrapolation exceeds numeric range")
        actual = target[train:]
        baseline = fmean(target[:train])
        unit = max(*(abs(v) for v in actual+predictions),abs(baseline)) or 1.0
        errors = [a/unit-b/unit for a,b in zip(actual,predictions)]
        mae = fmean(abs(e) for e in errors)*unit
        baseline_mae = fmean(abs(a/unit-baseline/unit) for a in actual)*unit
        center = baseline/unit
        sst = math.fsum((v/unit-center)**2 for v in actual)
        r2 = 1-math.fsum(e*e for e in errors)/sst if sst else None
    except (OverflowError, ValueError):
        return refused("Held-out extrapolation exceeds numeric range")
    if r2 is not None and not math.isfinite(r2):
        r2 = None
    if r2 is None:
        warnings.append("Held-out R2 is undefined because the training-mean baseline has zero or numerically unresolved squared error")
    return Drivers(names,beta,vif,train,test,predictions,r2,mae,baseline_mae,True,
                   "Fit and standardization use only the prefix; scores use only the held-out tail",
                   tuple(warnings))

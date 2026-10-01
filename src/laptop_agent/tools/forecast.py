"""`forecast`: a column of the user's own CSV, projected forward. Computed, never generated.

This half turns a file into what the forecasting core accepts - finite, ordered, evenly
spaced numbers - because dates, spacing, gaps and odd cells belong to the tool, not to the
core (Codex's ANALYTICS-01 contract). Whatever cannot be settled without a guess is refused
with a reason the user can act on: a missing month is not quietly treated as zero, two rows
on one date are not quietly added up, and "1,5" is not read as fifteen.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

MAX_ROWS = 10000   # what `analyze spreadsheet` reads too

# The gap in days, inclusive, between neighbours in each calendar period a date column can
# step by. Read from the SMALLEST gap: missing months widen the gaps around them, and the
# median gap of Jan, Feb, Jun is 75.5 days, which is no period at all.
_PERIODS = (("day", 1, 1), ("week", 7, 7), ("month", 28, 31), ("quarter", 89, 92), ("year", 365, 366))
_MONTHS_PER = {"month": 1, "quarter": 3, "year": 12}
_DATE_FORMATS = ("%Y-%m", "%Y/%m", "%m/%d/%Y", "%Y/%m/%d", "%b %Y", "%B %Y", "%Y")
_NO_SYMBOLS = str.maketrans("", "", "$€£¥ ")
# A comma only as a thousands separator: "1,234.5" is a number, "1,5" is a question.
_THOUSANDS = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?")


class SeriesError(ValueError):
    """Why a column cannot be forecast as it stands, in words the user can act on."""


@dataclass(frozen=True)
class Series:
    labels: list[str]       # one per observation: "2025-03", "2025-Q1", "2025-03-14", "3"
    values: list[float]
    period: str             # "day", "week", "month", "quarter", "year", or "row"
    last: date | None       # the last observation's date; None without a date column

    def next_labels(self, count: int) -> list[str]:
        """Labels for the `count` periods after the last observation."""
        if self.last is None:
            return [str(len(self.values) + step) for step in range(1, count + 1)]
        return [_label(_step(self.last, self.period, step), self.period) for step in range(1, count + 1)]


def _number(cell: str) -> float | None:
    text = cell.strip().translate(_NO_SYMBOLS).removesuffix("%")
    if "," in text:
        if not _THOUSANDS.fullmatch(text):
            return None
        text = text.replace(",", "")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _date(cell: str) -> date | None:
    text = cell.strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _index(day: date, period: str, first: date) -> int | None:
    """Which period after `first` holds `day`; None when it falls between two."""
    if period in _MONTHS_PER:
        return ((day.year - first.year) * 12 + day.month - first.month) // _MONTHS_PER[period]
    days, step = (day - first).days, 7 if period == "week" else 1
    return days // step if days % step == 0 else None


def _step(day: date, period: str, count: int) -> date:
    if period in ("day", "week"):
        return day + timedelta(days=count * (7 if period == "week" else 1))
    months = day.month - 1 + count * _MONTHS_PER[period]
    return day.replace(year=day.year + months // 12, month=months % 12 + 1, day=min(day.day, 28))


def _label(day: date, period: str) -> str:
    if period == "month":
        return f"{day.year}-{day.month:02d}"
    if period == "quarter":
        return f"{day.year}-Q{(day.month - 1) // 3 + 1}"
    if period == "year":
        return str(day.year)
    return day.isoformat()


def _rows(path: Path) -> tuple[list[str], list[list[str]]]:
    if path.suffix.lower() not in {".csv", ".tsv"}:
        raise SeriesError(f"I can forecast from a .csv or .tsv file, not {path.suffix or 'that file'}. "
                          "Save the sheet as CSV first.")
    try:
        # utf-8-sig: a file saved by Excel starts with a byte-order mark, which would
        # otherwise become part of the first column's name and stop it from matching.
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
            rows = [row for _, row in zip(range(MAX_ROWS + 1), reader)]
    except FileNotFoundError:
        raise SeriesError(f"There is no file at {path}.") from None
    if len(rows) < 2:
        raise SeriesError(f"{path.name} has no rows under its header.")
    return [name.strip() for name in rows[0]], rows[1:]


def _column(header: list[str], wanted: str, body: list[list[str]], numeric: bool) -> int:
    lowered = [name.lower() for name in header]
    if wanted.strip().lower() in lowered:
        return lowered.index(wanted.strip().lower())
    names = [name for index, name in enumerate(header) if not numeric or all(
        _number(row[index]) is not None for row in body[:20] if index < len(row) and row[index].strip())]
    listed = f" Its {'numeric ' if numeric else ''}columns are: {', '.join(names)}." if names else ""
    raise SeriesError(f"There is no column called '{wanted}'.{listed}")


def load_series(path: str | Path, column: str, date_column: str | None = None) -> Series:
    """The `column` of a CSV/TSV as a forecastable series: ordered by `date_column` when one
    is named, otherwise taken in file order as evenly spaced rows. Raises SeriesError."""
    path = Path(path).expanduser()
    header, body = _rows(path)
    value_at = _column(header, column, body, numeric=True)
    values: list[float] = []
    for line, row in enumerate(body, start=2):
        cell = row[value_at] if value_at < len(row) else ""
        if not cell.strip():
            raise SeriesError(f"Row {line} has no {header[value_at]}. Fill it in or remove the row; "
                              "I will not guess a missing value.")
        number = _number(cell)
        if number is None:
            raise SeriesError(f"Row {line}: '{cell.strip()}' in {header[value_at]} is not a number.")
        values.append(number)
    if date_column is None:
        return Series([str(index) for index in range(1, len(values) + 1)], values, "row", None)

    date_at = _column(header, date_column, body, numeric=False)
    dated: list[tuple[date, float]] = []
    for line, (row, value) in enumerate(zip(body, values), start=2):
        cell = row[date_at] if date_at < len(row) else ""
        day = _date(cell)
        if day is None:
            raise SeriesError(f"Row {line}: '{cell.strip()}' in {header[date_at]} is not a date I can read "
                              "(try 2025-03-14 or 2025-03).")
        dated.append((day, value))
    if len(dated) < 2:
        raise SeriesError("One dated row is not a series.")
    dated.sort(key=lambda pair: pair[0])
    days = [day for day, _value in dated]
    gaps = [(later - earlier).days for earlier, later in zip(days, days[1:]) if later > earlier]
    if not gaps:
        raise SeriesError(f"Every row has the same {header[date_at]}, so there is no series over time.")
    closest = min(gaps)
    period = next((name for name, low, high in _PERIODS if low <= closest <= high), None)
    if period is None:
        raise SeriesError(f"The closest dates in {header[date_at]} are {closest} days apart, which is not a "
                          "day, week, month, quarter or year. Total them per period first.")

    first = days[0]
    indices = [_index(day, period, first) for day in days]
    for day, index in zip(days, indices):
        if index is None:
            raise SeriesError(f"{day.isoformat()} falls between two {period}s, so the rows are not evenly spaced.")
    for day, index, following in zip(days, indices, indices[1:]):
        if following == index:
            raise SeriesError(f"Two rows fall in the {period} of {_label(day, period)}. Total them or keep one "
                              "row per period; I will not decide which.")
    missing = [_label(_step(first, period, index), period)
               for before, after in zip(indices, indices[1:]) for index in range(before + 1, after)]
    if missing:
        shown = ", ".join(missing[:3]) + (f" and {len(missing) - 3} more" if len(missing) > 3 else "")
        raise SeriesError(f"{len(missing)} {period}{'s are' if len(missing) > 1 else ' is'} missing: {shown}. "
                          "Add them (as 0 if that is what happened) or forecast from after the gap.")
    return Series([_label(day, period) for day in days], [value for _day, value in dated], period, days[-1])

"""Questions about a CSV of the user's own, answered by computing them, never by a model:
"total revenue by region in sales.csv", "average price per category in products.csv", "how
many orders by month in orders.csv", "top 5 products by revenue in sales.csv", "which region
has the highest revenue in sales.csv".

The table is read the way `forecast` and the diagnostics read it - one reader, one number
parser - and a cell that is not plainly a number is refused with its row rather than skipped,
because a total that quietly leaves out "N/A" is a different total. A blank is left out and
the reply says how many. The lead sentence is the answer, so it is what voice speaks first.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.files import parse_number
from laptop_agent.tools.forecast import MAX_ROWS, SeriesError, _TRAILING, _date, _rows, _shown

USAGE = ("total|average|median|count|max|min <column> by <column> in <file.csv>, "
         "top N <column> by <column> in <file.csv>, or which <column> has the most <column> in <file.csv>")
SHOWN = 12          # groups listed and drawn; the rest are counted
SHOWN_PERIODS = 24  # a time breakdown shows the latest periods, in order
KEPT = 200          # groups returned in the data

_STAT_WORD = (r"(?P<stat>total|sum|average|avg|mean|median|count|number|max(?:imum)?|highest|largest|biggest"
              r"|min(?:imum)?|lowest|smallest)")
_STATS = {"total": "sum", "sum": "sum", "average": "mean", "avg": "mean", "mean": "mean", "median": "median",
          "count": "count", "number": "count", "max": "max", "maximum": "max", "highest": "max", "largest": "max",
          "biggest": "max", "min": "min", "minimum": "min", "lowest": "min", "smallest": "min"}
_SEP = r"(?:by|per|for\s+each|for\s+every|in\s+each|across|grouped\s+by|broken\s+down\s+by|split\s+by)"
_LEAD = re.compile(
    r"\s*(?:(?:can|could|would|will)\s+(?:you|u)\s+(?:please\s+)?|please\s+)?"
    r"(?:(?:tell|show|give)\s+me|what(?:'s|\s+is|\s+are|\s+was|\s+were)|calculate|compute|find|get|list)?\s*"
    r"(?:the\s+)?", re.IGNORECASE)
_TOP = re.compile(rf"(?P<dir>top|bottom)\s+(?P<n>\d+)\s+(?P<group>.+?)\s+(?:by|for)\s+"
                  rf"(?:{_STAT_WORD}\s+(?:of\s+)?)?(?P<column>.+)", re.IGNORECASE | re.DOTALL)
_WHICH = re.compile(rf"which\s+(?P<group>.+?)\s+(?:has|had|have|made|sold|got)\s+the\s+"
                    rf"(?P<dir>most|highest|biggest|largest|lowest|least|smallest|fewest)\s+"
                    rf"(?:{_STAT_WORD.replace('?P<stat>', '?P<stat2>')}\s+(?:of\s+)?)?(?P<column>.+)",
                    re.IGNORECASE | re.DOTALL)
_HOW_MANY = re.compile(rf"how\s+many\s+(?P<column>.+?)\s+(?:(?:are|were)\s+there\s+)?{_SEP}\s+(?P<group>.+?)"
                       rf"(?:\s+(?:are|were)\s+there)?", re.IGNORECASE | re.DOTALL)
_STATED = re.compile(rf"{_STAT_WORD}\s+(?:of\s+)?(?P<column>.+?)\s+{_SEP}\s+(?P<group>.+)", re.IGNORECASE | re.DOTALL)
_BARE = re.compile(rf"(?P<column>.+?)\s+{_SEP}\s+(?P<group>.+)", re.IGNORECASE | re.DOTALL)
_PERIOD = re.compile(r"(?:the\s+|each\s+|every\s+)?(?P<period>day|week|month|quarter|year)s?"
                     r"(?:\s+(?:of|in|from)\s+(?P<column>.+))?", re.IGNORECASE)
_PERIOD_ADVERB = {"daily": "day", "weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year",
                  "annually": "year"}
_FILE_SPLIT = re.compile(r"\s+(?:in|from)\s+", re.IGNORECASE)
_QUOTED_FILE = re.compile(r"(?P<head>.+?)\s+(?:in|from)\s+\"(?P<path>[^\"]+\.(?:csv|tsv))\"", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class Request:
    stat: str | None        # None: inferred from the column - a total of numbers, else a count
    column: str
    group: str
    path: str
    limit: int | None = None
    ascending: bool = False
    must_match: bool = False    # a bare "<column> by <group>": only ours when the column exists
    single: bool = False        # "which region has the most": one answer, the ranking beside it


def _split_file(text: str) -> tuple[str, str] | None:
    """The question and the file it names. The file is the last "in/from <x>.csv" that exists,
    so a column called "Revenue in USD" keeps its "in"; failing that, the last one."""
    if (quoted := _QUOTED_FILE.fullmatch(text)) is not None:
        return quoted["head"], quoted["path"]
    if not re.search(r"\.(?:csv|tsv)$", text, re.IGNORECASE):
        return None
    splits = [(text[:m.start()], text[m.end():]) for m in _FILE_SPLIT.finditer(text)]
    splits = [(head, path) for head, path in reversed(splits) if head.strip() and path.strip()]
    if not splits:
        return None
    return next(((head, path) for head, path in splits if Path(path).expanduser().is_file()), splits[0])


def breakdown_request(command: str) -> Request | None:
    """The breakdown `command` asks for, or None when it is not one."""
    text = _TRAILING.sub("", command.strip())
    split = _split_file(text)
    if split is None:
        return None
    head, path = split
    head = head[_LEAD.match(head).end():].strip()
    if (top := _TOP.fullmatch(head)) is not None:
        stat = _STATS[top["stat"].lower()] if top["stat"] else None
        return Request(stat, top["column"].strip(), top["group"].strip(), path, int(top["n"]),
                       ascending=top["dir"].lower() == "bottom")
    if (which := _WHICH.fullmatch(head)) is not None:
        stat = _STATS[which["stat2"].lower()] if which["stat2"] else None
        return Request(stat, which["column"].strip(), which["group"].strip(), path,
                       ascending=which["dir"].lower() in {"lowest", "least", "smallest", "fewest"}, single=True)
    if (many := _HOW_MANY.fullmatch(head)) is not None:
        return Request("count", many["column"].strip(), many["group"].strip(), path)
    if (stated := _STATED.fullmatch(head)) is not None:
        return Request(_STATS[stated["stat"].lower()], stated["column"].strip(), stated["group"].strip(), path)
    if (bare := _BARE.fullmatch(head)) is not None:
        return Request(None, bare["column"].strip(), bare["group"].strip(), path, must_match=True)
    return None


class _NotOurs(Exception):
    """A bare "<words> by <words> in x.csv" whose words name no column: someone else's sentence."""


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-]+", " ", name.strip().lower()).strip()


def _find(header: list[str], wanted: str) -> int | None:
    """The column `wanted` names: its exact name, the same ignoring case, underscores and a
    plural, or the one column whose name contains it as whole words."""
    want = re.sub(r"^(?:the|each|every)\s+", "", _norm(wanted))
    names = [_norm(name) for name in header]
    forms = {want, want[:-1] if want.endswith("s") else want, want[:-2] if want.endswith("es") else want}
    for index, name in enumerate(names):
        if name == want:
            return index
    for index, name in enumerate(names):
        if name in forms or name + "s" in forms:
            return index
    holding = [index for index, name in enumerate(names)
               if any(form and re.search(rf"\b{re.escape(form)}\b", name) for form in forms)]
    return holding[0] if len(holding) == 1 else None


def _missing(header: list[str], wanted: str) -> SeriesError:
    return SeriesError(f"There is no column called '{wanted}'. Its columns are: {', '.join(header)}.")


def _is_numeric(body: list[list[str]], at: int) -> bool:
    """Mostly numbers: one "N/A" must be refused by its row, not turn Revenue into a text column."""
    cells = [row[at].strip() for row in body if at < len(row) and row[at].strip()]
    return 2 * sum(parse_number(cell) is not None for cell in cells) > len(cells)


def _date_columns(header: list[str], body: list[list[str]]) -> list[int]:
    """Columns whose cells are all dates and not all plain numbers ("2025" alone reads as both)."""
    found = []
    for at in range(len(header)):
        cells = [row[at].strip() for row in body[:50] if at < len(row) and row[at].strip()]
        if cells and all(_date(cell) is not None for cell in cells) and not _is_numeric(body[:50], at):
            found.append(at)
    return found


def _period_label(day: date, period: str) -> str:
    if period == "month":
        return f"{day.year}-{day.month:02d}"
    if period == "quarter":
        return f"{day.year}-Q{(day.month - 1) // 3 + 1}"
    if period == "year":
        return str(day.year)
    if period == "week":
        year, week, _ = day.isocalendar()
        return f"{year}-W{week:02d}"
    return day.isoformat()


def _grouping(header: list[str], body: list[list[str]], group: str) -> tuple[int, str | None]:
    """The column to group by and, for "by month" and the like, the period to bucket its dates in."""
    if (at := _find(header, group)) is not None:
        return at, None
    word = _norm(group)
    period_match = _PERIOD.fullmatch(word)
    period = _PERIOD_ADVERB.get(word) or (period_match["period"] if period_match else None)
    if period is None:
        raise _missing(header, group)
    named = period_match["column"] if period_match and period_match["column"] else None
    if named:
        if (at := _find(header, named)) is None:
            raise _missing(header, named)
        return at, period
    dated = _date_columns(header, body)
    if len(dated) == 1:
        return dated[0], period
    if not dated:
        raise SeriesError(f"No column holds dates, so I cannot group by {period}. Its columns are: {', '.join(header)}.")
    names = " or ".join(header[at] for at in dated)
    raise SeriesError(f"Group by the {period} of which date: {names}? Say 'by {period} of {header[dated[0]]}'.")


def _percent(part: float, whole: float) -> str:
    share = 100 * part / whole
    return "<1%" if 0 < share < 1 else f"{share:.0f}%"


def run_breakdown(request: Request) -> ToolResult:
    path = Path(request.path).expanduser()
    if request.limit is not None and request.limit < 1:
        return ToolResult.failure("Ask for at least one: top 5, not top 0.")
    try:
        header, body = _rows(path)
        value_at = _find(header, request.column)
        if value_at is None and request.must_match:
            raise _NotOurs
        group_at, period = _grouping(header, body, request.group)
    except SeriesError as exc:
        return ToolResult.failure(str(exc))

    numeric = value_at is not None and _is_numeric(body, value_at)
    stat = request.stat or ("sum" if numeric else "count")
    if stat != "count":
        if value_at is None:
            return ToolResult.failure(str(_missing(header, request.column)))
        if not numeric:
            return ToolResult.failure(f"{header[value_at]} is not a column of numbers, so it has no {stat}. "
                                      f"Try: count of {header[value_at]} by {header[group_at]} in {path.name}.")

    buckets: dict[str, list] = {}
    blank = 0
    for line, row in enumerate(body, start=2):
        key = row[group_at].strip() if group_at < len(row) else ""
        if period is not None:
            day = _date(key) if key else None
            if day is None:
                return ToolResult.failure(f"Row {line}: '{key}' in {header[group_at]} is not a date I can read "
                                          "(try 2025-03-14 or 2025-03).")
            key = _period_label(day, period)
        key = key or "(blank)"
        if value_at is None:            # "how many orders": the rows themselves
            buckets.setdefault(key, []).append(1)
            continue
        cell = row[value_at].strip() if value_at < len(row) else ""
        if not cell:
            blank += 1
            continue
        if stat == "count":             # a named column: its filled cells, or its distinct names
            buckets.setdefault(key, []).append(cell)
            continue
        number = parse_number(cell)
        if number is None:
            return ToolResult.failure(f"Row {line}: '{cell}' in {header[value_at]} is not a number. "
                                      "Fix or remove that row; I will not leave it out quietly.")
        buckets.setdefault(key, []).append(number)
    if not buckets:
        return ToolResult.failure(f"{path.name} has no {header[value_at] if value_at is not None else 'rows'} to add up.")

    # "how many customers by region": a column of names counts each name once; a column of
    # numbers ("count of sales") counts its filled cells, since equal amounts are separate sales.
    distinct = stat == "count" and value_at is not None and not numeric
    measure = {"sum": sum, "mean": statistics.fmean, "median": statistics.median, "max": max, "min": min,
               "count": (lambda items: len(set(items))) if distinct else len}[stat]
    totals = {key: float(measure(items)) for key, items in buckets.items()}
    ascending = request.ascending or (stat == "min" and request.limit is None)
    ranked = sorted(totals.items(), key=lambda pair: ((pair[1] if ascending else -pair[1]), pair[0]))
    if period is not None:
        ordered = sorted(totals.items())
        shown = ordered[-SHOWN_PERIODS:] if request.limit is None else ranked[:request.limit]
    else:
        shown = ranked[:request.limit or SHOWN]

    column = header[value_at] if value_at is not None else request.column
    by = f"{period} of {header[group_at]}" if period else header[group_at]
    noun = {"sum": f"Total {column}", "mean": f"Average {column}", "median": f"Median {column}",
            "max": f"Highest {column}", "min": f"Lowest {column}",
            "count": f"Distinct {column}" if distinct else f"Number of {column}"}[stat]
    title = f"{noun} by {by}"
    lead = _insight(stat, distinct, column, totals, ranked, ordered if period else None, buckets, request)

    share = stat in {"sum", "count"} and not distinct and all(value >= 0 for value in totals.values())
    whole = sum(totals.values())
    lines = [f"**{title}** · {path.name}, {len(body):,} rows", "", lead, ""]
    for key, value in shown:
        lines.append(f"- {key}: {_shown(value)}" + (f" ({_percent(value, whole)})" if share and whole else ""))
    hidden = len(totals) - len(shown)
    if hidden > 0:
        lines.append(f"- …and {hidden} more" + (" (earlier)" if period and request.limit is None else ""))
    notes = []
    if blank:
        notes.append(f"{blank} row{'s' if blank != 1 else ''} with no {column} {'were' if blank != 1 else 'was'} left out.")
    if len(body) >= MAX_ROWS:
        notes.append(f"Only the first {MAX_ROWS:,} rows were read.")
    if notes:
        lines += ["", "_" + " ".join(notes) + "_"]

    return ToolResult.success(
        "\n".join(lines),
        path=str(path),
        stat=stat,
        column=column,
        group_by=by,
        rows=len(body),
        groups=[{"label": key, "value": value} for key, value in (ordered if period else ranked)[:KEPT]],
        group_count=len(totals),
        left_out=blank,
        chart={"kind": "bar", "title": title, "labels": [key for key, _ in shown],
               "values": [value for _, value in shown], "time": period is not None},
    )


def _insight(stat: str, distinct: bool, column: str, totals: dict[str, float], ranked: list[tuple[str, float]],
             ordered: list[tuple[str, float]] | None, buckets: dict[str, list], request: Request) -> str:
    """The answer in one or two sentences, computed from the totals."""
    first, value = ranked[0]
    if ordered is not None and len(ordered) > 1 and request.limit is None and not request.single:
        peak = max(ordered, key=lambda pair: pair[1])
        (before, earlier), (latest, now) = ordered[-2], ordered[-1]
        change = ("level with" if now == earlier else
                  f"{'up' if now > earlier else 'down'} {_percent(abs(now - earlier), abs(earlier))} on" if earlier
                  else f"against {_shown(earlier)} in")
        return f"{peak[0]} was the highest at {_shown(peak[1])}. The latest, {latest}, is {_shown(now)}, {change} {before}."
    if len(totals) == 1:
        return f"Every row falls in {first}: {_shown(value)}."
    last, low = ranked[-1]
    if request.single or request.limit == 1:
        word = "lowest" if request.ascending else "highest"
        tied = [key for key, item in ranked if item == value]
        if len(tied) > 1:   # "Cora has the lowest" was wrong when Dyna had as few
            names = ", ".join(tied[:-1]) + " and " + tied[-1] if len(tied) <= 4 else f"{len(tied)} of them"
            return f"{names} share the {word}, {_shown(value)}."
        return f"{first} has the {word}, {_shown(value)}."
    if stat == "sum" or (stat == "count" and not distinct):
        whole = sum(totals.values())
        if whole > 0 and all(item >= 0 for item in totals.values()):
            what = "of the rows" if stat == "count" else "of the total"
            text = f"{first} leads with {_shown(value)}, {_percent(value, whole)} {what} ({_shown(whole)})."
            if len(totals) >= 4:
                text += f" The top 3 make up {_percent(sum(item for _, item in ranked[:3]), whole)}."
            return text + f" {last} is lowest at {_shown(low)}."
        return f"{first} is highest at {_shown(value)} and {last} lowest at {_shown(low)}."
    if stat in {"mean", "median"}:
        every = [number for items in buckets.values() for number in items]
        overall = statistics.fmean(every) if stat == "mean" else statistics.median(every)
        word = "average" if stat == "mean" else "median"
        high, low_pair = (ranked[-1], ranked[0]) if request.ascending else (ranked[0], ranked[-1])
        return (f"{high[0]} has the highest {word} {column}, {_shown(high[1])}, against {_shown(overall)} overall; "
                f"{low_pair[0]} the lowest, {_shown(low_pair[1])}.")
    if distinct:
        return f"{first} has the most distinct {column}, {_shown(value)}; {last} the fewest, {_shown(low)}."
    word = "lowest" if stat == "min" else "highest"
    return f"The {word} {column} is {_shown(value)}, in {first}."


def breakdown_command(command: str) -> ToolResult | None:
    """The breakdown `command` asks for, run; None when it is not one, which leaves it to the
    rest of the dispatch and the router."""
    request = breakdown_request(command)
    if request is None:
        return None
    try:
        return run_breakdown(request)
    except _NotOurs:
        return None

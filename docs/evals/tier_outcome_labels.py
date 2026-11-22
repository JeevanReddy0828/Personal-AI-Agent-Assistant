"""Outcome labels for the tier choice: the smallest model whose answer was good enough.

For each request the fast model answers three times and the smart model once, with the app's own
chat prompt (reasoning off, as the app calls them), then the ultra model judges every answer, shown
in a random order, as sufficient or not. One fast answer is close to a coin flip on a hard request,
so the label is fast when most fast answers are sufficient, else smart when the smart answer is,
else ultra. Records are appended one JSON line per request, so a stopped run resumes
where it left off. Live calls start at least 12 seconds apart. Run from the repository root:

    python -I -B docs/evals/tier_outcome_labels.py --out <file.jsonl> [--limit N]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETS = ("docs/evals/tier_choice_set.json", "docs/evals/tier_choice_fresh_set.json")
PACE_SECONDS = 12.0

JUDGE_PROMPT = """You are grading answers from a personal laptop assistant.

Request from the user:
<request>
{request}
</request>

{answers}

Grade each answer on its own, not against the others. An answer is "sufficient" when a careful
expert would accept it as it stands: it is correct, it does what was asked, and it leaves out
nothing the user needed. Length and tone do not matter; a short correct answer is sufficient. An
answer that refuses, stalls, makes a factual or arithmetic error, or misses a part of the request
is "insufficient". If the request asks the assistant to act on the laptop, judge only whether the
reply responds sensibly.

Reply with one JSON object and nothing else:
{{{keys}, "reason": "<25 words or fewer>"}}"""

FAST_SAMPLES = 3
RETRY_WAIT_SECONDS = 60.0
RETRIES = 3

_VERDICT = re.compile(r"\{.*\}", re.S)


def outcome_label(fast_passes: int, fast_samples: int, smart_ok: bool) -> str:
    if fast_passes * 2 > fast_samples:
        return "fast"
    return "smart" if smart_ok else "ultra"


def judge_prompt(request: str, answers: list[str]) -> str:
    blocks = "\n\n".join(f"Answer {n}:\n<answer_{n}>\n{text}\n</answer_{n}>" for n, text in enumerate(answers, 1))
    keys = ", ".join(f'"answer_{n}": "sufficient" or "insufficient"' for n in range(1, len(answers) + 1))
    return JUDGE_PROMPT.format(request=request, answers=blocks, keys=keys)


def parse_verdict(reply: str, count: int) -> tuple[list[bool], str]:
    """Read the judge's verdict on each of `count` answers; raise ValueError when any is missing or unclear."""
    match = _VERDICT.search(reply or "")
    if not match:
        raise ValueError("no JSON object in the judge's reply")
    data = json.loads(match.group(0))
    verdicts = []
    for n in range(1, count + 1):
        value = str(data.get(f"answer_{n}", "")).strip().lower()
        if value not in ("sufficient", "insufficient"):
            raise ValueError(f"answer_{n} is {value!r}")
        verdicts.append(value == "sufficient")
    return verdicts, str(data.get("reason", ""))[:300]


def load_requests() -> list[dict]:
    items: list[dict] = []
    for name in SETS:
        for item in json.loads((ROOT / name).read_text(encoding="utf-8"))["items"]:
            items.append({"id": item["id"], "text": item["text"], "opinion": item.get("tier")})
    return items


def chat_system_prompt() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from laptop_agent.planner import openai_compatible as oc

    return f"{oc._PERSONA}{oc._CAPABILITIES}{oc._NO_TOOL_CLAIMS} {oc.prompt_stamp()} {oc.reply_style()} Known facts about the user: none.\n"


class Caller:
    """One OpenAI-compatible chat call at a time, each started PACE_SECONDS after the last."""

    def __init__(self, base_url: str, key: str, sleep: Callable[[float], None] = time.sleep) -> None:
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.sleep = sleep
        self._last = 0.0

    def __call__(self, model: str, messages: list[dict], *, max_tokens: int, temperature: float, think: bool) -> tuple[str, float]:
        for attempt in range(RETRIES + 1):
            try:
                return self._once(model, messages, max_tokens=max_tokens, temperature=temperature, think=think)
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == RETRIES:
                    raise
                self.sleep(RETRY_WAIT_SECONDS)
        raise AssertionError("unreachable")

    def _once(self, model: str, messages: list[dict], *, max_tokens: int, temperature: float, think: bool) -> tuple[str, float]:
        wait = self._last + PACE_SECONDS - time.monotonic()
        if wait > 0:
            self.sleep(wait)
        self._last = time.monotonic()
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
                "chat_template_kwargs": {"enable_thinking": think}}
        request = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.loads(response.read())
        content = data["choices"][0]["message"].get("content") or ""
        return content.strip(), round(time.monotonic() - started, 2)


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"')
    return env


def label_one(call: Caller, item: dict, models: dict[str, str], system: str, rng: random.Random, judge_think: bool) -> dict:
    record = {"id": item["id"], "text": item["text"], "opinion": item["opinion"], "models": models}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": item["text"]}]
    answers: list[tuple[str, str]] = []
    for n in range(FAST_SAMPLES):
        text, seconds = call(models["fast"], messages, max_tokens=2048, temperature=0.6, think=False)
        answers.append((f"fast_{n + 1}", text))
        record[f"fast_{n + 1}_seconds"] = seconds
    text, record["smart_seconds"] = call(models["smart"], messages, max_tokens=2048, temperature=0.6, think=False)
    answers.append(("smart", text))
    rng.shuffle(answers)
    reply, record["judge_seconds"] = call(models["judge"], [{"role": "user", "content": judge_prompt(item["text"], [a for _, a in answers])}],
                                          max_tokens=16384 if judge_think else 600, temperature=1.0 if judge_think else 0.0, think=judge_think)
    verdicts, record["judge_reason"] = parse_verdict(reply, len(answers))
    record["order"] = [name for name, _ in answers]
    for (name, text), ok in zip(answers, verdicts):
        record[f"{name}_answer"], record[f"{name}_ok"] = text, ok
    record["fast_passes"] = sum(record[f"fast_{n + 1}_ok"] for n in range(FAST_SAMPLES))
    record["label"] = outcome_label(record["fast_passes"], FAST_SAMPLES, record["smart_ok"])
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--env", default=str(ROOT / ".env"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--ids", default="", help="comma-separated ids to label, in place of the whole set")
    parser.add_argument("--judge-think", action="store_true")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    env = read_env(Path(args.env))
    models = {"fast": env["OPENAI_MODEL"], "smart": env["OPENAI_SMART_MODEL"], "judge": env["OPENAI_ULTRA_MODEL"]}
    if models["fast"] == models["smart"]:
        raise SystemExit("fast and smart are the same model; an outcome label could not tell them apart")
    call = Caller(env["OPENAI_BASE_URL"], env["OPENAI_API_KEY"])
    system = chat_system_prompt()
    out = Path(args.out)
    done = set()
    if out.exists():
        done = {json.loads(line)["id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()}
    items = load_requests()
    if args.ids:
        wanted = set(args.ids.split(","))
        items = [item for item in items if item["id"] in wanted]
    items = [item for item in items if item["id"] not in done]
    if args.limit:
        items = items[: args.limit]
    rng = random.Random(args.seed)
    print(f"{len(done)} already labelled, {len(items)} to go", flush=True)
    for n, item in enumerate(items, 1):
        try:
            record = label_one(call, item, models, system, rng, args.judge_think)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
            print(f"{item['id']}: skipped ({type(exc).__name__}: {str(exc)[:120]})", flush=True)
            continue
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[{n}/{len(items)}] {item['id']} {record['label']:5} fast {record['fast_passes']}/{FAST_SAMPLES} (opinion {item['opinion']}) judge {record['judge_seconds']} s", flush=True)


if __name__ == "__main__":
    main()

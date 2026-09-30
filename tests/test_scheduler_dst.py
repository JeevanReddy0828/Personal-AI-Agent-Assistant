"""A daily job between 01:00 and 01:59 fired twice on the night the clocks go back.

`Schedule.is_due` built today's target as `now.replace(hour, minute)`, in each tick's own
offset. At 01:30 EDT on 1 November 2026 a 01:30 job fired; an hour later the clock read
01:30 again, now EST, the target moved an hour with it, and the job fired a second time.
The ticker reads the laptop's clock, so it now asks for today's target by the zone's rules.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import laptop_agent.timeparse as timeparse
from laptop_agent.scheduler import Schedule, parse_schedule
from test_daylight_saving import NEW_YORK, stopped, utc
from test_everyday_requests import Everyday


def fires(schedule: Schedule, start: datetime, end: datetime, last: datetime | None,
          step: timedelta = timedelta(minutes=1)) -> list[datetime]:
    """Tick like the ticker from `start` to `end`, running the job whenever it is due."""
    ran: list[datetime] = []
    moment = start
    while moment <= end:
        if schedule.is_due(moment, last, local=True):
            ran.append(moment)
            last = moment
        moment += step
    return ran


@unittest.skipIf(NEW_YORK is None, "no zone data here; CI installs tzdata")
class AcrossTheChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        zone = patch.object(timeparse, "LOCAL_ZONE", NEW_YORK)
        zone.start()
        self.addCleanup(zone.stop)

    def test_a_job_in_the_repeated_hour_fires_once(self) -> None:
        ran = fires(parse_schedule("daily at 1:30am"), utc(2026, 11, 1, 4, 0), utc(2026, 11, 1, 8, 0),
                    last=utc(2026, 10, 31, 5, 30))
        self.assertEqual(ran, [utc(2026, 11, 1, 5, 30)])                   # 01:30 EDT, not 01:30 EST too

    def test_a_morning_job_fires_at_seven_every_day_across_both_changes(self) -> None:
        for start, end, last, expected in (
            (utc(2026, 10, 31, 4, 0), utc(2026, 11, 3, 4, 0), utc(2026, 10, 30, 11, 0),
             [utc(2026, 10, 31, 11, 0), utc(2026, 11, 1, 12, 0), utc(2026, 11, 2, 12, 0)]),
            (utc(2027, 3, 13, 5, 0), utc(2027, 3, 16, 4, 0), utc(2027, 3, 12, 12, 0),
             [utc(2027, 3, 13, 12, 0), utc(2027, 3, 14, 11, 0), utc(2027, 3, 15, 11, 0)]),
        ):
            with self.subTest(start=start):
                ran = fires(parse_schedule("daily at 7am"), start, end, last, step=timedelta(minutes=5))
                self.assertEqual(ran, expected)
                self.assertEqual({moment.astimezone(NEW_YORK).strftime("%H:%M") for moment in ran}, {"07:00"})

    def test_a_skipped_time_fires_once_after_the_gap(self) -> None:
        ran = fires(parse_schedule("daily at 2:30am"), utc(2027, 3, 14, 5, 0), utc(2027, 3, 14, 9, 0),
                    last=utc(2027, 3, 13, 7, 30))
        self.assertEqual(ran, [utc(2027, 3, 14, 7, 30)])                   # 03:30 EDT, as reminders do

    def test_a_run_that_finished_after_midnight_does_not_skip_the_next_day(self) -> None:
        # `mark_ran` records when a job finished, so a late-evening job can end tomorrow.
        schedule = parse_schedule("daily at 11:50pm")
        finished = utc(2026, 10, 2, 4, 5)                                   # 00:05 EDT, 2 October
        self.assertFalse(schedule.is_due(utc(2026, 10, 3, 3, 40), finished, local=True))
        self.assertTrue(schedule.is_due(utc(2026, 10, 3, 3, 50), finished, local=True))

    def test_the_second_one_thirty_runs_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            everyday = Everyday(Path(tmp), llm="none")
            store = everyday.orchestrator.context.scheduler
            job = store.add("command", "what time is it", "daily at 1:30am", utc(2026, 10, 31, 12, 0))
            store.mark_ran(job.id, utc(2026, 11, 1, 5, 30), "ok")          # the 01:30 EDT run
            with patch("laptop_agent.agents.orchestrator.datetime", stopped(utc(2026, 11, 1, 6, 30))):
                result = asyncio.run(everyday.orchestrator.run_due_schedules())
            self.assertEqual(result.data["ran"], [])


class TheTickerReadsTheLaptopsClockTests(unittest.TestCase):
    def test_only_a_time_it_read_itself_takes_the_zone_rules(self) -> None:
        """What the ticker does, checked on any machine: CI runs in UTC, where no clock
        ever goes back, so only this shows a dropped `local` there."""
        with tempfile.TemporaryDirectory() as tmp:
            everyday = Everyday(Path(tmp), llm="none")
            store = everyday.orchestrator.context.scheduler
            real = store.claim_due_jobs
            asked: list[bool] = []

            def spy(now: datetime, local: bool = False):
                asked.append(local)
                return real(now, local=local)

            with patch.object(store, "claim_due_jobs", spy):
                asyncio.run(everyday.orchestrator.run_due_schedules())
                asyncio.run(everyday.orchestrator.run_due_schedules(datetime(2026, 11, 1, 6, 30, tzinfo=UTC)))
            self.assertEqual(asked, [True, False])


if __name__ == "__main__":
    unittest.main()

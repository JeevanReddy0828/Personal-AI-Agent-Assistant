"""Reminders that actually remind, timers, alarms, and managing them by voice.

Before this, a reminder was stored, confirmed ("Reminder #1 set for today at 6:00 PM")
and then never delivered: nothing read the due list unless asked. "tonight at 8" was set
for 8 AM, "at six" was not a time, a timer or an alarm reached a chat model that cannot set
one, and there was no way to cancel or snooze anything by saying so.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from laptop_agent.planner import HeuristicPlannerProvider
from laptop_agent.reminders import ReminderStore
from laptop_agent.timeparse import describe, parse_when, spoken_to_digits
from test_everyday_requests import Everyday

# A fixed Saturday morning, so every expectation is exact.
NOW = datetime(2026, 9, 26, 6, 12, tzinfo=timezone(timedelta(hours=-5)))


def resolved(text: str, **kwargs) -> str:
    when = parse_when(spoken_to_digits(text), NOW, **kwargs)
    return describe(when.at, NOW) if when else "none"


class PartOfDayTests(unittest.TestCase):
    def test_tonight_and_evening_make_a_bare_hour_pm(self) -> None:
        # Reported shape: "take out the trash tonight at 8" was set for 8 AM.
        self.assertEqual(resolved("take out the trash tonight at 8"), "today at 8:00 PM")
        self.assertEqual(resolved("tomorrow night at 9 to lock the door"), "tomorrow at 9:00 PM")
        self.assertEqual(resolved("this afternoon at 3 to call bob"), "today at 3:00 PM")

    def test_morning_keeps_a_bare_hour_am(self) -> None:
        # The 1-6 rule turned "tomorrow morning at 6" into 6 PM.
        self.assertEqual(resolved("tomorrow morning at 6 to run"), "tomorrow at 6:00 AM")

    def test_a_clock_time_beats_a_named_part_of_the_day(self) -> None:
        # "morning" is 9:00 on its own, and it used to win by standing later in the sentence.
        self.assertEqual(resolved("at 6 tomorrow morning to run"), "tomorrow at 6:00 AM")

    def test_only_words_beside_the_time_count(self) -> None:
        # "evening" belongs to the party, not to the 8.
        self.assertEqual(resolved("at 8 to prepare for the evening party"), "today at 8:00 AM")

    def test_an_alarm_reads_a_bare_hour_as_morning(self) -> None:
        self.assertEqual(resolved("at 6", default_half="am"), "tomorrow at 6:00 AM")
        self.assertEqual(resolved("at 6"), "today at 6:00 PM")


class SpokenTimeTests(unittest.TestCase):
    def test_numbers_said_out_loud(self) -> None:
        self.assertEqual(spoken_to_digits("call mom at six"), "call mom at 6")
        self.assertEqual(spoken_to_digits("at six thirty pm"), "at 6:30 pm")
        self.assertEqual(spoken_to_digits("in five minutes"), "in 5 minutes")
        self.assertEqual(spoken_to_digits("at quarter to eight"), "at 7:45")
        self.assertEqual(spoken_to_digits("at half past seven"), "at 7:30")
        self.assertEqual(spoken_to_digits("in a couple of minutes"), "in 2 minutes")

    def test_numbers_that_are_not_times_are_left_alone(self) -> None:
        self.assertEqual(spoken_to_digits("buy two apples at six"), "buy two apples at 6")

    def test_now_is_a_time_of_last_resort(self) -> None:
        self.assertEqual(resolved("now to stretch"), "today at 6:12 AM")
        self.assertEqual(resolved("check the store is open now at 5pm"), "today at 5:00 PM")


class ReminderStoreTests(unittest.TestCase):
    def test_remove_and_snooze(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = ReminderStore(Path(raw) / "reminders.json")
            first = store.add((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), "stretch")["reminder"]
            second = store.add((datetime.now(UTC) + timedelta(hours=1)).isoformat(), "call mom")["reminder"]
            self.assertEqual([item["id"] for item in store.due()], [first["id"]])
            snoozed = store.snooze(first["id"], datetime.now(UTC) + timedelta(minutes=10))
            self.assertEqual(snoozed["snoozed"], 1)
            self.assertEqual(store.due(), [])
            self.assertEqual(store.remove(second["id"])["message"], "call mom")
            self.assertIsNone(store.remove(second["id"]))
            self.assertIsNone(store.snooze(999, datetime.now(UTC)))


class VoiceRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = HeuristicPlannerProvider()

    def command(self, text: str) -> str:
        return self.planner.plan(text, "", {}).command or ""

    def test_timers_alarms_and_managing_them(self) -> None:
        cases = {
            "set a timer for 5 minutes": "timer set a timer for 5 minutes",
            "Jarvis. Set a timer for five minutes.": "timer Set a timer for 5 minutes.",
            "set a pasta timer for 10 minutes": "timer set a pasta timer for 10 minutes",
            "wake me up at 7": "alarm 7",
            "set an alarm for 6:30am": "alarm 6:30am",
            "delete reminder 2": "reminder delete 2",
            "cancel the last reminder": "reminder delete last",
            "cancel the reminder to call mom": "reminder delete call mom",
            "cancel the vitamins reminder": "reminder delete vitamins",
            "stop the timer": "reminder delete timer",
            "mark reminder 1 done": "reminder done 1",
            "snooze": "reminder snooze",
            "snooze for five minutes": "reminder snooze 5m",
            "snooze reminder 3": "reminder snooze 3",
        }
        for text, expected in cases.items():
            self.assertEqual(self.command(text), expected, text)

    def test_things_that_only_sound_like_it(self) -> None:
        for text in ("what is a timer", "alarm clock recommendations", "stop the music", "cancel that"):
            self.assertFalse(self.command(text).startswith(("timer", "alarm", "reminder")), text)


class ReminderConversationTests(unittest.TestCase):
    """The whole exchange, through handle(), the way it is said."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.everyday = Everyday(Path(self.tmp.name))
        self.reminders = self.everyday.orchestrator.context.reminders

    def say(self, text: str) -> str:
        result, _ran = self.everyday.say(text)
        return result.message

    def test_a_timer_is_a_reminder_that_goes_off(self) -> None:
        self.assertIn("Timer set: Timer (5 minutes)", self.say("set a timer for five minutes"))
        (item,) = self.reminders.list()
        due = datetime.fromisoformat(item["due_at"])
        self.assertAlmostEqual((due - datetime.now(UTC)).total_seconds(), 300, delta=5)

    def test_a_named_timer_keeps_its_name(self) -> None:
        self.assertIn("Pasta timer (10 minutes)", self.say("set a pasta timer for 10 minutes"))

    def test_an_alarm_is_in_the_morning(self) -> None:
        self.assertIn("Alarm set for", self.say("wake me up at 7"))
        (item,) = self.reminders.list()
        self.assertEqual(datetime.fromisoformat(item["due_at"]).astimezone().hour, 7)

    def test_dictated_reminder_loses_its_fillers_and_keeps_its_time(self) -> None:
        message = self.say("uh remind me to uh call mom at six")
        self.assertTrue(message.endswith(": call mom"), message)

    def test_a_daily_reminder_repeats_on_the_scheduler(self) -> None:
        message = self.say("remind me every day at 8am to take my vitamins")
        self.assertIn("daily at 08:00", message)
        (job,) = self.everyday.orchestrator.context.scheduler.list_jobs()
        self.assertEqual(job.spec, "reminder add now take my vitamins")
        self.assertEqual(self.reminders.list(), [])
        self.assertIn("take my vitamins", self.say("what are my reminders"))
        self.assertIn("Stopped the repeating reminder", self.say("cancel the vitamins reminder"))
        self.assertEqual(self.everyday.orchestrator.context.scheduler.list_jobs(), [])

    def test_the_scheduled_job_really_raises_a_reminder(self) -> None:
        # What the ticker runs at 8am: the reminder it creates is due at once.
        self.say("reminder add now take my vitamins")
        (item,) = self.reminders.due()
        self.assertEqual(item["message"], "take my vitamins")

    def test_a_weekly_repeat_is_set_once_and_says_so(self) -> None:
        message = self.say("remind me every monday at 10 to water plants")
        self.assertIn("Monday at 10:00 AM", message)
        self.assertIn("set once", message)

    def test_cancel_snooze_and_done_by_what_you_say(self) -> None:
        self.say("remind me to call mom at 6pm")
        self.say("remind me to buy milk tomorrow at 9")
        self.assertIn("Which one?", self.say("cancel my reminder"))
        self.assertIn("Cancelled: buy milk", self.say("cancel the reminder to buy milk"))
        self.assertIn("Nothing is going off", self.say("snooze"))
        self.assertIn("Snoozed until", self.say("snooze reminder 1"))
        self.assertIn("Done: call mom", self.say("mark reminder 1 done"))
        self.assertIn("no active reminder #99", self.say("delete reminder 99"))
        self.assertEqual(self.reminders.list(), [])

    def test_snooze_without_an_id_takes_the_one_that_is_going_off(self) -> None:
        self.reminders.add((datetime.now(UTC) - timedelta(seconds=5)).isoformat(), "stretch")
        self.reminders.add((datetime.now(UTC) + timedelta(hours=3)).isoformat(), "call mom")
        self.assertIn("stretch", self.say("snooze for five minutes"))
        self.assertEqual(self.reminders.due(), [])

    def test_the_list_is_readable_and_printed_once(self) -> None:
        self.say("remind me to call mom at 6pm")
        listing = self.say("what are my reminders")
        self.assertIn("call mom", listing)
        self.assertEqual(listing.count("call mom"), 1, listing)
        self.assertNotIn("+00:00", listing)


class DeliveryApiTests(unittest.TestCase):
    """/api/reminders against a live server: what the page polls, and its card buttons."""

    @classmethod
    def setUpClass(cls) -> None:
        import laptop_agent.webui as webui

        cls.webui = webui
        cls._tmp = tempfile.TemporaryDirectory()
        cls.store = ReminderStore(Path(cls._tmp.name) / "reminders.json")
        cls._original = webui._orchestrator.context.reminders
        object.__setattr__(webui._orchestrator.context, "reminders", cls.store)
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        object.__setattr__(cls.webui._orchestrator.context, "reminders", cls._original)
        cls._tmp.cleanup()

    def setUp(self) -> None:
        for item in self.store.list(include_done=True):
            self.store.remove(int(item["id"]))

    def get(self) -> dict:
        return json.loads(urllib.request.urlopen(self.base + "/api/reminders", timeout=15).read())

    def post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            self.base + "/api/reminders", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Jarvis-Token": self.webui._API_TOKEN})
        return json.loads(urllib.request.urlopen(request, timeout=15).read())

    def test_due_and_upcoming_with_the_time_to_the_next(self) -> None:
        now = datetime.now(UTC)
        self.store.add((now - timedelta(minutes=1)).isoformat(), "stretch")
        self.store.add((now + timedelta(seconds=90)).isoformat(), "Timer (90 seconds)")
        snapshot = self.get()
        self.assertEqual([item["message"] for item in snapshot["due"]], ["stretch"])
        self.assertEqual([item["message"] for item in snapshot["upcoming"]], ["Timer (90 seconds)"])
        self.assertTrue(0 < snapshot["next_in"] <= 90, snapshot["next_in"])
        self.assertIn("today at", snapshot["due"][0]["due_spoken"])

    def test_polling_writes_no_latency_traces(self) -> None:
        """A poll every half minute through handle() would push real turns out of the
        300-trace ring within hours."""
        before = len(self.webui._orchestrator.traces.recent(300))
        for _ in range(3):
            self.get()
        self.assertEqual(len(self.webui._orchestrator.traces.recent(300)), before)

    def test_the_card_buttons(self) -> None:
        now = datetime.now(UTC)
        stretch = self.store.add((now - timedelta(minutes=1)).isoformat(), "stretch")["reminder"]
        water = self.store.add((now - timedelta(minutes=1)).isoformat(), "water plants")["reminder"]
        snoozed = self.post({"action": "snooze", "id": stretch["id"], "minutes": 5})
        self.assertTrue(snoozed["ok"], snoozed)
        self.assertEqual([item["message"] for item in snoozed["due"]], ["water plants"])
        done = self.post({"action": "done", "id": water["id"]})
        self.assertTrue(done["ok"], done)
        self.assertEqual(done["due"], [])
        deleted = self.post({"action": "delete", "id": stretch["id"]})
        self.assertTrue(deleted["ok"], deleted)
        self.assertEqual(deleted["upcoming"], [])

    def test_a_bad_request_is_refused(self) -> None:
        for payload in ({"action": "explode", "id": 1}, {"action": "done", "id": "1"},
                        {"action": "done", "id": True}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.post(payload)
            self.assertEqual(caught.exception.code, 400)


class CliDeliveryTests(unittest.TestCase):
    def test_each_due_reminder_is_announced_once(self) -> None:
        from laptop_agent.cli import _due_reminder_lines, _seconds_to_next

        with tempfile.TemporaryDirectory() as raw:
            store = ReminderStore(Path(raw) / "reminders.json")
            store.add((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), "stretch")
            store.add((datetime.now(UTC) + timedelta(seconds=4)).isoformat(), "Timer (5 seconds)")
            announced: set = set()
            lines = _due_reminder_lines(store, announced)
            self.assertEqual(len(lines), 1)
            self.assertIn("stretch", lines[0])
            self.assertEqual(_due_reminder_lines(store, announced), [])
            # It wakes up for the timer, not fifteen seconds later.
            self.assertLessEqual(_seconds_to_next(store), 5)


if __name__ == "__main__":
    unittest.main()

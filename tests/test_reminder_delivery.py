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
            "stop the timer": "reminder stop timer",       # stop: what is going off, never a schedule
            "turn off my alarm": "reminder stop alarm",
            "mark reminder 1 done": "reminder done 1",
            "snooze": "reminder snooze",
            "snooze for five minutes": "reminder snooze 5m",
            "snooze reminder 3": "reminder snooze 3",
            # "can you please" is both halves of a polite prefix that used to allow one.
            "can you please set a timer for five minutes": "timer can you please set a timer for 5 minutes",
            "count down 10 minutes": "timer count down 10 minutes",
            "set a timer": "timer",
            "how much time is left on my timer": "timers",
            "show my timers": "timers",
            "never mind the timer": "reminder delete timer",
            "never mind the pasta timer": "reminder delete pasta timer",     # found on the live server
            "forget my dentist reminder": "reminder delete dentist",
            "i don't need the alarm anymore": "reminder delete alarm",
            "forget the timer": "reminder delete timer",
            "stop reminding me about the oven": "reminder delete the oven",
            "cancel all my reminders": "reminder delete all reminders",
            "delete all timers": "reminder delete all timers",
            "clear my reminders": "reminder delete all reminders",
            "snooze 5 more minutes": "reminder snooze 5m",
            "snooze for another 10 minutes": "reminder snooze 10m",
            "what's my next reminder": "reminders next",
            "when does my alarm go off": "reminders next alarm",
            "is my alarm set": "reminders next alarm",
            "set an alarm for every weekday at 7": "alarm every weekday at 7",
            "time left": "timers",
        }
        for text, expected in cases.items():
            self.assertEqual(self.command(text), expected, text)

    def test_things_that_only_sound_like_it(self) -> None:
        for text in ("what is a timer", "alarm clock recommendations", "stop the music", "cancel that",
                     "never mind", "i don't need the car anymore", "stop reminding me", "delete everything",
                     "remind me of my wife's birthday"):
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
        self.assertIn("Timer set for 5 minutes. It goes off", self.say("set a timer for five minutes"))
        (item,) = self.reminders.list()
        due = datetime.fromisoformat(item["due_at"])
        self.assertAlmostEqual((due - datetime.now(UTC)).total_seconds(), 300, delta=5)

    def test_a_named_timer_keeps_its_name(self) -> None:
        self.assertIn("Pasta timer set for 10 minutes", self.say("set a pasta timer for 10 minutes"))
        (item,) = self.reminders.list()
        self.assertEqual(item["message"], "Pasta timer (10 minutes)")   # what the card will say

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

    def test_a_weekly_repeat_repeats_on_its_day(self) -> None:
        # It used to be set once, for the coming Monday, with a note that it would not repeat.
        message = self.say("remind me every monday at 10 to water plants")
        self.assertIn("Repeating reminder set — Mondays at 10:00: water plants.", message)
        (job,) = self.everyday.orchestrator.context.scheduler.list_jobs()
        self.assertEqual(job.schedule.days, (0,))
        self.assertIn("weekdays at 08:00", self.say("remind me on weekdays at 8am to stand up"))
        # What still cannot repeat is set once, and says so.
        message = self.say("remind me every month on 2026-10-01 at 9 to pay rent")
        self.assertIn("set once", message)

    def test_an_alarm_can_repeat(self) -> None:
        # "every weekday at 7" was set once, for tomorrow, and the repeat dropped unsaid.
        self.assertEqual(self.say("set an alarm for every weekday at 7"),
                         "Alarm set — weekdays at 07:00. Say \"cancel the alarm\" to stop it.")
        self.assertIn("daily at 06:30", self.say("wake me up every day at 6:30"))
        self.assertIn("Repeating: weekdays at 07:00 — Alarm", self.say("when is my next alarm"))
        # The same request twice is one alarm, not two ringing together.
        self.assertIn("already set", self.say("set an alarm for every weekday at 7"))
        self.assertEqual(len(self.everyday.orchestrator.context.scheduler.list_jobs()), 2)

    def test_stop_only_stops_what_is_going_off(self) -> None:
        # "stop the alarm" with nothing ringing deleted the weekday alarm - an overslept morning.
        self.say("set an alarm for every weekday at 7")
        message = self.say("stop the alarm")
        self.assertIn("Nothing is going off right now", message)
        self.assertEqual(len(self.everyday.orchestrator.context.scheduler.list_jobs()), 1)
        # What is ringing is what stops, and the schedule behind it stays.
        self.reminders.add((datetime.now(UTC) - timedelta(seconds=5)).isoformat(), "Alarm")
        self.reminders.add((datetime.now(UTC) + timedelta(hours=20)).isoformat(), "Alarm")
        self.assertEqual(self.say("turn off the alarm"), "Stopped: Alarm.")
        self.assertEqual(self.reminders.due(), [])
        self.assertEqual(len(self.reminders.list()), 1)
        self.assertEqual(len(self.everyday.orchestrator.context.scheduler.list_jobs()), 1)
        # A running timer is stopped by "stop"; "cancel" is what removes a schedule.
        self.say("set a timer for 10 minutes")
        self.assertIn("Stopped: Timer (10 minutes).", self.say("stop the timer"))
        self.assertIn("Stopped the repeating reminder: Alarm.", self.say("cancel the alarm"))

    def test_cancelling_a_ringing_alarm_keeps_its_schedule(self) -> None:
        # The repeating job was matched first: its schedule was deleted, and it kept ringing.
        self.say("set an alarm for every weekday at 7")
        self.reminders.add((datetime.now(UTC) - timedelta(seconds=5)).isoformat(), "Alarm")   # this morning's
        self.assertEqual(self.say("cancel the alarm"), "Stopped: Alarm.")
        self.assertEqual(self.reminders.due(), [])
        self.assertEqual(len(self.everyday.orchestrator.context.scheduler.list_jobs()), 1)

    def test_next_and_how_much_longer(self) -> None:
        self.assertIn("You have no reminders coming up.", self.say("what's my next reminder"))
        self.say("remind me to call mom tomorrow at 6pm")
        self.say("remind me to stretch in 2 hours")
        self.assertIn(": stretch.", self.say("when is my next reminder"))    # the sooner one
        self.say("set a pasta timer for 10 minutes")
        self.assertRegex(self.say("how much longer"), r"^Pasta timer: \*\*")
        self.assertRegex(self.say("time left"), r"^Pasta timer: \*\*")

    def test_durations_said_in_parts(self) -> None:
        self.assertIn(": stretch", self.say("remind me in an hour and a half to stretch"))
        (item,) = self.reminders.list()
        due = datetime.fromisoformat(item["due_at"])
        self.assertAlmostEqual((due - datetime.now(UTC)).total_seconds(), 5400, delta=5)
        self.assertIn("too far ahead", self.say("remind me in 99999999999 days to stretch"))

    def test_snooze_said_loosely(self) -> None:
        self.reminders.add((datetime.now(UTC) - timedelta(seconds=5)).isoformat(), "stretch")
        self.assertIn("Snoozed until", self.say("snooze 5 more minutes"))
        (item,) = self.reminders.list()
        due = datetime.fromisoformat(item["due_at"])
        self.assertAlmostEqual((due - datetime.now(UTC)).total_seconds(), 300, delta=5)

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

    def test_a_timer_is_named_only_by_what_names_it(self) -> None:
        # The leftover words used to be the name: "Could you please timer set for 15 minute".
        self.assertIn("Timer set for 15 minutes.", self.say("could you set a 15 minute timer please"))
        self.assertEqual(self.reminders.list()[-1]["message"], "Timer (15 minutes)")
        self.assertIn("Timer set for 1 hour 30 minutes.", self.say("set a timer for an hour and a half"))
        self.assertIn("Timer set for 1 minute 30 seconds.", self.say("count down 90 seconds"))
        self.assertIn("Timer set for 1 hour 30 minutes.", self.say("set a timer for 1 hour and 30 minutes"))
        self.assertIn("Pasta timer set for 10 minutes.", self.say("set a timer for 10 minutes for the pasta"))
        self.assertIn("Timer to check the oven set for 5 minutes.",
                      self.say("set a timer for 5 minutes to check the oven"))

    def test_a_timer_needs_a_length_and_a_sane_one(self) -> None:
        self.assertIn("How long should the timer run?", self.say("set a timer"))
        self.assertIn("longer than a timer should run", self.say("timer 99999 hours"))
        # A number inside another token is not a length: this set a 309-minute timer.
        self.assertIn("How long should the timer run?", self.say("timer 1e309 minutes"))
        self.assertEqual(self.reminders.list(), [])
        # Days are a length too; without them the request went to a model free to claim it.
        self.assertIn("Timer set for 2 days.", self.say("set a timer for 2 days"))

    def test_how_long_is_left(self) -> None:
        self.assertIn("No timer is running", self.say("how much time is left on my timer"))
        self.say("set a pasta timer for 10 minutes")
        self.say("remind me to call mom at 6pm")                # not a timer, not listed
        left = self.say("how much time is left on my timer")
        self.assertRegex(left, r"^Pasta timer: \*\*(?:10 minutes|9 minutes 5\d seconds)\*\* left \(at ")
        self.assertNotIn("call mom", left)

    def test_letting_go_of_one(self) -> None:
        self.say("set a pasta timer for 10 minutes")
        self.say("remind me to call mom at 6pm")
        self.assertIn("Cancelled: Pasta timer (10 minutes).", self.say("never mind the pasta timer"))
        self.assertIn("Cancelled: call mom.", self.say("stop reminding me to call mom"))
        self.assertEqual(self.reminders.list(), [])
        # "forget" is also a direct command for facts; this one names a reminder.
        self.say("remind me about the dentist tomorrow at 9")
        self.assertIn("Cancelled: the dentist.", self.say("forget my dentist reminder"))

    def test_cancelling_all_of_them_asks_first(self) -> None:
        self.say("remind me to call mom at 6pm")
        self.say("remind me to buy milk tomorrow at 9")
        result, ran = self.everyday.say("cancel all my reminders")
        self.assertEqual(ran, "(denied)")
        self.assertIn(("high", "Cancel all 2 reminders"), self.everyday.approvals)
        self.assertEqual(len(self.reminders.list()), 2)              # refused: nothing removed
        self.everyday.orchestrator.context.web.approval_gate._ask = lambda request: True
        self.assertIn("Cancelled all 2 reminders.", self.say("cancel all my reminders"))
        self.assertEqual(self.reminders.list(), [])
        self.assertIn("no reminders to cancel", self.say("cancel all my reminders"))

    def test_hurried_and_half_said_times(self) -> None:
        self.assertIn("at 6:00 PM: call mom", self.say("remind me to call mom at 6ppm"))
        self.assertIn("What should I remind you about", self.say("remind me in 5"))
        message = self.say("remind me in 5 to stretch")
        self.assertIn(": stretch", message)
        (item,) = [item for item in self.reminders.list() if item["message"] == "stretch"]
        due = datetime.fromisoformat(item["due_at"])
        self.assertAlmostEqual((due - datetime.now(UTC)).total_seconds(), 300, delta=5)

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

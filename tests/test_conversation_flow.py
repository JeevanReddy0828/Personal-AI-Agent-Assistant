"""A conversation, not a string of unrelated commands: two requests said in one breath,
and a short answer to a question the assistant itself just asked.

Found by the third everyday corpus. "set a timer for 5 minutes and remind me to call mom at
6pm" set the timer and silently dropped the reminder. "set a timer" was answered "How long
should the timer run?", and the answer "10 minutes" then arrived alone, which nothing could
act on - every clarifying question was a dead end.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_everyday_requests import Everyday


class Conversation:
    """Turns said one after another, with the history exactly as the web page sends it:
    `{"role": "user"|"assistant", "text": ...}` (app.js sessionHistory; the CLI the same).
    Written with "content" keys, these tests once passed against code that did nothing in
    the real page."""

    def __init__(self, everyday: Everyday, key: str = "text") -> None:
        self.everyday = everyday
        self.key = key
        self.history: list[dict[str, str]] = []

    def say(self, text: str) -> str:
        result, _ran = self.everyday.say(text, stream=False, history=list(self.history))
        message = result.message if result is not None else "(denied)"
        self.history += [{"role": "user", self.key: text}, {"role": "assistant", self.key: message}]
        return message


class TwoRequestsInOneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.everyday = Everyday(Path(self.tmp.name))
        self.reminders = self.everyday.orchestrator.context.reminders

    def test_both_run(self) -> None:
        result, ran = self.everyday.say("set a timer for 5 minutes and remind me to call mom at 6pm")
        self.assertIn("Timer set for 5 minutes.", result.message)
        self.assertIn(": call mom", result.message)
        self.assertEqual(sorted(item["message"] for item in self.reminders.list()), ["Timer (5 minutes)", "call mom"])
        self.assertEqual([part["ok"] for part in result.data["parts"]], [True, True])
        self.assertIn("due_local", result.data)      # the page still schedules the delivery
        result, ran = self.everyday.say("what's the weather and what time is it")
        self.assertEqual(ran.split(" ;; ")[0], "weather")
        self.assertIn("Austin", result.message)
        self.assertIn("UTC", result.message)

    def test_an_and_inside_one_request_stays_in_it(self) -> None:
        # "eggs at 6pm" is not a request of its own, so the sentence is one reminder.
        message = self.everyday.say("remind me to buy milk and eggs at 6pm")[0].message
        self.assertIn(": buy milk and eggs", message)
        self.assertEqual(len(self.reminders.list()), 1)
        message = self.everyday.say("add milk and eggs to my shopping list")[0].message
        self.assertIn("Added milk and eggs", message)
        # "hotels in paris" does not start like a request: it is what the search is for.
        _result, ran = self.everyday.say("search for flights and hotels in paris")
        self.assertNotIn(";;", ran or "")

    def test_a_refused_part_does_not_stop_the_other(self) -> None:
        self.reminders.add("2030-01-01T09:00:00+00:00", "one")
        self.reminders.add("2030-01-02T09:00:00+00:00", "two")
        result, _ran = self.everyday.say("cancel all my reminders and set a timer for 5 minutes")
        self.assertIn("Not approved", result.message)            # HIGH is refused in this harness
        self.assertIn("Timer set for 5 minutes.", result.message)
        self.assertEqual(len(self.reminders.list()), 3)


class FollowUpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.everyday = Everyday(Path(self.tmp.name))
        self.talk = Conversation(self.everyday)
        self.reminders = self.everyday.orchestrator.context.reminders

    def test_how_long_then_the_length(self) -> None:
        self.assertIn("How long should the timer run?", self.talk.say("set a timer"))
        self.assertIn("Timer set for 10 minutes.", self.talk.say("10 minutes"))

    def test_either_history_shape(self) -> None:
        # The chat-completions shape ("content") works too, for any other client.
        talk = Conversation(self.everyday, key="content")
        talk.say("set a timer")
        self.assertIn("Timer set for 5 minutes.", talk.say("5 minutes"))

    def test_what_then_the_subject(self) -> None:
        self.assertIn("What should I remind you about", self.talk.say("remind me in 5"))
        self.assertIn(": stretch", self.talk.say("to stretch"))
        (item,) = self.reminders.list()
        self.assertEqual(item["message"], "stretch")

    def test_when_then_the_time(self) -> None:
        self.assertIn("I could not find a time in that", self.talk.say("remind me to call mom"))
        self.assertIn("6:00 PM: call mom", self.talk.say("6pm"))

    def test_a_fact_asked_for_then_given(self) -> None:
        self.assertIn("You haven't told me your name", self.talk.say("what's my name"))
        self.assertIn("your name is Jeevan", self.talk.say("it's Jeevan"))
        self.assertIn("haven't told me where you live", self.talk.say("where do i live"))
        self.assertIn("your city is Austin", self.talk.say("Austin"))
        self.assertIn("I don't know when your birthday is", self.talk.say("when is my birthday"))
        self.assertIn("your birthday is march 3rd", self.talk.say("march 3rd"))
        self.assertIn("3 March", self.talk.say("when is my birthday"))

    def test_which_one_then_the_pick(self) -> None:
        self.talk.say("remind me to call mom at 6pm")
        self.talk.say("remind me to buy milk tomorrow at 9")
        self.assertIn("Which one?", self.talk.say("cancel my reminder"))
        self.assertIn("Cancelled: buy milk.", self.talk.say("the second one"))
        self.assertEqual([item["message"] for item in self.reminders.list()], ["call mom"])

    def test_a_reply_that_is_not_an_answer_is_left_alone(self) -> None:
        self.talk.say("what's my name")
        for reply in ("never mind", "no", "what time is it", "why do you ask"):
            self.talk.say(reply)
            self.assertNotIn("name", self.everyday.orchestrator.context.memory.get_profile(), reply)
            self.talk.say("what's my name")
        # Filler is not an answer: "it's" was filed as the name, "to" as a reminder.
        self.talk.say("it's")
        self.assertNotIn("name", self.everyday.orchestrator.context.memory.get_profile())
        self.talk.say("remind me in 5")
        self.talk.say("to")
        self.assertEqual(self.reminders.list(), [])
        # A request of its own is handled as itself, not as the answer.
        self.assertIn("How long should the timer run?", self.talk.say("set a timer"))
        self.assertIn("Added milk", self.talk.say("add milk to my shopping list"))
        self.assertEqual(self.reminders.list(), [])

    def test_no_question_asked_means_no_rewrite(self) -> None:
        self.talk.say("what time is it")
        result, ran = self.everyday.say("10 minutes", history=list(self.talk.history))
        self.assertIsNone(ran)
        self.assertEqual(self.reminders.list(), [])


if __name__ == "__main__":
    unittest.main()

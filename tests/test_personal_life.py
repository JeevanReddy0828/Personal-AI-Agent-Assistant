"""Lists, remembered facts and notes, the calendar stand-in, this machine, and the everyday
phrasings that reached a chat model which could not act on them.

Found by driving a conversational corpus through the real orchestrator: "remember that I
parked on level 3" answered "Saved note." and "what do you know about me" then said nothing
was saved; "add milk to my shopping list" had no home at all; "what's on my calendar today"
was answered from a web search for the sentence.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.memory import MemoryStore, list_name
from laptop_agent.planner import HeuristicPlannerProvider
from test_everyday_requests import Everyday, FakeTier


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.everyday = Everyday(Path(self.tmp.name))

    def say(self, text: str) -> str:
        result, _ran = self.everyday.say(text)
        return result.message

    def test_a_note_is_remembered_and_shown(self) -> None:
        self.assertIn("I parked on level 3", self.say("remember that I parked on level 3"))
        self.assertIn("I parked on level 3", self.say("what do you know about me"))
        self.assertIn("I parked on level 3", self.say("memory"))

    def test_a_key_with_an_apostrophe_is_a_fact(self) -> None:
        self.assertIn("your wife's birthday is june 5", self.say("remember my wife's birthday is june 5"))
        self.assertEqual(self.everyday.orchestrator.context.memory.get_profile()["wife's_birthday"], "june 5")

    def test_facts_said_without_remember(self) -> None:
        self.say("my name is Jeevan")
        self.say("i live in Austin")
        self.say("note that the wifi password is hunter2")
        profile = self.everyday.orchestrator.context.memory.get_profile()
        self.assertEqual(profile["name"], "Jeevan")
        self.assertEqual(profile["city"], "Austin")
        self.assertEqual(profile["wifi_password"], "hunter2")

    def test_the_chat_model_is_told_the_notes(self) -> None:
        seen: list[dict] = []

        class Recording(FakeTier):
            def stream_answer(self, text, memory_profile, *args, **kwargs):
                seen.append(dict(memory_profile))
                yield "[answered]"

        self.everyday.orchestrator.planner.provider = Recording("fast")
        self.say("remember that I parked on level 3")
        self.say("where did I park")
        self.assertIn("I parked on level 3", str(seen[-1]))

    def test_a_fact_is_read_back_not_guessed(self) -> None:
        # Each of these reached a chat model, which could only repeat what it was shown.
        self.assertIn("You haven't told me your name yet", self.say("what's my name"))
        self.assertIn("You haven't told me where you live", self.say("where do i live"))
        self.say("my name is Jeevan")
        self.say("i live in Austin")
        self.say("remember my favourite restaurant is olive garden")
        self.say("remember my wife's birthday is june 5")
        self.assertEqual(self.say("do you remember my name"), "Your name is Jeevan.")
        self.assertEqual(self.say("what is my name again?"), "Your name is Jeevan.")
        self.assertEqual(self.say("where do i live"), "You live in Austin.")
        self.assertEqual(self.say("what's my favorite restaurant"), "Your favorite restaurant is olive garden.")
        self.assertEqual(self.say("what's my wife's birthday"), "Your wife's birthday is june 5.")
        self.assertEqual(self.say("remind me of my wife's birthday"), "Your wife's birthday is june 5.")
        result, ran = self.everyday.say("hey jarvis, what's my name")
        self.assertEqual((result.message, ran), ("Your name is Jeevan.", "what's my name"))

    def test_a_fact_can_be_corrected(self) -> None:
        self.say("my name is Jeevan")
        self.assertIn("your name is Jeev", self.say("change my name to Jeev"))
        self.assertEqual(self.say("what's my name"), "Your name is Jeev.")

    def test_a_question_about_something_else_goes_on(self) -> None:
        # "my ip" is not a fact anyone told it, and must not be answered "you haven't told me".
        result, ran = self.everyday.say("what's my ip")
        self.assertIsNone(ran)
        self.assertIn("answered]", result.message)
        self.assertEqual(self.everyday.say("what's my battery")[1], "system status")

    def test_a_note_can_be_forgotten_and_a_vague_word_forgets_nothing(self) -> None:
        self.say("remember that I parked on level 3")
        self.say("remember the door code is 4521")
        self.assertEqual(self.everyday.orchestrator.context.memory.forget_notes("the"), [])
        self.assertIn("Forgotten", self.say("forget that I parked on level 3"))
        self.assertNotIn("parked", self.say("memory"))


class ListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.everyday = Everyday(Path(self.tmp.name))

    def say(self, text: str) -> str:
        result, _ran = self.everyday.say(text)
        return result.message

    def test_a_shopping_list_the_way_it_is_said(self) -> None:
        self.assertIn("Added milk, eggs and bread to your shopping list",
                      self.say("add milk, eggs and bread to my shopping list"))
        self.assertIn("already on", self.say("put milk on the grocery list"))   # one list, two names
        self.assertIn("Removed eggs", self.say("remove eggs from my shopping list"))
        listing = self.say("what's on my shopping list")
        self.assertIn("- milk", listing)
        self.assertNotIn("eggs", listing)
        self.assertIn("shopping", self.say("what lists do i have"))
        self.assertIn("Cleared your shopping list (2 items)", self.say("clear my shopping list"))
        self.assertIn("empty", self.say("what's on my shopping list"))

    def test_list_the_verb_still_reaches_everything_else(self) -> None:
        """A bare `list ` command would have swallowed these."""
        for text in ("list files in downloads", "list the planets in order"):
            _result, ran = self.everyday.say(text)
            self.assertFalse((ran or "").startswith("list "), f"{text} ran {ran}")

    def test_a_list_asked_for_every_other_way(self) -> None:
        self.say("would you mind adding eggs to my shopping list")
        self.assertIn("- eggs", self.say("shopping list"))
        self.assertIn("- eggs", self.say("what do i need to buy"))
        self.assertIn("- eggs", self.say("what's on the list"))          # the only list there is
        self.say("add call mom to my to do list")
        self.assertIn("Your lists:", self.say("what's on the list"))    # which one? all of them
        # A near spelling of a list that exists is that list, not a second one beside it.
        self.assertIn("to your shopping list (2 items)", self.say("add milk to my shoping list"))
        self.assertEqual(sorted(self.everyday.orchestrator.context.memory.lists()), ["shopping", "todo"])

    def test_a_list_edit_that_names_no_list(self) -> None:
        self.assertIn("Which list should milk go on?", self.say("add milk to my list"))
        self.say("add milk and eggs to my shopping list")
        self.assertIn("Added bread to your shopping list", self.say("add bread to the list"))   # the only one
        self.assertIn("Removed milk from your shopping list", self.say("delete milk from my list"))
        self.say("add call mom to my todo list")
        self.assertIn("Which list — shopping, todo?", self.say("put butter on the list"))
        self.assertIn("isn't on any of your lists", self.say("remove caviar from my list"))
        self.say("add bread to my todo list")
        self.assertIn("on your shopping and todo lists — which one?", self.say("remove bread from the list"))

    def test_a_list_that_does_not_exist_is_not_invented(self) -> None:
        result, ran = self.everyday.say("python list")
        self.assertIsNone(ran)
        self.assertIn("answered]", result.message)
        self.assertIn("shopping list is empty", self.say("what do i need to buy"))

    def test_list_names(self) -> None:
        self.assertEqual(list_name("grocery"), "shopping")
        self.assertEqual(list_name("to-do list"), "todo")
        with tempfile.TemporaryDirectory() as raw:
            store = MemoryStore(Path(raw) / "memory.json")
            store.add_to_list("Shopping", ["Milk"])
            self.assertEqual(MemoryStore(Path(raw) / "memory.json").list_items("shopping"), ["Milk"])


class HonestAboutWhatIsConnectedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.everyday = Everyday(Path(self.tmp.name))

    def test_the_calendar_says_it_is_not_connected_and_shows_reminders(self) -> None:
        self.everyday.say("remind me to call mom at 6pm")
        result, ran = self.everyday.say("what's on my calendar today")
        self.assertEqual(ran, "calendar")
        self.assertIn("no calendar is connected", result.message)
        self.assertIn("call mom", result.message)

    def test_adding_to_the_calendar_sets_a_reminder_and_says_so(self) -> None:
        result, ran = self.everyday.say("schedule a meeting with john tomorrow at 3pm")
        self.assertIn("I set a reminder instead", result.message)
        self.assertIn("tomorrow at 3:00 PM", result.message)
        (item,) = self.everyday.orchestrator.context.reminders.list()
        self.assertEqual(item["message"], "a meeting with john")

    def test_system_status_reads_the_machine(self) -> None:
        result, ran = self.everyday.say("how much battery do i have")
        self.assertEqual(ran, "system status")
        self.assertIn("Disk:", result.message)
        self.assertIn("Battery", result.message)

    def test_with_no_model_a_question_says_what_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result, _ran = Everyday(Path(raw), llm="none").say("what is photosynthesis", stream=False)
            self.assertIn("no language model is connected", result.message)
            self.assertIn("reminders", result.message)
            result, _ran = Everyday(Path(raw), llm="dead").say("what is photosynthesis", stream=False)
            self.assertIn("couldn't reach any configured language model", result.message)

    def test_an_unknown_command_is_not_blamed_on_a_missing_model(self) -> None:
        import asyncio

        result = asyncio.run(self.everyday.orchestrator.handle("frobnicate the widget", _allow_planner=False))
        self.assertFalse(result.ok)
        self.assertIn("don't know how to do that", result.message)
        self.assertNotIn("LLM provider", result.message)


class ScreenshotTests(unittest.TestCase):
    def test_a_screenshot_never_overwrites_a_file(self) -> None:
        """MEDIUM runs without a click in the web app, and `screenshot report.docx` wrote a
        picture over the user's document."""
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            document = Path(raw) / "report.docx"
            document.write_text("my thesis", encoding="utf-8")
            result, _ran = everyday.say(f"screenshot {document}")
            self.assertTrue(result.ok, result.message)
            self.assertEqual(document.read_text(encoding="utf-8"), "my thesis")
            self.assertTrue(result.data["path"].endswith(".png"))
            again, _ran = everyday.say("take a screenshot")
            self.assertTrue(again.ok, again.message)
            self.assertIn(str(Path(raw)), again.data["path"])


class ChanceTests(unittest.TestCase):
    """A chat model cannot draw at random: it gives the likeliest continuation, which is the
    same "Heads!" every time it is asked the same way."""

    def test_draws(self) -> None:
        from laptop_agent.tools.chance import draw

        self.assertEqual({draw("flip a coin").message for _ in range(200)}, {"**Heads**.", "**Tails**."})
        rolls = {draw("roll a dice").data["rolls"][0] for _ in range(300)}
        self.assertEqual(rolls, {1, 2, 3, 4, 5, 6})
        self.assertIn("in total", draw("roll 2 dice").message)
        self.assertLessEqual(max(draw("roll a d20").data["rolls"][0] for _ in range(50)), 20)
        picks = {draw("pick a number between 1 and 3").data["value"] for _ in range(100)}
        self.assertEqual(picks, {1, 2, 3})
        self.assertEqual(draw("pick a number between 10 and 1").data["value"] in range(1, 11), True)
        self.assertFalse(draw("roll 50 dice").ok)
        # Refused by size rather than missed by the pattern and handed to a model to invent.
        self.assertFalse(draw("roll 99999999999999999999 dice").ok)
        self.assertFalse(draw("roll a d" + "9" * 5000).ok)
        self.assertFalse(draw("pick a number between 1 and " + "9" * 40).ok)
        self.assertIsNone(draw("roll with it"))
        self.assertIsNone(draw("coin a phrase"))

    def test_through_the_assistant(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            result, ran = everyday.say("can you flip a coin for me")
            self.assertIn(result.message, {"**Heads**.", "**Tails**."})
            result, _ran = everyday.say("roll a dice")
            self.assertRegex(result.message, r"^You rolled an? \*\*[1-6]\*\*\.$")


class EverydayPhrasingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = HeuristicPlannerProvider()

    def command(self, text: str) -> str:
        return self.planner.plan(text, "", {}).command or ""

    def test_they_reach_their_tools(self) -> None:
        cases = {
            "add milk to my shopping list": "list shopping add milk",
            "what's on my to-do list": "list to-do show",
            "my name is jeevan": "remember name = jeevan",
            "call me boss": "remember name = boss",
            "take a note: buy batteries": "remember buy batteries",
            "what's on my calendar today": "calendar",
            "am i free on friday afternoon": "calendar",
            "how much battery do i have": "system status",
            "take a screenshot": "screenshot",
            "open notepad": "open app notepad",
            "open gmail": "open url https://mail.google.com",
            "show my jobs": "jobs",
            "pull jobs from jobright": "jobright pull",
            "restaurants nearby": "around restaurants",
            "make a pdf about healthy eating": "document healthy eating as pdf",
            "draw a cat": "image cat",
            "tech news": "news tech",
            "email bob@example.com about lunch": "email to bob@example.com subject lunch body Draft email about: lunch",
        }
        for text, expected in cases.items():
            self.assertEqual(self.command(text), expected, text)

    def test_small_talk_is_recognised_whole(self) -> None:
        from laptop_agent.planner.heuristic import SMALL_TALK

        # strip_address takes the "hey" off "hey there", which left "there" to match nothing.
        for text in ("hey there", "hi there", "good afternoon jarvis", "thanks jarvis"):
            self.assertEqual(self.planner.plan(text, "", {}).explanation, SMALL_TALK, text)

    def test_near_misses_stay_conversation(self) -> None:
        for text in ("call me back later", "my car is broken", "open the door", "start a business",
                     "draw a conclusion", "paint the wall blue", "draw the diagram",
                     "how much space does a car need", "the latest news on nothing in particular was dull"):
            decision = self.planner.plan(text, "", {})
            self.assertFalse(decision.is_command and not decision.command.startswith("news"), f"{text} -> {decision.command}")


if __name__ == "__main__":
    unittest.main()

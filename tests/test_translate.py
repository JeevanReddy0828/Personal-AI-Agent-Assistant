from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from laptop_agent import nvcf
from laptop_agent.access import acting_as
from laptop_agent.accounts import Principal
from laptop_agent.planner.heuristic import HeuristicPlannerProvider
from laptop_agent.safety import ApprovalGate, RiskLevel
from laptop_agent.tools import translate
from laptop_agent.tools.translate import LANGUAGES, TranslateTool, parse_translation, script_language

import test_orchestrator


def echo(texts, source, target):
    """A translator that shows what it was asked: `[source>target] text` per piece."""
    return [f"[{source}>{target}] {text}" for text in texts]


class ParseTests(unittest.TestCase):
    def test_the_forms_people_use(self):
        cases = {
            "good morning to spanish": ("good morning", "spanish", None),
            "good morning into Japanese": ("good morning", "japanese", None),
            "good morning in french": ("good morning", "french", None),
            "bonjour from french to english": ("bonjour", "english", "french"),
            "bonjour to english from french": ("bonjour", "english", "french"),
            '"where is the station?" to german': ("where is the station?", "german", None),
            "the word in french to english": ("the word in french", "english", None),
            "good night to brazilian portuguese please": ("good night", "brazilian portuguese", None),
            "hello to telugu": ("hello", "telugu", None),
        }
        for raw, expected in cases.items():
            with self.subTest(raw):
                self.assertEqual(parse_translation(raw), expected)

    def test_the_language_can_lead_a_colon(self):
        # "translate this into french: the meeting is at noon" went to the chat model. The text
        # after the colon is taken whole, even when it ends in a language of its own.
        cases = {
            "this into french: the meeting is at noon": ("the meeting is at noon", "french", None),
            "to spanish: where is the train station?": ("where is the train station?", "spanish", None),
            "the following into german: I speak English": ("I speak English", "german", None),
            "from english to italian: 'good night'": ("good night", "italian", "english"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw):
                self.assertEqual(parse_translation(raw), expected)

    def test_a_sentence_no_language_closes_is_not_a_request(self):
        for raw in ("this into a plan of action", "the meeting notes", "to spanish", "to spanish:",
                    "my notes to klingon", "this into klingon: hello", ""):
            with self.subTest(raw):
                self.assertIsNone(parse_translation(raw))

    def test_a_script_names_its_language(self):
        self.assertEqual(script_language("ありがとう"), "ja")
        self.assertEqual(script_language("東京駅"), "zh-CN")
        self.assertEqual(script_language("東京駅はどこですか"), "ja")
        self.assertEqual(script_language("안녕하세요"), "ko")
        self.assertEqual(script_language("नमस्ते"), "hi")
        self.assertEqual(script_language("Привет"), "ru")
        self.assertEqual(script_language("Привіт"), "uk")
        self.assertIsNone(script_language("bonjour"))


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.asked = []
        self.requests = []
        gate = ApprovalGate(lambda request: (self.requests.append(request), True)[1])
        self.tool = TranslateTool(backend=self.backend, approval_gate=gate)

    def backend(self, texts, source, target):
        self.asked.append((list(texts), source, target))
        return echo(texts, source, target)

    def test_a_translation_says_both_languages_and_asks_as_a_network_read(self):
        result = self.tool.translate("good morning", "spanish")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["translation"], "[en>es-US] good morning")
        self.assertTrue(result.message.startswith("English → Spanish:"))
        self.assertEqual([request.risk for request in self.requests], [RiskLevel.MEDIUM])

    def test_the_source_comes_from_the_request_then_the_script_then_english(self):
        self.tool.translate("bonjour", "english", "french")
        self.tool.translate("ありがとう", "english")
        self.tool.translate("thank you", "korean")
        self.assertEqual([(source, target) for _, source, target in self.asked],
                         [("fr", "en"), ("ja", "en"), ("en", "ko")])

    def test_latin_text_into_english_asks_the_model_which_language_it_is(self):
        tool = TranslateTool(backend=self.backend, detect=lambda text: "French.")
        tool.translate("bonjour tout le monde", "english")
        self.assertEqual(self.asked[0][1:], ("fr", "en"))

    def test_with_nothing_to_tell_the_language_it_asks_instead_of_guessing(self):
        for tool in (TranslateTool(backend=self.backend),
                     TranslateTool(backend=self.backend, detect=lambda text: None),
                     TranslateTool(backend=self.backend, detect=lambda text: "Klingon")):
            result = tool.translate("bonjour tout le monde", "english")
            self.assertFalse(result.ok)
            self.assertIn("from French to English", result.message)
        self.assertEqual(self.asked, [])

    def test_a_detector_that_fails_is_recorded_and_treated_as_unknown(self):
        def broken(text):
            raise TimeoutError("busy")

        with patch.object(translate, "record_failure") as record:
            result = TranslateTool(backend=self.backend, detect=broken).translate("bonjour", "english")
        self.assertFalse(result.ok)
        record.assert_called_once()

    def test_text_already_in_the_target_language_is_not_sent(self):
        result = self.tool.translate("ありがとう", "japanese")
        self.assertTrue(result.ok)
        self.assertIn("already Japanese", result.message)
        self.assertEqual(self.asked, [])

    def test_a_language_the_model_does_not_know_is_named_with_the_ones_it_does(self):
        for args in (("hello", "telugu"), ("hello", "klingon"), ("hello", "spanish", "tamil"), ("నమస్కారం", "english")):
            with self.subTest(args):
                result = self.tool.translate(*args)
                self.assertFalse(result.ok)
                self.assertIn("It knows:", result.message)
                self.assertIn("Japanese", result.message)
        self.assertIn("Telugu", self.tool.translate("నమస్కారం", "english").message)
        self.assertEqual(self.asked, [])

    def test_long_text_goes_in_pieces_and_comes_back_in_its_lines(self):
        sentence = "This sentence is about forty characters. "
        text = "Heading\n\n" + sentence * 20 + "\nLast line."
        result = self.tool.translate(text, "french")
        pieces = self.asked[0][0]
        self.assertTrue(all(len(piece) <= 400 for piece in pieces))
        self.assertGreater(len(pieces), 3)
        lines = result.data["translation"].split("\n")
        self.assertEqual(lines[0], "[en>fr] Heading")
        self.assertEqual(lines[1], "")
        self.assertTrue(lines[-1].endswith("Last line."))
        self.assertEqual(len(lines), 4)

    def test_a_long_unpunctuated_sentence_is_bounded_without_changing_its_words(self):
        text = " ".join(["translation"] * 95)
        pieces_seen = []

        def bounded_backend(texts, source, target):
            pieces_seen.extend(texts)
            if any(len(piece) > 400 for piece in texts):
                raise ValueError("piece too long")
            return texts

        result = TranslateTool(backend=bounded_backend).translate(text, "french")
        self.assertTrue(result.ok, result.message)
        self.assertGreater(len(pieces_seen), 1)
        self.assertTrue(all(piece.startswith("translation") and piece.endswith("translation")
                            for piece in pieces_seen))
        self.assertEqual(result.data["translation"], text)

    def test_an_unbroken_token_is_bounded_without_inserting_spaces(self):
        text = "a" * 1000
        pieces_seen = []

        def bounded_backend(texts, source, target):
            pieces_seen.extend(texts)
            if any(len(piece) > 400 for piece in texts):
                raise ValueError("piece too long")
            return texts

        result = TranslateTool(backend=bounded_backend).translate(text, "french")
        self.assertTrue(result.ok, result.message)
        self.assertGreater(len(pieces_seen), 1)
        self.assertEqual(result.data["translation"], text)

    def test_too_long_is_refused_before_anything_is_sent(self):
        self.assertFalse(self.tool.translate("a" * 5001, "french").ok)
        self.assertEqual(self.asked, [])

    def test_a_backend_failure_is_recorded_and_reported(self):
        def dead(texts, source, target):
            raise TimeoutError("The translation service did not answer in time.")

        with patch.object(translate, "record_failure") as record:
            result = TranslateTool(backend=dead).translate("hello", "french")
        self.assertFalse(result.ok)
        self.assertIn("did not answer in time", result.message)
        record.assert_called_once()

    def test_a_service_failure_is_told_plainly_not_as_a_grpc_dump(self):
        class Rendezvous(Exception):
            def code(self):
                return SimpleNamespace(name="UNAVAILABLE")

            def __str__(self):
                return "<_MultiThreadedRendezvous of RPC that terminated with:\n\tstatus = ...>"

        def dead(texts, source, target):
            raise Rendezvous()

        with patch.object(translate, "record_failure") as record:
            result = TranslateTool(backend=dead).translate("hello", "french")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "NVIDIA's translation service is unreachable or overloaded right now "
                                         "(UNAVAILABLE); try again in a minute.")
        record.assert_called_once()

    def test_a_short_answer_is_not_passed_off_as_the_translation(self):
        with patch.object(translate, "record_failure") as record:
            result = TranslateTool(backend=lambda texts, s, t: []).translate("hello", "french")
        self.assertFalse(result.ok)
        record.assert_called_once()

    def test_without_the_client_it_says_what_to_install(self):
        def missing(texts, source, target):
            raise ImportError("No module named 'riva'")

        result = TranslateTool(backend=missing).translate("hello", "french")
        self.assertFalse(result.ok)
        self.assertIn("nvidia-riva-client", result.message)

    def test_every_code_is_one_the_model_reported(self):
        reported = {"ar", "bg", "cs", "da", "de", "el", "en", "es-ES", "es-US", "et", "fi", "fr", "hi", "hr",
                    "hu", "id", "it", "ja", "ko", "lt", "lv", "nl", "no", "pl", "pt-BR", "pt-PT", "ro", "ru",
                    "sk", "sl", "sv", "th", "tr", "uk", "vi", "zh-CN", "zh-TW"}
        self.assertEqual(set(LANGUAGES.values()), reported)


class WaitExpired(Exception):
    pass


class RivaBackendTests(unittest.TestCase):
    def setUp(self):
        self.channel = Mock()
        self.auth_args = []
        self.calls = []
        self.pending = SimpleNamespace(
            result=lambda timeout=None: (self.calls.append(("wait", timeout)), SimpleNamespace(
                translations=[SimpleNamespace(text="hola")]))[1],
            cancel=lambda: self.calls.append("cancel"))
        client = types.ModuleType("riva.client")

        def auth(**kwargs):
            self.auth_args.append(kwargs)
            return SimpleNamespace(channel=self.channel)

        def translate_call(texts, model, source, target, future=False):
            self.calls.append(("translate", list(texts), source, target, future))
            if not future:
                raise AssertionError("The blocking SDK call has no deadline")
            return self.pending

        client.Auth = auth
        client.NeuralMachineTranslationClient = lambda auth: SimpleNamespace(translate=translate_call)
        parent = types.ModuleType("riva")
        parent.client = client
        grpc = types.ModuleType("grpc")
        grpc.FutureTimeoutError = WaitExpired
        environ = {key: value for key, value in os.environ.items() if not key.startswith("RIVA_")}
        environ["RIVA_API_KEY"] = "fixture-not-a-key"
        for fixture in (patch.dict(sys.modules, {"riva": parent, "riva.client": client, "grpc": grpc}),
                        patch.dict(os.environ, environ, clear=True)):
            fixture.start()
            self.addCleanup(fixture.stop)

    def test_it_asks_the_hosted_model_with_a_deadline_and_closes_the_channel(self):
        self.assertEqual(translate._riva_translate(["hello"], "en", "es-US"), ["hola"])
        self.assertEqual(self.calls[0], ("translate", ["hello"], "en", "es-US", True))
        self.assertEqual(self.calls[1][0], "wait")
        self.assertGreater(self.calls[1][1], 0)
        self.assertIn("cancel", self.calls)
        metadata = dict(self.auth_args[0]["metadata_args"])
        self.assertEqual(metadata["function-id"], translate.RIVA_NMT_FUNCTION_ID)
        self.channel.close.assert_called_once()

    def test_a_stalled_call_times_out_and_still_closes_its_channel(self):
        def stall(timeout=None):
            raise WaitExpired()

        self.pending.result = stall
        with self.assertRaises(TimeoutError):
            translate._riva_translate(["hello"], "en", "es-US")
        self.channel.close.assert_called_once()

    def test_a_retired_model_id_is_looked_up_and_the_call_retried(self):
        class NotFound(Exception):
            def code(self):
                return SimpleNamespace(name="NOT_FOUND")

        retired = translate.RIVA_NMT_FUNCTION_ID
        answer = self.pending.result

        def result(timeout=None):
            if dict(self.auth_args[-1]["metadata_args"])["function-id"] == retired:
                raise NotFound()
            return answer(timeout)

        self.pending.result = result
        listing = [{"name": translate.RIVA_NMT_NAME, "id": "current-id", "status": "ACTIVE", "createdAt": "2026-10-06"}]
        nvcf._reset()
        self.addCleanup(nvcf._reset)
        with patch.object(nvcf, "_list_functions", lambda key: listing):
            self.assertEqual(translate._riva_translate(["hello"], "en", "fr"), ["hola"])
        used = [dict(call["metadata_args"])["function-id"] for call in self.auth_args]
        self.assertEqual(used, [retired, "current-id"])
        self.assertEqual(self.channel.close.call_count, 2)

    def test_the_model_can_be_chosen(self):
        os.environ["RIVA_NMT_FUNCTION_ID"] = "other-function"
        translate._riva_translate(["hello"], "en", "fr")
        self.assertEqual(dict(self.auth_args[0]["metadata_args"])["function-id"], "other-function")


class RoutingTests(unittest.TestCase):
    ROUTES = {
        "translate good morning to spanish": "translate good morning to spanish",
        'can you translate "where is the station" into japanese?': 'translate "where is the station" into japanese',
        "translate bonjour from french to english": "translate bonjour from french to english",
        "how do you say thank you in korean": "translate thank you to korean",
        "how do i say good night in brazilian portuguese": "translate good night to brazilian portuguese",
        "what's the word for library in german": "translate library to german",
        "what is love in italian": "translate love to italian",
        "translate hello to telugu": "translate hello to telugu",
        # After a colon the question mark is the text's own, so it is kept.
        "translate to spanish: where is the station?": "translate to spanish: where is the station?",
        "how to say i love you in korean": "translate i love you to korean",
        "can you say good morning in italian please": "translate good morning to italian",
        "in japanese how would i say nice to meet you": "translate nice to meet you to japanese",
        "what is the spanish phrase for good luck": "translate good luck to spanish",
    }
    STAY = (
        "what's the best way to say sorry in japanese",  # advice, not the phrase translated
        "translate this into a plan of action",
        "what is the population in china",
        "what is a noun in german grammar",
        "do not translate this to french",
        "how do you say no to your boss",
        "say something nice in spanish",
        "say a few words in french",
        "say it again in english",
        "what's the english word for when you're sad",
        "what is the french word for the feeling of being homesick",
        "what's the spanish word for a man who sells fish",
        "in french class we learned how to say hello",
    )

    def test_requests_route_instantly(self):
        planner = HeuristicPlannerProvider()
        for text, command in self.ROUTES.items():
            with self.subTest(text):
                self.assertEqual(planner.plan(text, "", {}).command, command)

    def test_near_misses_are_left_alone(self):
        planner = HeuristicPlannerProvider()
        for text in self.STAY:
            with self.subTest(text):
                command = planner.plan(text, "", {}).command or ""
                self.assertFalse(command.startswith("translate"), command)


class OrchestratorTranslationTests(unittest.TestCase):
    # Through the module: a class imported by name is collected and run again here.
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.orch = self.build(Path(temp.name))
        self.asked = []

        def backend(texts, source, target):
            self.asked.append((list(texts), source, target))
            return echo(texts, source, target)

        self.orch._translate_tool_cache = TranslateTool(backend=backend)

    def say(self, text, history=None):
        return asyncio.run(self.orch.handle(text, history=history or []))

    def test_that_means_the_reply_above_without_its_tool_data(self):
        history = [{"role": "user", "text": "give me a greeting"},
                   {"role": "assistant", "text": "Good evening, everyone.\n[tool result data, context only - "
                                                 "not a format to imitate] {\"x\": 1}"}]
        result = self.say("translate that to spanish", history)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.asked[0][0], ["Good evening, everyone."])

    def test_saying_that_in_a_language_means_the_reply_above(self):
        # Asked aloud the follow-up is "can you say that in german", which went to the chat model.
        history = [{"role": "user", "text": "when is the meeting"},
                   {"role": "assistant", "text": "The meeting moved to Friday at 3pm."}]
        for asked in ("can you say that in german", "say it in german"):
            with self.subTest(asked):
                self.asked.clear()
                result = self.say(asked, history)
                self.assertTrue(result.ok, result.message)
                self.assertEqual(self.asked[0][0], ["The meeting moved to Friday at 3pm."])
                self.assertEqual(self.asked[0][2], "de")

    def test_that_with_nothing_above_says_so(self):
        result = self.say("translate that to spanish")
        self.assertFalse(result.ok)
        self.assertIn("nothing earlier", result.message)
        self.assertEqual(self.asked, [])

    def test_prose_that_starts_with_translate_is_not_run_as_a_command(self):
        result = self.say("translate this into a plan of action")
        self.assertEqual(self.asked, [])
        self.assertNotIn("Say it like", result.message, "answered with command syntax")

    def test_a_personal_account_may_translate(self):
        with acting_as(Principal("p1", "family", "personal")):
            result = self.say("translate good morning to french")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.asked[0][1:], ("en", "fr"))

    def test_the_fast_tier_names_the_language(self):
        seen = []

        def answer(request, context, max_tokens=None):
            seen.append(max_tokens)
            return "French.\n"

        self.orch.planner = SimpleNamespace(provider=SimpleNamespace(answer=answer))
        self.assertEqual(self.orch._detect_language("bonjour"), "French")
        self.assertTrue(seen and seen[0] <= 16)


if __name__ == "__main__":
    unittest.main()

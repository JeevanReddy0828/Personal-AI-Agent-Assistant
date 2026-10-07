from __future__ import annotations

import os
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

from laptop_agent import nvcf

NAME = "ai-riva-translate-1_6b"
PINNED = "pinned-id"
VARIABLE = "RIVA_NMT_FUNCTION_ID"


class RpcError(Exception):
    """Shaped like grpc.RpcError: the status is a method returning an enum with a name."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status

    def code(self):
        return SimpleNamespace(name=self.status)


def listing(*rows):
    return [{"name": name, "id": fid, "status": status, "createdAt": created}
            for name, fid, status, created in rows]


class RediscoveryTests(unittest.TestCase):
    def setUp(self):
        nvcf._reset()
        self.addCleanup(nvcf._reset)
        self.lists = 0
        self.functions = listing((NAME, "fresh-id", "ACTIVE", "2026-10-06"))
        self.attempts = []
        self.answers = {}
        self.failed = []
        environ = {key: value for key, value in os.environ.items() if key != VARIABLE}
        for fixture in (patch.dict(os.environ, environ, clear=True),
                        patch.object(nvcf, "_list_functions", self.list_functions),
                        patch.object(nvcf, "record_failure", lambda source, exc: self.failed.append(source))):
            fixture.start()
            self.addCleanup(fixture.stop)

    def list_functions(self, key):
        self.lists += 1
        if isinstance(self.functions, Exception):
            raise self.functions
        return self.functions

    def attempt(self, function_id):
        self.attempts.append(function_id)
        answer = self.answers.get(function_id, RpcError("NOT_FOUND"))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def call(self):
        return nvcf.call(NAME, PINNED, VARIABLE, "key", self.attempt)

    def test_a_working_id_is_used_without_any_lookup(self):
        self.answers[PINNED] = "hola"
        self.assertEqual(self.call(), "hola")
        self.assertEqual((self.attempts, self.lists), ([PINNED], 0))

    def test_a_retired_id_is_looked_up_once_and_the_new_one_kept(self):
        self.answers["fresh-id"] = "hola"
        self.assertEqual(self.call(), "hola")
        self.assertEqual(self.call(), "hola")
        self.assertEqual(self.attempts, [PINNED, "fresh-id", "fresh-id"])
        self.assertEqual(self.lists, 1)

    def test_an_outage_is_not_a_reason_to_look_anything_up(self):
        for status in ("UNAVAILABLE", "DEADLINE_EXCEEDED", "PERMISSION_DENIED", "INTERNAL"):
            with self.subTest(status):
                self.answers[PINNED] = RpcError(status)
                with self.assertRaises(RpcError):
                    self.call()
        self.assertEqual(self.lists, 0)
        with self.assertRaises(ValueError):
            nvcf.call(NAME, PINNED, VARIABLE, "key", lambda fid: (_ for _ in ()).throw(ValueError("no code")))

    def test_an_id_the_user_set_is_never_replaced(self):
        os.environ[VARIABLE] = "users-id"
        with self.assertRaises(RpcError):
            self.call()
        self.assertEqual((self.attempts, self.lists), (["users-id"], 0))

    def test_a_key_that_cannot_list_keeps_the_original_failure_and_says_what_to_set(self):
        self.functions = urllib.error.HTTPError(nvcf.LIST_URL, 403, "Forbidden", {}, None)
        with self.assertRaises(nvcf.StaleFunctionError) as raised:
            self.call()
        self.assertIn(VARIABLE, str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, RpcError)
        self.assertEqual(self.failed, ["nvcf/lookup"])
        self.assertEqual(self.attempts, [PINNED])

    def test_a_failed_lookup_is_not_repeated_on_every_call(self):
        self.functions = urllib.error.HTTPError(nvcf.LIST_URL, 403, "Forbidden", {}, None)
        for _ in range(3):
            with self.assertRaises(nvcf.StaleFunctionError):
                self.call()
        self.assertEqual(self.lists, 1)
        with patch.object(nvcf.time, "monotonic", lambda: 10 ** 9):
            with self.assertRaises(nvcf.StaleFunctionError):
                self.call()
        self.assertEqual(self.lists, 2, "a later lookup is allowed once the wait has passed")

    def test_only_an_active_function_with_the_exact_name_counts(self):
        self.functions = listing((NAME, "old-id", "INACTIVE", "2026-10-07"),
                                 (NAME + "-v2", "other-id", "ACTIVE", "2026-10-07"),
                                 (NAME, "older-id", "ACTIVE", "2026-01-01"),
                                 (NAME, "newest-id", "ACTIVE", "2026-10-05"))
        self.answers["newest-id"] = "hola"
        self.assertEqual(self.call(), "hola")
        self.assertEqual(self.attempts, [PINNED, "newest-id"])

    def test_no_current_id_or_the_same_one_is_reported_not_retried(self):
        for functions in (listing(), listing((NAME, PINNED, "ACTIVE", "2026-10-06"))):
            with self.subTest(functions):
                nvcf._reset()
                self.attempts.clear()
                self.functions = functions
                with self.assertRaises(nvcf.StaleFunctionError):
                    self.call()
                self.assertEqual(self.attempts, [PINNED])

    def test_the_retry_happens_once(self):
        with self.assertRaises(RpcError):
            self.call()
        self.assertEqual(self.attempts, [PINNED, "fresh-id"])
        self.assertEqual(self.lists, 1)


class DescribeTests(unittest.TestCase):
    """What the user is told. A gRPC error's text is a multi-line dump, and NOT_FOUND's carries
    the NVIDIA account id."""

    def test_each_failure_reads_as_a_sentence_with_what_to_do(self):
        cases = {"UNAVAILABLE": "try again in a minute", "DEADLINE_EXCEEDED": "try again in a minute",
                 "PERMISSION_DENIED": "OPENAI_API_KEY", "NOT_FOUND": "RIVA_*_FUNCTION_ID",
                 "INTERNAL": "failures"}
        for code, advice in cases.items():
            with self.subTest(code):
                # Shaped like the real dump, which carries the details - and the account id.
                error = type("Rendezvous", (RpcError,), {"__str__": lambda self: (
                    f"<_MultiThreadedRendezvous of RPC that terminated with:\n\tstatus = StatusCode.{code}"
                    "\n\tdetails = \"Function 'x': Not found for account 'secret-account'\">")})(code)
                text = nvcf.describe(error, "NVIDIA's translation service")
                self.assertTrue(text.startswith("NVIDIA's translation service "))
                self.assertIn(f"({code})", text)
                self.assertIn(advice, text)
                self.assertNotIn("secret-account", text)
                self.assertNotIn("\n", text)

    def test_anything_that_is_not_a_grpc_error_is_left_to_the_caller(self):
        for error in (TimeoutError("did not answer"), ValueError("x"), nvcf.StaleFunctionError("set it")):
            with self.subTest(type(error).__name__):
                self.assertIsNone(nvcf.describe(error, "NVIDIA's translation service"))


if __name__ == "__main__":
    unittest.main()

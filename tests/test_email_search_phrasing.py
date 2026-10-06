"""Targeted mailbox requests must search the named sender and topic."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.email import EmailTool
from test_everyday_requests import Everyday


class _Mailbox:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.provider_queries: list[tuple[str, str]] = []

    def search_inbox(self, query: str = "UNSEEN", limit: int = 10) -> ToolResult:
        self.queries.append(query)
        messages = ([{"from": "Alex", "subject": "Budget", "snippet": "The numbers are ready."}]
                    if query.startswith('from:"Alex"') else [])
        return ToolResult.success(f"Search result for {query}", messages=messages)

    def search_oauth_mail(self, provider: str, query: str, limit: int = 10) -> ToolResult:
        self.provider_queries.append((provider, query))
        return self.search_inbox(query, limit)


class EmailSearchPhrasingTests(unittest.TestCase):
    def test_time_spans_are_inbox_reads_not_senders(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            mailbox = _Mailbox()
            object.__setattr__(everyday.orchestrator.context, "email", mailbox)
            for said in ("show me emails from the last 3 days",
                         "show me emails from yesterday",
                         "show me emails from this week",
                         "show me emails from today"):
                with self.subTest(said=said):
                    result, ran = everyday.say(said, stream=False)
                    self.assertEqual(ran, "email digest")
                    self.assertTrue(result.ok, result.message)
                    self.assertEqual(mailbox.queries, [])

    def test_mentioning_new_email_does_not_read_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            mailbox = _Mailbox()
            object.__setattr__(everyday.orchestrator.context, "email", mailbox)
            for said in ("is there a new email standard for spam",
                         "i got a new email address remember it"):
                with self.subTest(said=said):
                    result, ran = everyday.say(said, stream=False)
                    self.assertNotEqual(ran, "email unread")
                    self.assertTrue(result.ok, result.message)
                    self.assertEqual(mailbox.queries, [])

    def test_search_my_email_for_topic_reaches_mailbox(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            mailbox = _Mailbox()
            object.__setattr__(everyday.orchestrator.context, "email", mailbox)
            for said in ("search my email for the budget",
                         "um search my email for the budget",
                         "search my email for the budget and then tell me what you find"):
                with self.subTest(said=said):
                    result, ran = everyday.say(said, stream=False)
                    self.assertEqual(ran, "email search the budget")
                    self.assertTrue(result.ok, result.message)
                    self.assertEqual(mailbox.queries[-1], "the budget")

    def test_named_sender_and_topic_search_reaches_mailbox_not_digest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            mailbox = _Mailbox()
            object.__setattr__(everyday.orchestrator.context, "email", mailbox)
            for said in ("find the email from Alex about the budget",
                         "um find emails from Alex about the budget"):
                with self.subTest(said=said):
                    result, ran = everyday.say(said, stream=False)
                    self.assertEqual(ran, 'email search from:"Alex" budget')
                    self.assertTrue(result.ok, result.message)
                    self.assertIn("Alex", result.message)
                    self.assertIn("Budget", result.message)
                    self.assertEqual(mailbox.queries[-1], 'from:"Alex" budget')

    def test_sender_only_and_general_inbox_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            mailbox = _Mailbox()
            object.__setattr__(everyday.orchestrator.context, "email", mailbox)
            found, ran = everyday.say("find emails from Alex", stream=False)
            self.assertEqual(ran, 'email search from:"Alex"')
            self.assertIn("Budget", found.message)
            digest, ran = everyday.say("show me my recent emails", stream=False)
            self.assertEqual(ran, "email digest")
            self.assertIn("caught up", digest.message)

    def test_imap_combines_sender_and_topic_without_losing_either(self) -> None:
        self.assertEqual(EmailTool._imap_criteria('from:"Alex" budget'),
                         ["FROM", '"Alex"', "TEXT", '"budget"'])
        self.assertEqual(EmailTool._imap_criteria('from:"Alex Smith"'),
                         ["FROM", '"Alex Smith"'])
        self.assertEqual(EmailTool._imap_criteria("invoice"), ["TEXT", '"invoice"'])

    def test_provider_suffix_is_not_part_of_the_sender_or_topic(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            mailbox = _Mailbox()
            object.__setattr__(everyday.orchestrator.context, "email", mailbox)
            for said, query in (("find the email from Alex about the budget in Gmail",
                                 'from:"Alex" budget'),
                                ("find emails from Alex in Outlook", 'from:"Alex"')):
                with self.subTest(said=said):
                    result, ran = everyday.say(said, stream=False)
                    self.assertEqual(ran, f"email api search {'gmail' if 'Gmail' in said else 'outlook'} {query}")
                    self.assertIn("Budget", result.message)
                    self.assertEqual(mailbox.provider_queries[-1][1], query)

    def test_gmail_address_is_a_sender_not_a_provider_choice(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            mailbox = _Mailbox()
            object.__setattr__(everyday.orchestrator.context, "email", mailbox)
            result, ran = everyday.say("find email from alex@gmail.com about budget", stream=False)
            self.assertEqual(ran, 'email search from:"alex@gmail.com" budget')
            self.assertEqual(mailbox.queries[-1], 'from:"alex@gmail.com" budget')
            self.assertEqual(mailbox.provider_queries, [])
            self.assertTrue(result.ok, result.message)


if __name__ == "__main__":
    unittest.main()

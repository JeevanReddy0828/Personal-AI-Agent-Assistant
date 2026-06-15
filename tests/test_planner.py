from __future__ import annotations

import unittest

from laptop_agent.planner import HeuristicPlannerProvider


class HeuristicPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = HeuristicPlannerProvider()

    def plan(self, text: str):
        return self.provider.plan(text, "help text", {})

    def test_routes_open_url(self) -> None:
        decision = self.plan("open website example.com")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "open url example.com")

    def test_routes_job_application_to_plan_only(self) -> None:
        decision = self.plan("apply to the job at https://example.com/jobs/1")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "plan apply job https://example.com/jobs/1")

    def test_routes_form_inspection(self) -> None:
        decision = self.plan("inspect forms at example.com/apply")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "inspect forms example.com/apply")

    def test_routes_fill_preview(self) -> None:
        decision = self.plan("preview form fill for example.com/apply")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "preview form fill example.com/apply")

    def test_routes_fill_form(self) -> None:
        decision = self.plan("fill the form at example.com/apply")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "fill form example.com/apply")

    def test_routes_unread_email(self) -> None:
        decision = self.plan("show unread emails")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email unread")

    def test_routes_email_search(self) -> None:
        decision = self.plan("find emails about invoice")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email search invoice")

    def test_routes_email_token_status(self) -> None:
        decision = self.plan("show email token status")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email tokens status")

    def test_routes_email_token_refresh(self) -> None:
        decision = self.plan("refresh email oauth token for gmail")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email oauth refresh gmail")

    def test_routes_oauth_email_draft(self) -> None:
        decision = self.plan("draft email using gmail to ada@example.com about hello")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email api draft gmail to ada@example.com subject hello body Draft email about: hello")

    def test_routes_oauth_email_send(self) -> None:
        decision = self.plan("send email using outlook to ada@example.com about hello")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email api send outlook to ada@example.com subject hello body Draft email about: hello")

    def test_routes_gmail_api_search(self) -> None:
        decision = self.plan("find emails about invoice in gmail")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email api search gmail invoice")

    def test_routes_outlook_unread(self) -> None:
        decision = self.plan("show unread emails in outlook")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email api unread outlook")

    def test_routes_summarize_file(self) -> None:
        decision = self.plan("summarize the file notes.md")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "summarize file notes.md")

    def test_routes_organize_folder(self) -> None:
        decision = self.plan("organize the folder C:/Users/me/Downloads")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "organize folder C:/Users/me/Downloads")

    def test_routes_organize_folder_apply(self) -> None:
        decision = self.plan("tidy the folder C:/Users/me/Downloads and apply")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "organize folder C:/Users/me/Downloads apply")

    def test_routes_transcribe(self) -> None:
        decision = self.plan("transcribe the audio meeting.mp3")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "transcribe meeting.mp3")

    def test_routes_ocr(self) -> None:
        decision = self.plan("extract text from the image receipt.jpg")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "ocr image receipt.jpg")

    def test_routes_summarize_media_file(self) -> None:
        decision = self.plan("summarize the recording standup.mp3")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "summarize file standup.mp3")

    def test_routes_read_screen(self) -> None:
        decision = self.plan("what's on my screen")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "read screen")

    def test_routes_task_dashboard(self) -> None:
        decision = self.plan("show tasks")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "tasks")

    def test_routes_index_file(self) -> None:
        decision = self.plan("index the file contract.pdf")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "index file contract.pdf")

    def test_routes_recall(self) -> None:
        decision = self.plan("what do I know about kubernetes")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "recall kubernetes")

    def test_routes_knowledge_stats(self) -> None:
        decision = self.plan("show knowledge stats")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "knowledge stats")

    def test_routes_knowledge_export(self) -> None:
        decision = self.plan("export knowledge to reports/knowledge.md")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "knowledge export reports/knowledge.md")

    def test_routes_search_notes(self) -> None:
        decision = self.plan("search my notes for invoices")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "recall invoices")

    def test_routes_research(self) -> None:
        decision = self.plan("research the history of jazz")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "research the history of jazz")

    def test_routes_look_into_as_research(self) -> None:
        decision = self.plan("look into rust async runtimes")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "research rust async runtimes")

    def test_routes_research_report(self) -> None:
        decision = self.plan("create a research report on local-first agents")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "research report local-first agents")

    def test_routes_save_research_report(self) -> None:
        decision = self.plan("save research report on local-first agents to obsidian")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "save research report local-first agents to obsidian")

    def test_routes_web_search(self) -> None:
        decision = self.plan("search the web for best mechanical keyboards")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "web search best mechanical keyboards")

    def test_routes_google_query(self) -> None:
        decision = self.plan("google python walrus operator")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "web search python walrus operator")

    def test_read_file_still_routes_to_read(self) -> None:
        decision = self.plan("read file report.txt")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "read file report.txt")

    def test_unknown_request_returns_chat(self) -> None:
        decision = self.plan("invent something vague")
        self.assertTrue(decision.is_chat)
        self.assertLess(decision.confidence, 0.5)


if __name__ == "__main__":
    unittest.main()

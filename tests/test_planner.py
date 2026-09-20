from __future__ import annotations

import unittest

from laptop_agent.planner import HeuristicPlannerProvider
from laptop_agent.planner.heuristic import is_diagram_subject, is_plain_question


class HeuristicPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = HeuristicPlannerProvider()

    def plan(self, text: str):
        return self.provider.plan(text, "help text", {})

    def test_routes_process_file(self) -> None:
        decision = self.plan("process file C:/reports/q3.pdf")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "process file C:/reports/q3.pdf")

    def test_routes_window_arrangement_however_it_is_asked(self) -> None:
        """All three of these were reported failing. The first reached the window tool but
        parsed the politeness into the app name; the other two never routed at all and the
        chat tier answered them by asking for approval and then claiming it had acted."""
        for text in ("Hey Jarvis, could you put my WhatsApp on left and chrome on right?",
                     "can you split whatsapp left and chrome right please",
                     "And Chrome on right using split windows function"):
            decision = self.plan(text)
            self.assertTrue(decision.is_command, text)
            self.assertTrue((decision.command or "").startswith("window "), f"{text} -> {decision.command}")

    def test_arrangement_routing_does_not_grab_unrelated_sentences(self) -> None:
        for text in ("move the report to the archive folder",
                     "split the bill between four people",
                     "what is on the left of the diagram"):
            decision = self.plan(text)
            self.assertFalse((decision.command or "").startswith("window "), f"{text} -> {decision.command}")

    def test_placements_route_without_any_verb(self) -> None:
        """How it gets said when nobody is being careful: name, position, name, position,
        with no "put" or "move" anywhere. The tool has always parsed these correctly — the
        router simply never sent them."""
        for text in ("i want whatsapp on the left and chrome on the right",
                     "whatsapp on the left and chrome on the right",
                     "whatsapp to the left and chrome to the right",
                     "can you put whatsapp on the left and chrome on the right",
                     "notepad on the top left and spotify on the bottom right",
                     "chrome on the left half and slack on the right half"):
            with self.subTest(text=text):
                self.assertTrue((self.plan(text).command or "").startswith("window "), text)

    def test_the_position_vocabulary_comes_from_the_tool(self) -> None:
        """It used to be hand-copied into the planner and had already drifted: no corners,
        no thirds, no halves. So "notepad on the top left" could not route even though
        `parse_placements` reads it perfectly. Derived now, so the two cannot disagree."""
        from laptop_agent.planner.heuristic import _POSITION_WORD
        from laptop_agent.tools.windows import LAYOUTS, _ALIASES

        for phrase in ("top left", "bottom right", "left third", "right half"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.replace(" ", r"\s+"), _POSITION_WORD, phrase)
        self.assertEqual(
            len(set(LAYOUTS) | set(_ALIASES)), _POSITION_WORD.count("|") + 1,
            "the planner's position list no longer covers the tool's vocabulary",
        )

    def test_a_bare_placement_sentence_is_not_a_window_request(self) -> None:
        """The reason this route was left undone. Every one of these is a clean
        `<something> on the <position>`, and none is a request to move a window."""
        for text in ("i want to go left at the next junction",
                     "what is on the left side of the brain",
                     "the chrome finish on the right handle is worn",
                     "should i put the legend on the right",
                     "turn left and then right",
                     "my keys are on the right",
                     "the report on the left is wrong",
                     "the left engine and the right engine",
                     "is the logo on the left or on the right",
                     "what is on the left and what is on the right",
                     # "in the middle" is ordinary English, and these fullmatch every
                     # structural rule above. A name ending in a copula is a sentence
                     # about something, not the name of a window.
                     "the value is in the middle and the key is on the left",
                     "the bug is in the middle and the fix is on the right",
                     "the labels are on the left and the values are on the right",
                     "the header was on the top and the footer was on the bottom"):
            with self.subTest(text=text):
                self.assertFalse((self.plan(text).command or "").startswith("window "), text)

    def test_in_the_top_left_is_still_a_placement(self) -> None:
        """The copula guard must not cost the `in` preposition itself — people say
        "spotify in the top left" as readily as "on the top left"."""
        for text in ("spotify in the top left and notepad in the bottom right",
                     "chrome in the left half and slack in the right half"):
            with self.subTest(text=text):
                self.assertTrue((self.plan(text).command or "").startswith("window "), text)

    def test_no_sentence_in_this_repos_own_prose_routes_to_the_window_tool(self) -> None:
        """A corpus, not a hand-picked list. The risk of matching name-then-position is
        that it starts grabbing ordinary sentences, and the only honest way to know is to
        run it over real ones."""
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        blobs = [(root / name).read_text(encoding="utf-8")
                 for name in ("CLAUDE.md", "README.md", "ERRORS.md") if (root / name).exists()]
        sentences = []
        for blob in blobs:
            sentences += [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", blob)
                          if 10 < len(s.strip()) < 200]
        self.assertGreater(len(sentences), 500, "corpus did not load")
        grabbed = [s for s in sentences
                   if (self.plan(s).command or "").startswith("window ")]
        self.assertEqual(grabbed, [], f"{len(grabbed)} ordinary sentences routed to the window tool")

    def test_routes_a_slide_deck_request(self) -> None:
        """The document route needed a trailing format ("... as a pdf"), which a deck
        request never has — so "create a ppt for sun and planets" fell through to the LLM
        router, which asked for a document and got a PDF."""
        decision = self.plan("can you create a ppt for sun and planets")
        self.assertTrue(decision.is_command)
        self.assertTrue(decision.command.startswith("document "), decision.command)
        self.assertIn("ppt", decision.command, "the format word has to survive for the tool to read it")

    def test_a_question_about_presentations_is_not_a_deck_request(self) -> None:
        decision = self.plan("what makes a good presentation")
        self.assertFalse(decision.is_command and (decision.command or "").startswith("document "))

    def test_routes_whats_in_file(self) -> None:
        decision = self.plan("what's in budget.csv")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "process file budget.csv")

    def test_routes_spreadsheet_analysis(self) -> None:
        decision = self.plan("analyze sales.csv")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "analyze spreadsheet sales.csv")

    def test_routes_stats_for_tsv(self) -> None:
        decision = self.plan("stats for data.tsv")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "analyze spreadsheet data.tsv")

    def test_local_recommendation_goes_to_web_search(self) -> None:
        decision = self.plan("find me Indian restaurants in kyle, Tx")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "web search Indian restaurants in kyle, Tx")

    def test_known_category_near_me_uses_geolocation(self) -> None:
        # A recognised place category routes to the IP-geolocated 'around' tool
        # (real nearby listings) rather than a generic web search.
        decision = self.plan("best coffee near me")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "around coffee")

    def test_unknown_thing_near_me_falls_back_to_web_search(self) -> None:
        decision = self.plan("best tattoo artist near me")
        self.assertTrue(decision.is_command)
        self.assertTrue(decision.command.startswith("web search"))

    def test_help_me_decide_routes_to_solve(self) -> None:
        decision = self.plan("help me decide between Postgres and MySQL for my app")
        self.assertTrue(decision.is_command)
        self.assertTrue(decision.command.startswith("solve "))

    def test_should_i_x_or_y_routes_to_solve(self) -> None:
        decision = self.plan("should I rewrite the auth layer now or later")
        self.assertTrue(decision.is_command)
        self.assertTrue(decision.command.startswith("solve "))

    def test_plain_question_does_not_route_to_solve(self) -> None:
        decision = self.plan("what is python")
        self.assertFalse(decision.is_command and decision.command.startswith("solve"))

    def test_multi_stop_trip_routes_to_trip(self) -> None:
        decision = self.plan("plan a road trip from Austin to Dallas to Houston")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "trip Austin | Dallas | Houston")

    def test_trip_preserves_city_state(self) -> None:
        decision = self.plan("plan a road trip from Austin, TX to Dallas, TX to Houston, TX")
        self.assertEqual(decision.command, "trip Austin, TX | Dallas, TX | Houston, TX")

    def test_trip_comma_only_list(self) -> None:
        decision = self.plan("plan a trip: Austin, Dallas, Houston")
        self.assertEqual(decision.command, "trip Austin | Dallas | Houston")

    def test_youtube_summary_routes_to_summarizer(self) -> None:
        decision = self.plan("summarize this youtube video https://youtu.be/dQw4w9WgXcQ")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "summarize youtube https://youtu.be/dQw4w9WgXcQ")

    def test_plain_youtube_link_without_summary_intent_is_not_summarized(self) -> None:
        # "play X on youtube" must NOT become a transcript summary.
        decision = self.plan("play lofi beats on youtube")
        self.assertTrue(decision.command.startswith("play music"))

    def test_distance_routes_to_travel(self) -> None:
        self.assertEqual(self.plan("driving distance from Austin to Dallas").command, "distance Austin to Dallas")
        self.assertEqual(self.plan("how far is Dallas from Austin").command, "distance Austin to Dallas")

    def test_hotels_routes_to_travel_not_websearch(self) -> None:
        self.assertEqual(self.plan("find hotels near Austin, TX").command, "hotels near Austin, TX")
        self.assertEqual(self.plan("where to stay in Kyoto").command, "hotels near Kyoto")

    def test_flights_route_to_web_search(self) -> None:
        self.assertEqual(
            self.plan("flights from Austin to Tokyo").command, "web search flights from Austin to Tokyo"
        )

    def test_weather_routes_to_weather_tool(self) -> None:
        decision = self.plan("what's the weather in Austin, TX")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "weather Austin, TX")

    def test_weather_strips_time_words(self) -> None:
        self.assertEqual(self.plan("weather forecast for Dallas tomorrow").command, "weather Dallas")

    def test_draw_request_routes_to_the_image_tool(self) -> None:
        decision = self.plan("draw me a picture of a red fox in snow")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "image a red fox in snow")

    def test_generate_an_image_phrasing_also_routes(self) -> None:
        self.assertEqual(self.plan("generate an image of a brass compass").command, "image a brass compass")

    def test_asking_to_see_reminders_routes_instantly_however_it_is_phrased(self) -> None:
        """This was an exact set of four strings. Everything else went to the LLM router —
        or, when the phrasing avoided the word "my", to no router at all."""
        for text in (
            "what are my reminders", "what are my reminders?", "what reminders do i have",
            "do i have any reminders", "any reminders", "show me my reminders",
            "list my reminders", "check my reminders", "tell me my reminders",
            "see my reminders", "whats on my reminder list", "reminders", "my reminders",
            # Politeness. `strip_address` removes greetings and the wake word but not
            # "can you", so without the prefix the commonest spoken form of all missed.
            "can you show me my reminders", "could you list my reminders",
            "would you show my reminders", "please show my reminders",
            "i want to see my reminders", "pull up my reminders",
            "give me my reminders", "read out my reminders", "read my reminders",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.plan(text).command, "reminders")

    def test_due_is_read_inside_the_question_not_anywhere_in_the_sentence(self) -> None:
        for text in ("what reminders are due", "which reminders are due",
                     "any reminders due", "reminders due", "due reminders",
                     "show due reminders"):
            with self.subTest(text=text):
                self.assertEqual(self.plan(text).command, "reminders due")
        # The trap: "due" appears, but this is a creation. Testing `due` against the whole
        # sentence would file it as a listing and silently drop the reminder.
        self.assertEqual(self.plan("remind me to pay the bill due friday").command,
                         "reminder add to pay the bill due friday")

    def test_the_listing_pattern_cannot_match_part_way_through_a_sentence(self) -> None:
        """The anchor is in the pattern, not in the caller's `.match()`. Left to the call
        site, a later `.search()` would turn this creation into a listing and drop it."""
        from laptop_agent.planner.heuristic import _REMINDER_ASK

        self.assertIsNone(_REMINDER_ASK.search("remind me to tell bob to check my reminders"))

    def test_creating_a_reminder_still_wins_over_listing_one(self) -> None:
        for text, expected in (
            ("remind me to call mom at 6pm", "reminder add to call mom at 6pm"),
            ("can you remind me to call mom at 6pm", "reminder add to call mom at 6pm"),
            ("set a reminder for the dentist tomorrow", "reminder add for the dentist tomorrow"),
            ("add a reminder to water plants", "reminder add to water plants"),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.plan(text).command, expected)

    def test_a_question_about_reminders_in_general_is_not_a_listing(self) -> None:
        # Requires the plural or an explicit "my reminder", so a definition stays chat.
        for text in ("what is a reminder", "how do reminders work in ios"):
            with self.subTest(text=text):
                self.assertIsNone(getattr(self.plan(text), "command", None))

    def test_a_plural_tool_word_is_still_a_tool_word(self) -> None:
        """`reminder\b` does not match "reminders". Only files/notes/jobs were listed in
        the plural, so these were classified as plain knowledge questions and answered by
        a model that cannot see any of them."""
        for text in (
            "do i have any reminders", "do i have any drafts", "do i have any documents",
            "do i have any tasks", "do i have any downloads", "do i have any screenshots",
            "do i have any workflows", "what reminders do i have",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_plain_question(text), text)

    def test_a_plain_question_skips_the_router(self) -> None:
        # These need no classification call: nothing in them names something to act on.
        for text in (
            "how does TCP congestion control work",
            "what is the difference between a process and a thread",
            "why do databases use write-ahead logging",
            "explain what a bloom filter is good for",
            "who was Ada Lovelace",
            "tell me about quantum computing",
        ):
            self.assertTrue(is_plain_question(text), text)

    def test_anything_actionable_still_goes_to_the_router(self) -> None:
        for text in (
            "what files are here",            # names the user's data
            "what is the weather in Austin",  # names a tool
            "what is in config.yaml",         # names a target
            "summarize the readme",           # an instruction, not a question
            "what do you remember about me",  # personal memory
            "draw me a picture of a fox",
            "email the team about the outage",
        ):
            self.assertFalse(is_plain_question(text), text)

    def test_decisions_stay_with_the_advisor(self) -> None:
        # The advisor researches and recommends; a fast chat reply would be worse.
        for text in (
            "should I use Postgres or MySQL",
            "should we migrate to Kubernetes",
            "what's the best way to learn Rust",
            "is it better to cache or recompute",
        ):
            self.assertFalse(is_plain_question(text), text)

    def test_an_empty_or_enormous_input_is_not_a_plain_question(self) -> None:
        self.assertFalse(is_plain_question(""))
        self.assertFalse(is_plain_question("   "))
        self.assertFalse(is_plain_question("what is " + "x" * 500))

    def test_news_questions_route_to_the_news_feed(self) -> None:
        # A generic web search for these returned cnn.com and foxnews.com with taglines.
        self.assertEqual(self.plan("what is the latest news today").command, "news")
        self.assertEqual(self.plan("give me the news").command, "news")
        self.assertEqual(self.plan("what are the top headlines").command, "news")

    def test_a_news_topic_is_carried_through(self) -> None:
        self.assertEqual(self.plan("any news on nvidia").command, "news nvidia")
        self.assertEqual(self.plan("headlines on nvidia").command, "news nvidia")
        self.assertEqual(self.plan("tell me the news on openai").command, "news openai")

    def test_a_named_file_is_not_a_news_request(self) -> None:
        decision = self.plan("read the news article file.txt")
        self.assertNotEqual(decision.command, "news")
        self.assertIn("file", decision.command)

    def test_technical_diagrams_are_recognised(self) -> None:
        for text in (
            "ERD diagram of the database schema",
            "a state diagram of TCP congestion control",
            "a flowchart of the login process",
            "an architecture diagram",
            "a wireframe of the settings page",
            "a UML sequence diagram",
        ):
            self.assertTrue(is_diagram_subject(text), text)

    def test_ordinary_pictures_are_not_diagrams(self) -> None:
        for text in (
            "a red fox asleep in fresh snow",
            "a brass compass on a weathered desk",
            "a futuristic city at dusk",
        ):
            self.assertFalse(is_diagram_subject(text), text)

    def test_draw_without_a_subject_stays_chat(self) -> None:
        # "draw the diagram" is about something already in the conversation, not a prompt.
        self.assertFalse(self.plan("draw the diagram").is_command)

    def test_general_email_read_uses_imap_digest_not_oauth(self) -> None:
        # Regression: "give me important emails" was routed to a high-risk Outlook
        # OAuth read and blocked; it must use the local IMAP digest instead.
        for phrase in (
            "for last 2 days - give me any important emails",
            "show me my emails",
            "any emails from the last 2 days",
        ):
            decision = self.plan(phrase)
            self.assertTrue(decision.is_command, phrase)
            self.assertEqual(decision.command, "email digest", phrase)

    def test_unread_email_still_routes_to_imap(self) -> None:
        self.assertEqual(self.plan("check unread emails").command, "email unread")

    def test_file_search_needs_a_file_cue(self) -> None:
        # explicit "files" -> file search
        decision = self.plan("search files config in src")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "search files config src")

    def test_named_file_routes_to_file_search(self) -> None:
        decision = self.plan("find report.txt in downloads")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "search files report.txt downloads")

    def test_open_youtube_and_search_routes_to_music(self) -> None:
        decision = self.plan("Hey Jarvis can you open YouTube and type Telugu music?")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "play music Telugu music")

    def test_play_on_youtube_routes_to_music(self) -> None:
        decision = self.plan("play despacito on youtube")
        self.assertEqual(decision.command, "play music despacito")

    def test_open_youtube_bare_opens_url(self) -> None:
        decision = self.plan("open youtube")
        self.assertEqual(decision.command, "open url https://www.youtube.com")

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

    def test_routes_inbox_digest(self) -> None:
        decision = self.plan("summarize my inbox")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email digest")

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

    def test_routes_briefing(self) -> None:
        decision = self.plan("give me a daily briefing")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "briefing")

    def test_routes_reminder_add(self) -> None:
        """The heuristic hands the phrasing through as said and lets `timeparse` read the
        time, so the exact wording of the routed command is deliberately not pinned here —
        only that it routes to `reminder add` and that the reminder comes out right. It
        used to rewrite the sentence itself, which is why it could only manage an ISO date
        and sent everything else to the model."""
        decision = self.plan("remind me to call Alex at 2026-06-20 09:00")
        self.assertTrue(decision.is_command)
        self.assertTrue(
            decision.command.startswith("reminder add "),
            f"routed to {decision.command!r}")
        self.assertIn("2026-06-20 09:00", decision.command)
        self.assertIn("call Alex", decision.command)

    def test_routes_reminders_due(self) -> None:
        decision = self.plan("show due reminders")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "reminders due")

    def test_routes_reminder_done(self) -> None:
        decision = self.plan("complete reminder 3")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "reminder done 3")

    def test_routes_explicit_terminal_command(self) -> None:
        decision = self.plan("run terminal command: echo hello")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "run command echo hello")

    def test_routes_workflow(self) -> None:
        decision = self.plan("run workflow: memory ;; tasks")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "workflow memory ;; tasks")

    def test_routes_workflow_retry(self) -> None:
        decision = self.plan("resume failed workflow")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "workflow retry failed")

    def test_routes_autopilot_goal(self) -> None:
        decision = self.plan("run autopilot for project health")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "autopilot project health")

    def test_routes_autopilot_status(self) -> None:
        decision = self.plan("show autopilot status")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "autopilot status")

    def test_routes_autonomous_agent_goal(self) -> None:
        decision = self.plan("autonomously summarize the README and index it")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "agent run summarize the README and index it")

    def test_routes_agent_runs(self) -> None:
        decision = self.plan("agent runs")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "agent runs")

    def test_do_phrase_is_not_hijacked_into_agent(self) -> None:
        # "do something vague" must NOT route to the autonomous agent.
        decision = self.plan("do something vague")
        self.assertFalse(decision.is_command and decision.command.startswith("agent run"))

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

    def test_routes_ask_file(self) -> None:
        decision = self.plan("what does report.txt say about latency")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "ask file report.txt about latency")

    def test_routes_ask_knowledge(self) -> None:
        decision = self.plan("answer from knowledge about payment retries")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "ask knowledge payment retries")

    def test_unknown_request_returns_chat(self) -> None:
        decision = self.plan("invent something vague")
        self.assertTrue(decision.is_chat)
        self.assertLess(decision.confidence, 0.5)

    def test_summarize_inbox_routes_to_digest(self) -> None:
        for text in ("summarize my inbox", "give me a digest of my emails", "summarize my unread emails"):
            self.assertEqual(self.plan(text).command, "email digest", text)

    def test_multi_query_mentioning_email_stays_chat(self) -> None:
        # "Summarize X" and "write an email" in separate sentences must not misroute to the
        # inbox (which would hit IMAP and fail on bad credentials).
        blob = (
            "Summarize the difference between RAM and storage in two sentences.\n"
            "Write a polite email asking to reschedule a meeting.\n"
            "Create a three-day beginner workout plan with no equipment."
        )
        self.assertTrue(self.plan(blob).is_chat)


if __name__ == "__main__":
    unittest.main()


class AddressStrippingTests(unittest.TestCase):
    """Every heuristic route matches from the start of the message, so addressing the
    assistant by name defeated all of them: "Hey Jarvis, draw me a fox" fell through to
    the LLM, which silently disabled the instant router and every guard built on it.
    People say the name out loud constantly."""

    def setUp(self) -> None:
        from laptop_agent.planner.heuristic import HeuristicPlannerProvider

        self.planner = HeuristicPlannerProvider()

    def strip(self, text: str) -> str:
        from laptop_agent.planner.heuristic import strip_address

        return strip_address(text)

    def test_a_wake_name_is_dropped_before_routing(self) -> None:
        for text, expected in (
            ("Hey Jarvis, news india", "news india"),
            ("Jarvis, scan files .", "scan files ."),
            ("J.A.R.V.I.S. news india", "news india"),
            ("ok computer, scan files .", "scan files ."),
            ("hey jarvis what time is it", "what time is it"),
        ):
            self.assertEqual(self.strip(text), expected, text)

    def test_routes_work_with_the_name_attached(self) -> None:
        for text, command in (
            ("Hey Jarvis, news india", "news india"),
            ("Jarvis, scan files .", "scan files ."),
            ("hey jarvis what time is it", "time what time is it"),
        ):
            self.assertEqual(self.planner.plan(text, "", {}).command, command, text)

    def test_a_bare_greeting_is_not_emptied(self) -> None:
        # "hey" and "jarvis" alone are greetings for the chat path, not requests with
        # their subject deleted.
        for text in ("hello", "hey", "jarvis", "hi there"):
            self.assertTrue(self.strip(text), text)
            self.assertFalse(self.planner.plan(text, "", {}).is_command, text)

    def test_an_ordinary_request_is_untouched(self) -> None:
        for text in ("news india", "scan files .", "what time is it", "draw a red fox"):
            self.assertEqual(self.strip(text), text)


class NewsTopicTests(unittest.TestCase):
    """"what's the news in india" was answered "Top stories about in india" — the
    preposition was being kept as part of the topic."""

    def setUp(self) -> None:
        from laptop_agent.planner.heuristic import HeuristicPlannerProvider

        self.planner = HeuristicPlannerProvider()

    def command(self, text: str) -> str:
        return self.planner.plan(text, "", {}).command or ""

    def test_a_preposition_is_not_part_of_the_topic(self) -> None:
        for text in ("what's the news in india", "news in india", "news from india",
                     "news about india", "Hey Jarvis, news in the india"):
            self.assertEqual(self.command(text), "news india", text)

    def test_a_bare_request_still_gets_everything(self) -> None:
        for text in ("news", "what's the news", "headlines"):
            self.assertEqual(self.command(text), "news", text)

    def test_a_real_topic_survives(self) -> None:
        self.assertEqual(self.command("latest news on ukraine"), "news ukraine")

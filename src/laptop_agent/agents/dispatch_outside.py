"""The command tables that reach outside: generation, translation, travel, the web, the desktop and email.

One part of `AgentOrchestrator` (agents/orchestrator.py), split out by area; it reads the
rest of the orchestrator through `self`."""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.translate import parse_translation
from laptop_agent.tools.travel import TravelTool
from laptop_agent.tools.weather import clean_place


class OutsideDispatch:
    async def _dispatch_generate(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for news, weather, translation, documents and pictures."""
        if lowered == "news":
            return self._news_tool().headlines()

        if lowered.startswith("news "):
            # "news about nvidia" searched for "about nvidia" and titled the answer "Top
            # stories about about nvidia".
            topic = re.sub(
                r"^(?:about|on|regarding|for|from|in|re|of)\s+",
                "",
                command[len("news ") :].strip(),
                flags=re.IGNORECASE,
            )
            topic = re.sub(
                r"\b(?:today|tonight|now|right now|please|headlines?)\s*$", "", topic, flags=re.IGNORECASE
            ).strip(" ?.!,")
            return self._news_tool().headlines(topic) if topic else self._news_tool().headlines()

        if lowered.startswith("document "):
            return self._document_tool().create(command[len("document ") :].strip())

        if lowered.startswith("workspace "):
            return self._workspace_tool().create(command[len("workspace ") :].strip())

        if lowered.startswith("image "):
            return self._generate_image(command[len("image ") :].strip())

        if lowered in {"weather", "forecast", "weather here", "local weather", "weather forecast"}:
            return self._forecast("")

        if lowered.startswith("weather "):
            return self._forecast(command[len("weather ") :])

        if lowered.startswith("translate "):
            return self._translation(command[len("translate ") :], history_turns)
        return None

    # "translate that to Spanish" means the reply above, not the word "that".
    _EARLIER_REPLY = re.compile(
        r"(?:this|that|it|the above|(?:your|the|that|this)\s+(?:last\s+|previous\s+)?"
        r"(?:answer|reply|response|message))",
        re.IGNORECASE,
    )
    # The web client appends a digest of each reply's tool data to the turn it sends back.
    _DIGEST_TAIL = re.compile(r"\n\[tool result data, context only[^\n]*$")
    # "translate what I copied to Spanish" means the clipboard.
    _CLIPBOARD_TEXT = re.compile(
        r"(?:what\s+i(?:'ve|\s+have)?\s+(?:just\s+)?copied|(?:my|the)\s+clipboard|the\s+text\s+i(?:'ve)?\s+(?:just\s+)?copied)",
        re.IGNORECASE,
    )

    # Our own clipboard replies are not something to copy: "copy that" twice copied the confirmation.
    _CLIPBOARD_REPLY = re.compile(r"(?:Copied to your clipboard:|Your clipboard has no text)")

    def _earlier_reply(self, history_turns) -> str:
        """The assistant's last real reply in this chat, without the web client's data digest, our
        own clipboard confirmations, or the heading the clipboard reply puts above the text."""
        for turn in reversed(history_turns or []):
            text = self._DIGEST_TAIL.sub("", str(turn.get("text") or "")).strip()
            if turn.get("role") != "assistant" or not text or self._CLIPBOARD_REPLY.match(text):
                continue
            return re.sub(r"^On your clipboard:\s*", "", text)
        return ""

    def _translation(self, raw: str, history_turns) -> ToolResult:
        """`translate <text> to <language> [from <language>]`."""
        parsed = parse_translation(raw)
        if parsed is None:
            return ToolResult.failure(
                "Say it like: translate good morning to Spanish, or translate bonjour from French to English."
            )
        text, target, source = parsed
        if self._EARLIER_REPLY.fullmatch(text):
            text = self._earlier_reply(history_turns)
            if not text:
                return ToolResult.failure("There is nothing earlier in this chat to translate.")
        elif self._CLIPBOARD_TEXT.fullmatch(text):
            read = self._clipboard_tool().content()  # refuses a personal account itself
            if not read.ok:
                return read
            text = str(read.data["text"]).strip()
            if not text:
                return ToolResult.failure("Your clipboard has no text to translate.")
        return self._translate_tool().translate(text, target, source)

    def _detect_language(self, text: str) -> str | None:
        """The fast tier names the language of Latin-script text bound for English: the
        translation service needs a source and cannot detect one."""
        provider = getattr(self.planner, "provider", None)
        answer = getattr(provider, "answer", None)
        if answer is None:
            return None
        request = (
            "Which language is this text written in? Reply with only the language's "
            "English name, one or two words.\n\n" + text[:300]
        )
        reply = (answer(request, {}, max_tokens=8) or "").strip()
        return reply.splitlines()[0].strip(" .\"'*") if reply else None

    # Profile keys that say where the user is: "remember my city is Austin" stores `city`.
    _HOME_KEYS = frozenset(
        {
            "city",
            "location",
            "home city",
            "hometown",
            "home town",
            "town",
            "home",
            "where i live",
            "zip",
            "zip code",
            "postcode",
        }
    )

    def _forecast(self, raw: str) -> ToolResult:
        """`weather [place]`. With no place, the forecast is for where the user is."""
        place = clean_place(raw) or self._home_place()
        if not place:
            return ToolResult.failure(
                'Where should I check? Name a city — or tell me once, "remember my city is '
                "Austin\", and I'll use it from then on."
            )
        return self._weather_tool().forecast(place)

    def _home_place(self) -> str:
        """A remembered city, else the approximate location from the IP address."""
        for key, value in self.context.memory.get_profile().items():
            if re.sub(r"[^a-z]+", " ", str(key).lower()).strip() in self._HOME_KEYS and str(value).strip():
                return str(value).strip()
        located = self._travel_tool().here()
        return str(located.data.get("label") or "") if located.ok else ""

    async def _dispatch_travel(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for distance, trips, maps and places."""
        if lowered.startswith("distance "):
            rest = command[len("distance ") :].strip()
            match = re.search(r"\s+(?:to|and|->|→)\s+", rest, re.IGNORECASE)
            if not match:
                return ToolResult.failure("Use: distance <origin> to <destination>")
            return self._travel_tool().distance(rest[: match.start()].strip(), rest[match.end() :].strip())

        if lowered.startswith("trip "):
            rest = command[len("trip ") :].strip()
            stops = (
                [s.strip() for s in rest.split("|")]
                if "|" in rest
                else re.split(r"\s+(?:to|->|→|then)\s+", rest, flags=re.IGNORECASE)
            )
            return self._travel_tool().trip([s for s in stops if s.strip()])

        if lowered.startswith("around "):
            # Only claim "around <category>" for a known place category, so ordinary
            # messages that merely start with "around" (e.g. "around 3pm, remind me…")
            # fall through to normal routing instead of a stray places lookup.
            first = command[len("around ") :].strip().split(None, 1)[0] if command[len("around ") :].strip() else ""
            if TravelTool.knows_category(first):
                return self._travel_tool().around(first)

        if lowered in {"where am i", "where am i?", "my location", "locate me", "what's my location"}:
            return self._travel_tool().here()

        if lowered.startswith("map "):
            return self._travel_tool().map_query(command[len("map ") :].strip())

        if lowered.startswith("hotels near ") or lowered.startswith("hotels in "):
            place = command.split(None, 2)[2] if len(command.split(None, 2)) > 2 else ""
            return self._travel_tool().nearby("hotel", place.strip())

        if lowered.startswith("nearby "):
            rest = command[len("nearby ") :].strip()
            match = re.search(r"\s+(?:near|in|around)\s+", rest, re.IGNORECASE)
            if not match:
                return ToolResult.failure("Use: nearby <category> near <place>")
            return self._travel_tool().nearby(rest[: match.start()].strip(), rest[match.end() :].strip())
        return None

    @staticmethod
    def _with_flights_link(query: str, result: ToolResult) -> ToolResult:
        """ "flights from Austin to Tokyo" is routed to a web search, which finds pages about fares
        but no fares: a Google Flights search for the same words goes at the top. No fare is ever
        stated by us; there is no free fare API to read one from."""
        if not re.match(r"flights?\s+(?:from\s+\S.*?\s+)?to\s+\S", query, re.IGNORECASE):
            return result
        link = "https://www.google.com/travel/flights?q=" + urllib.parse.quote(query)
        result.message = f"[Search live fares on Google Flights]({link})\n\n" + (result.message or "")
        result.data["flights_link"] = link
        return result

    async def _dispatch_web(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for YouTube, search, URLs, downloads and the browser."""
        if lowered.startswith("summarize youtube "):
            return self._youtube_summary(command[len("summarize youtube ") :].strip())

        if lowered.startswith("youtube summary "):
            return self._youtube_summary(command[len("youtube summary ") :].strip())

        if lowered.startswith("web search "):
            query = command[len("web search ") :].strip()
            return self._with_flights_link(query, self.context.websearch.search(query))

        if lowered.startswith("search web "):
            return self.context.websearch.search(command[len("search web ") :].strip())

        if lowered.startswith("message "):
            parsed = re.match(r"message (whatsapp|telegram)(?: to (.+?))? :: (.+)$", command, re.IGNORECASE | re.DOTALL)
            if not parsed:
                return ToolResult.failure("Say it like: message whatsapp to mom :: I'm running late.")
            return self._messaging().prepare(parsed.group(1), parsed.group(2) or "", parsed.group(3))

        if lowered.startswith("open url "):
            return self.context.web.open_url(command[len("open url ") :].strip())

        if lowered.startswith("download "):
            return self.context.web.download(command[len("download ") :].strip())

        if lowered.startswith("inspect page "):
            return await self.context.browser.inspect_page(command[len("inspect page ") :].strip())

        if lowered.startswith("inspect forms "):
            return await self.context.browser.inspect_forms(command[len("inspect forms ") :].strip())

        if lowered.startswith("preview form fill "):
            return await self.context.browser.preview_form_fill(
                command[len("preview form fill ") :].strip(),
                self.context.memory.get_profile(),
            )

        if lowered.startswith("fill form "):
            return await self.context.browser.fill_form(
                command[len("fill form ") :].strip(),
                self.context.memory.get_profile(),
            )
        return None

    async def _dispatch_desktop(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for apps, windows, screenshots, the shell and media keys."""
        if lowered == "lock screen":
            return self._system_tool().lock()
        if lowered in {"power sleep", "power shutdown", "power restart", "power cancel"}:
            action = lowered.split(" ", 1)[1]
            return self._system_tool().sleep() if action == "sleep" else self._system_tool().power(action)
        if lowered == "brightness" or lowered.startswith("brightness "):
            return self._system_tool().brightness(command[len("brightness") :])
        if lowered.startswith("wallpaper "):
            return self._system_tool().set_wallpaper(command[len("wallpaper ") :])
        if lowered == "processes":
            return self._system_tool().processes()
        if lowered.startswith("end process "):
            return self._system_tool().end_process(command[len("end process ") :])
        if lowered == "settings" or lowered.startswith("settings "):
            return self._system_tool().open_settings(command[len("settings") :])

        if lowered in {"windows", "list windows", "show windows", "what windows are open"}:
            return self.context.windows.list_windows()

        if lowered in {"window", "split", "snap", "arrange"} or lowered.startswith(
            ("window ", "windows ", "split ", "snap ", "arrange ")
        ):
            return self._arrange_windows(command)

        if lowered.startswith("open app "):
            return self.context.desktop.open_app_or_file(command[len("open app ") :].strip())

        if lowered in {"screenshot", "take a screenshot", "take screenshot", "screen shot"}:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return self.context.desktop.screenshot(str(self.data_dir / "screenshots" / f"screenshot-{stamp}.png"))

        if lowered.startswith("screenshot "):
            rest = command[len("screenshot ") :].strip()
            lead = re.match(r"(?:to|as|into)\s+", rest, re.IGNORECASE)
            target = rest[lead.end() :] if lead else rest
            # "screenshot my screen and tell me what's on it" saved a file named after the sentence,
            # "...and read the error/bug message" one at "error\bug message.png", and "...and read
            # the report.png" one at that sentence (Codex's reviews of #258). A destination is said
            # as one: a drive or a ./ ~/ / start, a single image file name, or one word after "to".
            # Asking what is on it is asking to look.
            if (
                re.match(r"[A-Za-z]:[\\/]|[.~]{0,2}[\\/]", target)
                or re.fullmatch(r"\S+\.(?:png|jpe?g|bmp|gif|webp)", target, re.IGNORECASE)
                or (lead and re.fullmatch(r"\S+", target))
            ):
                return self.context.desktop.screenshot(target)
            if re.search(
                r"\b(?:tell|describe|explain|read|summari[sz]e)\b|\bwhat(?:'s|s|\s+is)\s+on\b", target, re.IGNORECASE
            ):
                return self._read_screen()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return self.context.desktop.screenshot(str(self.data_dir / "screenshots" / f"screenshot-{stamp}.png"))

        if lowered.startswith("keys type "):
            app, _, text = command[len("keys type ") :].partition("::")
            return self._keys().type_text(text, app.strip())

        if lowered.startswith("keys "):
            action, _, app = command[len("keys ") :].partition(" in ")
            return self._keys().shortcut(action, app)

        if lowered.startswith("run command "):
            return self._run_terminal_command(command[len("run command ") :].strip())

        if lowered.startswith("terminal "):
            return self._run_terminal_command(command[len("terminal ") :].strip())

        if lowered.startswith("shell "):
            return self._run_terminal_command(command[len("shell ") :].strip())

        if lowered == "clipboard":
            return self._clipboard_tool().show()

        if lowered.startswith("clipboard copy "):
            text = command[len("clipboard copy ") :].strip()
            if self._EARLIER_REPLY.fullmatch(text):
                text = self._earlier_reply(history_turns)
                if not text:
                    return ToolResult.failure("There is nothing earlier in this chat to copy.")
            return self._clipboard_tool().copy(text)

        if lowered.startswith("clipboard ask "):
            return self._clipboard_question(command[len("clipboard ask ") :].strip())

        if lowered.startswith("play music "):
            return self.context.music.play(command[len("play music ") :].strip())

        level = re.fullmatch(r"media volume (\d{1,3})%?", lowered)
        if level:
            return self.context.music.set_volume(int(level.group(1)))

        if lowered.startswith("media "):
            return self.context.music.media_key(command[len("media ") :].strip())
        return None

    async def _dispatch_email(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for reading, drafting and sending mail."""

        if lowered in {
            "email digest",
            "summarize inbox",
            "summarize my inbox",
            "summarize my emails",
            "summarize my unread",
            "inbox digest",
        }:
            return self._email_digest("UNSEEN")

        if lowered.startswith("email digest "):
            return self._email_digest(command[len("email digest ") :].strip() or "UNSEEN")

        if lowered.startswith("email search "):
            return self.context.email.search_inbox(command[len("email search ") :].strip() or "ALL")

        if lowered.startswith("email unread"):
            return self.context.email.search_inbox("UNSEEN")

        if lowered.startswith("email api search "):
            parts = command[len("email api search ") :].strip().split(maxsplit=1)
            if len(parts) != 2:
                return ToolResult.failure("Use: email api search gmail|outlook <query>")
            return self.context.email.search_oauth_mail(parts[0], parts[1])

        if lowered.startswith("email api unread "):
            return self.context.email.search_oauth_mail(command[len("email api unread ") :].strip(), "UNSEEN")

        if lowered.startswith("email api draft "):
            parts = command[len("email api draft ") :].strip().split(maxsplit=1)
            if len(parts) != 2:
                return ToolResult.failure("Use: email api draft gmail|outlook to <addr> subject <subject> body <body>")
            draft_result = self._parse_email(parts[1])
            if not draft_result.ok:
                return draft_result
            return self.context.email.create_oauth_draft(parts[0], draft_result.data["draft"])

        if lowered.startswith("email api send "):
            parts = command[len("email api send ") :].strip().split(maxsplit=1)
            if len(parts) != 2:
                return ToolResult.failure("Use: email api send gmail|outlook to <addr> subject <subject> body <body>")
            draft_result = self._parse_email(parts[1])
            if not draft_result.ok:
                return draft_result
            return self.context.email.send_oauth_mail(parts[0], draft_result.data["draft"])

        if lowered in {"email oauth", "email oauth status"}:
            return self.context.email.oauth_status()

        if lowered.startswith("email oauth url "):
            return self.context.email.oauth_authorization_url(command[len("email oauth url ") :].strip())

        if lowered.startswith("email oauth exchange "):
            parts = command[len("email oauth exchange ") :].strip().split(maxsplit=1)
            if len(parts) != 2:
                return ToolResult.failure("Use: email oauth exchange gmail|outlook <authorization-code>")
            return self.context.email.exchange_oauth_code(parts[0], parts[1])

        if lowered.startswith("email oauth refresh "):
            return self.context.email.refresh_oauth_token(command[len("email oauth refresh ") :].strip())

        if lowered.startswith("email oauth forget "):
            return self.context.email.forget_oauth_token(command[len("email oauth forget ") :].strip())

        if lowered in {"email tokens", "email token status", "email tokens status"}:
            return self.context.email.token_status()

        if lowered.startswith("email "):
            return self._email(command[len("email ") :])

        if lowered.startswith("send email "):
            draft_result = self._parse_email(command[len("send email ") :])
            if not draft_result.ok:
                return draft_result
            return self.context.email.send_smtp(draft_result.data["draft"])
        return None

    async def _dispatch_batch(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for job applications and multi-command batches."""
        if lowered.startswith("plan apply job "):
            return await self.context.browser.prepare_job_application(
                command[len("plan apply job ") :].strip(),
                self.context.memory.get_profile(),
            )

        if lowered in {"multi retry failed", "retry failed tasks", "retry failed subtasks"}:
            return await self._retry_failed_tasks()

        if lowered.startswith("multi "):
            return await self._run_many(command[len("multi ") :])
        return None

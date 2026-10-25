"""`describe video`: a clip sent to a video-capable model, with the size limit measured on the hosted endpoint.

Nothing here touches the network or needs ffmpeg: the transport, the converter and the
approval gate are all injected.
"""
from __future__ import annotations

import base64
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from laptop_agent.app import _build_video_planner
from laptop_agent.planner.core import Planner
from laptop_agent.planner.openai_compatible import OpenAICompatiblePlannerProvider
from laptop_agent.safety import ApprovalDenied, RiskLevel
from laptop_agent.tools.video import MAX_VIDEO_BYTES, prepare_clip
import test_orchestrator

CLIP = b"\x00\x00\x00\x18ftypmp42" + b"x" * 100


def _clip(directory: Path, name: str = "holiday.mp4", size: int | None = None) -> Path:
    path = directory / name
    path.write_bytes(CLIP if size is None else b"x" * size)
    return path


class ProviderTests(unittest.TestCase):
    def test_the_clip_goes_inline_as_a_video_part(self) -> None:
        sent: list[dict] = []
        provider = OpenAICompatiblePlannerProvider(
            "k", "vid-model", transport=lambda payload: sent.append(payload) or "A red screen, then blue.")
        with tempfile.TemporaryDirectory() as raw:
            answer = provider.describe_video(str(_clip(Path(raw))), "what colours?")
        self.assertEqual(answer, "A red screen, then blue.")
        (payload,) = sent
        self.assertEqual(payload["model"], "vid-model")
        text, video = payload["messages"][0]["content"]
        self.assertEqual(text, {"type": "text", "text": "what colours?"})
        self.assertEqual(video["type"], "video_url")
        self.assertEqual(video["video_url"]["url"], "data:video/mp4;base64," + base64.b64encode(CLIP).decode())

    def test_an_unreadable_file_or_a_failed_call_is_none(self) -> None:
        def failing(payload):
            raise TimeoutError("slow")

        provider = OpenAICompatiblePlannerProvider("k", "m", transport=failing)
        with tempfile.TemporaryDirectory() as raw:
            self.assertIsNone(provider.describe_video(str(_clip(Path(raw))), "q"))
            self.assertIsNone(provider.describe_video(str(Path(raw) / "missing.mp4"), "q"))


class PrepareClipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()

    def fake_ffmpeg(self, out_size: int = 1000, code: int = 0):
        calls: list[list[str]] = []

        def run(command, **kwargs):
            calls.append(command)
            if code == 0:
                Path(command[-1]).write_bytes(b"y" * out_size)
            return subprocess.CompletedProcess(command, code, stdout="", stderr="")

        run.calls = calls
        return run

    def test_a_small_mp4_is_sent_as_it_is_and_needs_no_ffmpeg(self) -> None:
        source = _clip(self.root)
        clip, why = prepare_clip(source, self.scratch, runner=self.fake_ffmpeg(), which=lambda name: None)
        self.assertEqual((clip, why), (source, ""))

    def test_without_ffmpeg_a_big_or_foreign_clip_says_what_is_missing(self) -> None:
        big = _clip(self.root, size=MAX_VIDEO_BYTES + 1)
        clip, why = prepare_clip(big, self.scratch, which=lambda name: None)
        self.assertIsNone(clip)
        self.assertIn("ffmpeg is not installed", why)
        self.assertIn("8.0MB", why)
        clip, why = prepare_clip(_clip(self.root, "a.mov"), self.scratch, which=lambda name: None)
        self.assertIsNone(clip)
        self.assertIn(".mov", why)

    def test_ffmpeg_shrinks_a_big_clip_and_drops_its_audio(self) -> None:
        big = _clip(self.root, size=MAX_VIDEO_BYTES + 1)
        run = self.fake_ffmpeg(out_size=2000)
        clip, why = prepare_clip(big, self.scratch, runner=run, which=lambda name: "/usr/bin/ffmpeg")
        self.assertEqual(why, "")
        self.assertEqual(clip, self.scratch / "clip.mp4")
        (command,) = run.calls
        self.assertEqual(command[command.index("-i") + 1], str(big))
        self.assertIn("-an", command)

    def test_a_clip_that_is_still_too_big_or_unreadable_is_refused(self) -> None:
        big = _clip(self.root, size=MAX_VIDEO_BYTES + 1)
        found = lambda name: "/usr/bin/ffmpeg"
        clip, why = prepare_clip(big, self.scratch, runner=self.fake_ffmpeg(out_size=MAX_VIDEO_BYTES + 5), which=found)
        self.assertIsNone(clip)
        self.assertIn("shorter clip", why)
        clip, why = prepare_clip(big, self.scratch, runner=self.fake_ffmpeg(code=1), which=found)
        self.assertIsNone(clip)
        self.assertIn("could not read", why)


class DescribeVideoCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.orchestrator = test_orchestrator.OrchestratorTests().build(self.root / "data")
        self.asked: list = []
        gate = self.orchestrator.context.web.approval_gate
        gate._ask = lambda request: self.asked.append(request) or True
        self.questions: list[str] = []
        self.answer: str | None = "A dog runs across a lawn."

        class Provider:
            def describe_video(provider, path, prompt, model=None):
                self.questions.append(prompt)
                return self.answer

        self.orchestrator.video_planner = Planner(Provider())
        self.clip = _clip(self.root)

    def run_command(self, arguments: str):
        return self.orchestrator._describe_video(arguments)

    def test_it_watches_the_clip_and_asks_first(self) -> None:
        result = self.run_command(str(self.clip))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.message, "A dog runs across a lawn.")
        self.assertTrue(result.data["video"])
        (request,) = self.asked
        self.assertEqual(request.risk, RiskLevel.MEDIUM)
        self.assertIn("holiday.mp4", request.action)

    def test_what_follows_the_double_colon_is_the_question(self) -> None:
        self.run_command(f"{self.clip} :: what breed is the dog?")
        self.run_command(f"'{self.clip}'")
        self.assertEqual(self.questions[0], "what breed is the dog?")
        self.assertIn("Describe what happens", self.questions[1])

    def test_it_is_reachable_through_handle(self) -> None:
        import asyncio

        result = asyncio.run(self.orchestrator.handle(f"describe video {self.clip}"))
        self.assertEqual(result.message, "A dog runs across a lawn.")

    def test_a_refused_upload_sends_nothing(self) -> None:
        self.orchestrator.context.web.approval_gate._ask = lambda request: False
        with self.assertRaises(ApprovalDenied):
            self.run_command(str(self.clip))
        self.assertEqual(self.questions, [])

    def test_each_way_it_cannot_run_says_why(self) -> None:
        self.assertIn("Video not found", self.run_command(str(self.root / "gone.mp4")).message)
        self.assertIn("isn't a video file", self.run_command(str(_clip(self.root, "notes.txt"))).message)
        self.answer = None
        self.assertIn("did not answer", self.run_command(str(self.clip)).message)
        self.orchestrator.video_planner = None
        self.assertIn("OPENAI_VIDEO_MODEL", self.run_command(str(self.clip)).message)
        self.assertEqual(self.questions, [self.questions[0]])   # only the one that was answered with None


class ConfigTests(unittest.TestCase):
    def test_the_slot_needs_a_model_and_a_working_brain(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = test_orchestrator.OrchestratorTests().config(Path(raw))
            ready = replace(base, llm_provider="openai-compatible", llm_api_key="k", llm_model="chat")
            self.assertIsNone(_build_video_planner(ready))
            planner = _build_video_planner(replace(ready, llm_video_model="vid"))
            self.assertEqual(planner.provider.model, "vid")
            self.assertIsNone(_build_video_planner(replace(base, llm_video_model="vid")))

    def test_a_blank_setting_is_not_a_model(self) -> None:
        import os
        from unittest.mock import patch

        from laptop_agent.config import load_config

        for value, expected in (("", None), ("  ", None), (" some/model ", "some/model")):
            with patch.dict(os.environ, {"OPENAI_VIDEO_MODEL": value}):
                self.assertEqual(load_config().llm_video_model, expected)


if __name__ == "__main__":
    unittest.main()

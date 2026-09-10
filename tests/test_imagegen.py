from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from laptop_agent.safety import ApprovalDenied, ApprovalGate
from laptop_agent.tools.imagegen import ImageTool

# The smallest valid JPEG header the tool sniffs for, padded so it looks like real bytes.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def backend(raw: bytes = JPEG, seen: list[tuple[str, dict]] | None = None):
    def _call(model: str, body: dict) -> dict:
        if seen is not None:
            seen.append((model, body))
        return {"artifacts": [{"base64": base64.b64encode(raw).decode(), "finishReason": "SUCCESS", "seed": 7}]}

    return _call


class ImageToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def tool(self, **kwargs) -> ImageTool:
        kwargs.setdefault("backend", backend())
        return ImageTool(api_key="", data_dir=self.data_dir, **kwargs)

    def test_generate_writes_the_file_and_embeds_it(self) -> None:
        result = self.tool().generate("a brass compass")
        self.assertTrue(result.ok)
        saved = Path(result.data["image"])
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.parent, self.data_dir / "images")
        self.assertEqual(saved.suffix, ".jpg")  # extension comes from the magic bytes
        self.assertEqual(saved.read_bytes(), JPEG)
        self.assertIn(f"![a brass compass]({result.data['url']})", result.message)
        self.assertEqual(result.data["url"], f"/api/image?name={saved.name}")

    def test_png_bytes_get_a_png_extension(self) -> None:
        result = self.tool(backend=backend(PNG)).generate("a fox")
        self.assertTrue(result.ok)
        self.assertEqual(Path(result.data["image"]).suffix, ".png")

    def test_shape_picks_a_supported_resolution(self) -> None:
        seen: list[tuple[str, dict]] = []
        result = self.tool(backend=backend(seen=seen)).generate("a wide valley", shape="landscape")
        self.assertTrue(result.ok)
        self.assertEqual((seen[0][1]["width"], seen[0][1]["height"]), (1344, 768))
        self.assertEqual((result.data["width"], result.data["height"]), (1344, 768))

    def test_unknown_shape_falls_back_to_square(self) -> None:
        seen: list[tuple[str, dict]] = []
        self.tool(backend=backend(seen=seen)).generate("a fox", shape="hexagonal")
        self.assertEqual((seen[0][1]["width"], seen[0][1]["height"]), (1024, 1024))

    def test_empty_prompt_is_a_clear_failure(self) -> None:
        result = self.tool().generate("   ")
        self.assertFalse(result.ok)
        self.assertIn("What should I draw", result.message)

    def test_without_a_key_or_backend_it_explains_how_to_get_one(self) -> None:
        result = ImageTool(api_key="", data_dir=self.data_dir).generate("a fox")
        self.assertFalse(result.ok)
        self.assertIn("build.nvidia.com", result.message)
        self.assertFalse((self.data_dir / "images").exists())

    def test_empty_response_does_not_write_a_file(self) -> None:
        result = self.tool(backend=lambda model, body: {"artifacts": []}).generate("a fox")
        self.assertFalse(result.ok)
        self.assertIn("no picture", result.message)
        self.assertFalse((self.data_dir / "images").exists())

    def test_undecodable_payload_is_reported(self) -> None:
        result = self.tool(backend=lambda model, body: {"artifacts": [{"base64": "not base64!!"}]}).generate("a fox")
        self.assertFalse(result.ok)
        self.assertIn("could not decode", result.message)

    def test_pictures_made_in_the_same_second_do_not_overwrite_each_other(self) -> None:
        # The name carries a second-resolution timestamp, so three fast generations of the
        # same subject used to collide and leave one file.
        results = [self.tool().generate("a plain grey sphere") for _ in range(3)]
        names = [r.data["name"] for r in results]
        self.assertEqual(len(set(names)), 3, names)
        self.assertEqual(len(list((self.data_dir / "images").iterdir())), 3)
        for result in results:
            self.assertTrue(Path(result.data["image"]).is_file())

    def test_denied_approval_stops_the_call(self) -> None:
        calls: list[tuple[str, dict]] = []
        tool = self.tool(backend=backend(seen=calls), approval_gate=ApprovalGate(ask=lambda request: False))
        with self.assertRaises(ApprovalDenied):
            tool.generate("a fox")
        self.assertEqual(calls, [])


class FallbackTests(unittest.TestCase):
    """A queued primary endpoint is the ordinary failure, so a second model can stand in."""

    PRIMARY = "vendor/primary"
    SPARE = "vendor/spare"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def tool(self, backend_fn) -> ImageTool:
        return ImageTool(
            api_key="primary-key",
            data_dir=self.data_dir,
            model=self.PRIMARY,
            backend=backend_fn,
            fallback_model=self.SPARE,
            fallback_api_key="spare-key",
        )

    def test_spare_model_answers_when_the_primary_is_queued(self) -> None:
        good = backend()
        tried: list[str] = []

        def flaky(model: str, body: dict) -> dict:
            tried.append(model)
            if model == self.PRIMARY:
                raise TimeoutError("queued")
            return good(model, body)

        result = self.tool(flaky).generate("a fox")
        self.assertTrue(result.ok)
        self.assertEqual(tried, [self.PRIMARY, self.SPARE])
        self.assertEqual(result.data["model"], self.SPARE)
        self.assertTrue(result.data["fell_back"])
        self.assertIn("the usual model was busy", result.message)

    def test_a_working_primary_never_reaches_the_spare(self) -> None:
        seen: list[tuple[str, dict]] = []
        result = self.tool(backend(seen=seen)).generate("a fox")
        self.assertTrue(result.ok)
        self.assertEqual([model for model, _ in seen], [self.PRIMARY])
        self.assertFalse(result.data["fell_back"])

    def test_both_failing_reports_each_reason(self) -> None:
        def dead(model: str, body: dict) -> dict:
            raise TimeoutError("queued")

        result = self.tool(dead).generate("a fox")
        self.assertFalse(result.ok)
        self.assertIn(self.PRIMARY, result.message)
        self.assertIn(self.SPARE, result.message)
        self.assertEqual(len(result.data["attempts"]), 2)
        self.assertFalse((self.data_dir / "images").exists())

    def test_each_model_uses_its_own_key(self) -> None:
        tool = self.tool(backend())
        self.assertEqual(tool._key_for(self.PRIMARY), "primary-key")
        self.assertEqual(tool._key_for(self.SPARE), "spare-key")

    def test_a_spare_without_its_own_key_reuses_the_primary_key(self) -> None:
        tool = ImageTool(
            api_key="one-key",
            data_dir=self.data_dir,
            model=self.PRIMARY,
            backend=backend(),
            fallback_model=self.SPARE,
        )
        self.assertEqual(tool._key_for(self.SPARE), "one-key")


if __name__ == "__main__":
    unittest.main()

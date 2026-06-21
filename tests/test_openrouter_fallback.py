from __future__ import annotations

import unittest
from types import SimpleNamespace

from laptop_agent.app import _build_openrouter_planner


def _cfg(**kw):
    base = dict(
        openrouter_api_key=None,
        openrouter_model="meta-llama/llama-3.3-70b-instruct:free",
        openrouter_base_url="https://openrouter.ai/api/v1",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class OpenRouterPlannerBuildTests(unittest.TestCase):
    def test_none_without_key(self) -> None:
        self.assertIsNone(_build_openrouter_planner(_cfg()))

    def test_none_without_model(self) -> None:
        self.assertIsNone(_build_openrouter_planner(_cfg(openrouter_api_key="sk-x", openrouter_model=None)))

    def test_built_when_key_and_model_present(self) -> None:
        planner = _build_openrouter_planner(_cfg(openrouter_api_key="sk-x"))
        self.assertIsNotNone(planner)
        provider = planner.provider
        self.assertEqual(provider.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(provider.model, "meta-llama/llama-3.3-70b-instruct:free")
        self.assertEqual(provider.api_key, "sk-x")


if __name__ == "__main__":
    unittest.main()

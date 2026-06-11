from __future__ import annotations

import unittest

from laptop_agent.tools.browser import BrowserAutomationTool


class BrowserToolTests(unittest.TestCase):
    def test_maps_profile_values_to_form_fields(self) -> None:
        fields = [
            {
                "index": 0,
                "name": "candidateName",
                "field_id": "candidate-name",
                "label": "Full name",
                "placeholder": "",
                "required": True,
            },
            {
                "index": 1,
                "name": "email",
                "field_id": "email",
                "label": "Email address",
                "placeholder": "",
                "required": True,
            },
            {
                "index": 2,
                "name": "unknown",
                "field_id": "custom-question",
                "label": "Why do you want this role?",
                "placeholder": "",
                "required": False,
            },
        ]
        profile = {"name": "Ada Lovelace", "email": "ada@example.com"}

        mappings = BrowserAutomationTool.map_profile_to_fields(fields, profile)

        self.assertEqual(mappings[0]["matched_profile_key"], "name")
        self.assertEqual(mappings[0]["value_preview"], "Ada Lovelace")
        self.assertEqual(mappings[1]["matched_profile_key"], "email")
        self.assertEqual(mappings[2]["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()

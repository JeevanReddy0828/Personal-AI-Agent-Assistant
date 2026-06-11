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

    def test_builds_fill_preview_with_selectors_and_warnings(self) -> None:
        fields = [
            {
                "index": 0,
                "tag": "input",
                "field_type": "text",
                "name": "",
                "field_id": "full-name",
                "label": "Full name",
                "placeholder": "",
                "required": True,
            },
            {
                "index": 1,
                "tag": "input",
                "field_type": "email",
                "name": "email",
                "field_id": "",
                "label": "Email address",
                "placeholder": "",
                "required": True,
            },
            {
                "index": 2,
                "tag": "textarea",
                "field_type": "textarea",
                "name": "",
                "field_id": "",
                "label": "Cover letter",
                "placeholder": "",
                "required": True,
            },
        ]
        mappings = [
            {
                "field_index": 0,
                "field_label": "Full name",
                "required": True,
                "matched_profile_key": "name",
                "value_preview": "Ada Lovelace",
                "status": "mapped",
            },
            {
                "field_index": 1,
                "field_label": "Email address",
                "required": True,
                "matched_profile_key": "email",
                "value_preview": "ada@example.com",
                "status": "mapped",
            },
            {
                "field_index": 2,
                "field_label": "Cover letter",
                "required": True,
                "matched_profile_key": None,
                "value_preview": None,
                "status": "needs_review",
            },
        ]

        preview = BrowserAutomationTool.build_fill_preview(fields, mappings)

        self.assertEqual(preview[0]["selector"], "#full-name")
        self.assertEqual(preview[1]["selector"], 'input[name="email"]')
        self.assertTrue(preview[0]["would_fill"])
        self.assertFalse(preview[2]["would_fill"])
        self.assertEqual(preview[2]["status"], "needs_review")

    def test_builds_fill_actions_for_safe_text_fields_only(self) -> None:
        fill_preview = [
            {
                "field_index": 0,
                "selector": "#full-name",
                "field_label": "Full name",
                "field_type": "text",
                "matched_profile_key": "name",
                "value_preview": "Ada Lovelace",
                "would_fill": True,
            },
            {
                "field_index": 1,
                "selector": "#resume",
                "field_label": "Resume",
                "field_type": "file",
                "matched_profile_key": "resume",
                "value_preview": "resume.pdf",
                "would_fill": True,
            },
            {
                "field_index": 2,
                "selector": "#agree",
                "field_label": "Agree",
                "field_type": "checkbox",
                "matched_profile_key": "agree",
                "value_preview": "yes",
                "would_fill": True,
            },
        ]

        actions = BrowserAutomationTool.build_fill_actions(fill_preview)

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["selector"], "#full-name")


if __name__ == "__main__":
    unittest.main()

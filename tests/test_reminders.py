from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from laptop_agent.reminders import ReminderStore


class ReminderStoreTests(unittest.TestCase):
    def test_add_list_and_persist(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "reminders.json"
            store = ReminderStore(path)
            added = store.add("2026-06-20 09:30", "Call Alex")
            self.assertTrue(added["ok"])

            reopened = ReminderStore(path)
            reminders = reopened.list()

            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0]["message"], "Call Alex")
            self.assertEqual(reminders[0]["id"], 1)

    def test_due_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = ReminderStore(Path(raw) / "reminders.json")
            store.add("2026-06-10 09:00", "Past")
            store.add("2026-06-20 09:00", "Future")

            due = store.due(datetime(2026, 6, 16, tzinfo=UTC))
            self.assertEqual([item["message"] for item in due], ["Past"])
            self.assertTrue(store.complete(1))
            self.assertEqual(store.due(datetime(2026, 6, 16, tzinfo=UTC)), [])

    def test_corrupt_storage_falls_back_to_empty(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "reminders.json"
            path.write_text("not json", encoding="utf-8")
            store = ReminderStore(path)
            self.assertEqual(store.list(), [])

    def test_invalid_date_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = ReminderStore(Path(raw) / "reminders.json")
            with self.assertRaises(ValueError):
                store.add("tomorrow", "Call Alex")


if __name__ == "__main__":
    unittest.main()

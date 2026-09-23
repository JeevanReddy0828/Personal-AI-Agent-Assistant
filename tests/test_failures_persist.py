from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from laptop_agent.failures import Failure, FailureLog


class FailurePersistenceTests(unittest.TestCase):
    """A reason nobody can read later is not a diagnostic.

    This log exists because two outages hid behind code that handled them "gracefully".
    It then kept its records in memory only, so the reasons died with the process that
    caught them — measured on the real `.agent_data`, four `image` turns failed after
    ~61 seconds each and not one reason survived to say why.
    """

    def test_a_reason_survives_the_process_that_caught_it(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "failures.json"
            first = FailureLog()
            first.attach(path)
            first.record("imagegen/generate", TimeoutError("read timed out"), model="flux")

            # A different FailureLog is what a restart looks like from here.
            second = FailureLog()
            second.attach(path)
            recent = second.recent(10)

        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["where"], "imagegen/generate")
        self.assertEqual(recent[0]["kind"], "TimeoutError")
        self.assertIn("read timed out", recent[0]["message"])
        self.assertEqual(recent[0]["context"].get("model"), "flux")

    def test_age_is_wall_clock_so_a_restart_cannot_invent_one(self) -> None:
        """`monotonic` counts from a point that restarts with the process, so a persisted
        monotonic stamp read back by a fresh one puts the age anywhere between negative
        and centuries. `model_status.py` learned this first."""
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "failures.json"
            log = FailureLog()
            log.attach(path)
            log.record("somewhere", "reported")

            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(stored), 1)
            # Wall clock is seconds since the epoch: a monotonic stamp is far smaller.
            self.assertGreater(stored[0]["when"], 1_600_000_000,
                               "`when` is not wall clock; a restart will invent an age")
            self.assertLess(abs(stored[0]["when"] - time.time()), 60)

            reloaded = FailureLog()
            reloaded.attach(path)
            age = reloaded.recent(1)[0]["age_seconds"]
            self.assertGreaterEqual(age, 0.0)
            self.assertLess(age, 120.0, f"age survived the restart as {age}s")

    def test_this_session_is_appended_to_the_history_not_swapped_for_it(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "failures.json"
            old = FailureLog()
            old.attach(path)
            old.record("yesterday", "older")

            fresh = FailureLog()
            fresh.record("before_attach", "recorded while the path was still unknown")
            fresh.attach(path)
            fresh.record("today", "newer")

            wheres = [row["where"] for row in fresh.recent(10)]
        self.assertIn("yesterday", wheres, "the file's history was dropped on attach")
        self.assertIn("before_attach", wheres, "a record made before attach was lost")
        self.assertIn("today", wheres)
        self.assertEqual(wheres[0], "today", "recent() must still be newest first")

    def test_a_corrupt_file_costs_nothing(self) -> None:
        """Half a write, or somebody's editor, must not stop the log recording. This is
        the recorder of last resort: it fails soft or it fails everything."""
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "failures.json"
            path.write_text("{not json at all", encoding="utf-8")
            log = FailureLog()
            log.attach(path)
            log.record("after_corruption", "still works")
            self.assertEqual([r["where"] for r in log.recent(5)], ["after_corruption"])

    def test_an_unwritable_path_never_escalates_a_handled_error(self) -> None:
        """Recording is best effort by definition. A write failure here must not become
        the unhandled exception that a handled one was being recorded for."""
        with tempfile.TemporaryDirectory() as scratch:
            blocked = Path(scratch) / "failures.json"
            blocked.mkdir()                     # a directory can be neither written nor replaced
            log = FailureLog()
            log.attach(blocked)
            log.record("somewhere", ValueError("still recorded in memory"))
            self.assertEqual(len(log.recent(5)), 1)

    def test_an_unattached_log_still_works(self) -> None:
        log = FailureLog()
        log.record("no_path", "nothing to write to")
        self.assertEqual(len(log.recent(5)), 1)

    def test_the_ring_stays_bounded_across_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "failures.json"
            log = FailureLog(limit=5)
            log.attach(path)
            for index in range(12):
                log.record(f"where_{index}", "boom")
            self.assertEqual(len(log.recent(50)), 5)

            reloaded = FailureLog(limit=5)
            reloaded.attach(path)
            self.assertEqual(len(reloaded.recent(50)), 5,
                             "the file grew past the ring it is meant to mirror")
            self.assertEqual(reloaded.recent(1)[0]["where"], "where_11")

    def test_clear_empties_the_file_too(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "failures.json"
            log = FailureLog()
            log.attach(path)
            log.record("somewhere", "boom")
            log.clear()

            reloaded = FailureLog()
            reloaded.attach(path)
            self.assertEqual(reloaded.recent(5), [],
                             "clearing left the records on disk to come back")


class OrchestratorAttachesTheLogTests(unittest.TestCase):
    def test_the_orchestrator_points_the_log_at_its_own_data_dir(self) -> None:
        """Not `load_config()`: that reads the process-wide directory, which is how
        traces from a test run once landed in the live `.agent_data`."""
        source = (Path(__file__).resolve().parents[1]
                  / "src" / "laptop_agent" / "agents" / "orchestrator.py"
                  ).read_text(encoding="utf-8")
        self.assertIn('FAILURES.attach(self.data_dir / "failures.json")', source)


if __name__ == "__main__":
    unittest.main()


class AttachIsIdempotentTests(unittest.TestCase):
    """The orchestrator attaches on construction, and a test run builds many. A record
    already in memory has also been written to the file, so a second attach to the same
    path counted it twice — the ring filled with copies until `recent(50)` saturated and
    an unrelated test failed with "50 not greater than 50"."""

    def test_attaching_twice_does_not_duplicate_a_record(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "failures.json"
            log = FailureLog()
            log.attach(path)
            log.record("once", "only once")
            for _ in range(5):
                log.attach(path)
            wheres = [row["where"] for row in log.recent(50)]
        self.assertEqual(wheres, ["once"], f"the record was duplicated: {wheres}")

    def test_repeated_attach_does_not_grow_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "failures.json"
            log = FailureLog()
            log.attach(path)
            for index in range(3):
                log.record(f"where_{index}", "boom")
            first = len(json.loads(path.read_text(encoding="utf-8")))
            for _ in range(4):
                log.attach(path)
            log.record("last", "boom")
            grown = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(first, 3)
        self.assertEqual(len(grown), 4, f"attach duplicated history on disk: {len(grown)} rows")

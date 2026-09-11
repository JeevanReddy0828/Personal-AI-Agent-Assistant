from __future__ import annotations

import threading
import unittest

from laptop_agent.approvals import ApprovalBroker


class ApprovalBrokerTests(unittest.TestCase):
    """The browser had no way to answer the approval gate, so every HIGH/CRITICAL action
    was auto-denied: downloads, shell commands and sending mail simply did not work in the
    app's main interface. This bridges the blocking gate to an HTTP answer — carefully,
    because it is the thing standing between a model's suggestion and the user's laptop."""

    def broker(self, timeout: float = 5.0) -> ApprovalBroker:
        broker = ApprovalBroker(timeout=timeout)
        broker.add_listener(lambda request: None)      # a connected UI
        return broker

    def answer_soon(self, broker: ApprovalBroker, approved: bool, delay: float = 0.05):
        def run():
            deadline = threading.Event()
            deadline.wait(delay)
            pending = broker.pending()
            if pending:
                broker.resolve(str(pending[0]["id"]), approved)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def test_an_approval_lets_the_action_through(self) -> None:
        broker = self.broker()
        self.answer_soon(broker, True)
        self.assertTrue(broker.request("Download file: https://example.com/a.csv", "high", "why"))

    def test_a_denial_stops_it(self) -> None:
        broker = self.broker()
        self.answer_soon(broker, False)
        self.assertFalse(broker.request("Download file: https://example.com/a.csv", "high", "why"))

    def test_silence_is_never_consent(self) -> None:
        # A timeout must deny. Nobody answers here.
        broker = self.broker(timeout=0.2)
        self.assertFalse(broker.request("Send email to boss@example.com", "critical", "why"))

    def test_no_connected_ui_denies_immediately(self) -> None:
        # Nobody can answer, so waiting the full timeout only pins the thread.
        broker = ApprovalBroker(timeout=30.0)
        started = threading.Event()

        def run():
            broker.request("Run command: rm -rf /", "critical", "why")
            started.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(started.wait(2.0), "request should not block without a listener")

    def test_an_id_is_single_use(self) -> None:
        broker = self.broker(timeout=2.0)
        seen: list[str] = []
        broker.add_listener(lambda request: seen.append(str(request["id"])))

        outcome: list[bool] = []
        thread = threading.Thread(
            target=lambda: outcome.append(broker.request("Download file: x", "high", "why")),
            daemon=True,
        )
        thread.start()
        while not seen:
            threading.Event().wait(0.01)
        request_id = seen[0]
        self.assertTrue(broker.resolve(request_id, True))
        thread.join(timeout=2.0)
        self.assertEqual(outcome, [True])
        # Replaying the same id must not authorise anything else.
        self.assertFalse(broker.resolve(request_id, True))

    def test_an_unknown_id_is_refused(self) -> None:
        self.assertFalse(self.broker().resolve("not-a-real-id", True))

    def test_pending_describes_the_action_for_the_card(self) -> None:
        broker = self.broker(timeout=2.0)
        threading.Thread(
            target=lambda: broker.request(
                "Download file: https://example.com/a.csv", "high", "Downloads can be unsafe.",
                preview="Destination: C:/downloads/a.csv",
            ),
            daemon=True,
        ).start()
        while not broker.pending():
            threading.Event().wait(0.01)
        entry = broker.pending()[0]
        self.assertEqual(entry["risk"], "high")
        self.assertIn("example.com", str(entry["action"]))
        self.assertIn("unsafe", str(entry["reason"]))
        self.assertIn("Destination", str(entry["preview"]))

    def test_a_broken_listener_does_not_block_the_request(self) -> None:
        # A dead SSE connection must not stop the approval being answerable elsewhere.
        broker = ApprovalBroker(timeout=0.3)

        def exploding(request):
            raise BrokenPipeError("client gone")

        broker.add_listener(exploding)
        self.assertFalse(broker.request("Download file: x", "high", "why"))


if __name__ == "__main__":
    unittest.main()

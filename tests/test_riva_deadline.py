from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from laptop_agent import nvcf
from laptop_agent.cancellation import OperationCancelled, cancel, operation
from laptop_agent.tools import transcribe


class WaitExpired(Exception):
    pass


class Pending:
    def __init__(self, clock, result=None, error=None, on_wait=None):
        self.clock, self.value, self.error, self.on_wait = clock, result, error, on_wait
        self.waits = []
        self.cancelled = False

    def result(self, timeout=None):
        if timeout is None or not 0 < timeout <= .1:
            raise AssertionError('Riva wait must be positive and at most 0.1 seconds')
        self.waits.append(timeout)
        if self.on_wait:
            self.on_wait()
        if self.error:
            raise self.error
        if self.value is not None:
            return self.value
        self.clock[0] += timeout
        raise WaitExpired()

    def cancel(self):
        self.cancelled = True
        return True


class RivaDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clip = Path(self.temp.name)/'clip.wav'
        with wave.open(str(self.clip), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            audio.writeframes(b'\0\0'*1600)
        self.clock = [0.0]
        self.pending = Pending(self.clock)
        self.channel = Mock()
        self.calls = []
        client = types.ModuleType('riva.client')
        client.Auth = lambda **kw: types.SimpleNamespace(channel=self.channel)
        client.RecognitionConfig = lambda **kw: kw
        client.AudioEncoding = types.SimpleNamespace(LINEAR_PCM=1)
        def recognize(audio, config, future=False):
            self.calls.append((len(audio), config, future))
            if not future:
                raise AssertionError('The blocking SDK call has no deadline')
            return self.pending
        client.ASRService = lambda auth: types.SimpleNamespace(offline_recognize=recognize)
        parent = types.ModuleType('riva'); parent.client = client
        grpc = types.ModuleType('grpc'); grpc.FutureTimeoutError = WaitExpired
        fixtures = [patch.dict(sys.modules, {'riva': parent, 'riva.client': client, 'grpc': grpc}),
                    patch.dict(os.environ, {'RIVA_API_KEY': 'fixture-not-a-key', 'RIVA_ASR_TIMEOUT_SECONDS': '.35'}),
                    patch.object(transcribe.time, 'monotonic', lambda: self.clock[0]),
                    patch.object(transcribe, 'record_failure')]
        for fixture in fixtures:
            fixture.start(); self.addCleanup(fixture.stop)

    def test_silent_rpc_is_cancelled_at_the_deadline_and_channel_closed(self):
        with self.assertRaisesRegex(TimeoutError, '0.35-second deadline'):
            transcribe._riva_asr_backend(self.clip)
        self.assertAlmostEqual(self.clock[0], .35)
        self.assertTrue(all(0 < duration <= .1 for duration in self.pending.waits))
        self.assertTrue(self.pending.cancelled)
        self.channel.close.assert_called_once()
        transcribe.record_failure.assert_called_once()
        self.assertEqual(transcribe.record_failure.call_args.args[0], 'transcribe/riva-timeout')

    def test_success_keeps_transcript_and_releases_channel(self):
        self.pending.value = types.SimpleNamespace(results=[types.SimpleNamespace(
            alternatives=[types.SimpleNamespace(transcript='hello from the fixture')])])
        result = transcribe._riva_asr_backend(self.clip)
        self.assertEqual(result['text'], 'hello from the fixture')
        self.assertEqual(result['engine'], 'riva:parakeet')
        self.assertTrue(self.calls[0][2])
        self.channel.close.assert_called_once()
        transcribe.record_failure.assert_not_called()

    def test_sdk_error_still_cleans_up(self):
        self.pending.error = RuntimeError('fixture rpc failed')
        with self.assertRaisesRegex(RuntimeError, 'fixture rpc failed'):
            transcribe._riva_asr_backend(self.clip)
        self.assertTrue(self.pending.cancelled)
        self.channel.close.assert_called_once()

    def test_stop_cancels_rpc_without_starting_local_fallback(self):
        self.pending.on_wait = lambda: cancel('riva-stop-fixture')
        with patch.dict(os.environ, {'LAPTOP_AGENT_STT': 'auto'}), \
             patch.object(transcribe, '_riva_available', return_value=True), \
             patch.object(transcribe, '_vosk_asr_backend') as local:
            with operation('riva-stop-fixture'), self.assertRaises(OperationCancelled):
                transcribe._default_asr_backend(self.clip)
        self.assertTrue(self.pending.cancelled)
        self.channel.close.assert_called_once()
        local.assert_not_called()

    def test_stop_racing_an_rpc_error_does_not_fall_back(self):
        self.pending.error = RuntimeError('connection closed')
        self.pending.on_wait = lambda: cancel('riva-error-race')
        with patch.dict(os.environ, {'LAPTOP_AGENT_STT': 'auto'}), \
             patch.object(transcribe, '_riva_available', return_value=True), \
             patch.object(transcribe, '_vosk_asr_backend') as local:
            with operation('riva-error-race'), self.assertRaises(OperationCancelled):
                transcribe._default_asr_backend(self.clip)
        self.assertTrue(self.pending.cancelled)
        self.channel.close.assert_called_once()
        local.assert_not_called()

    def test_auto_timeout_uses_local_backend_but_explicit_riva_reports_failure(self):
        with patch.object(transcribe, '_riva_available', return_value=True), \
             patch.object(transcribe, '_vosk_available', return_value=True), \
             patch.object(transcribe, '_vosk_asr_backend', return_value={'text': 'local fallback'}) as local:
            with patch.dict(os.environ, {'LAPTOP_AGENT_STT': 'auto'}):
                result = transcribe.TranscribeTool().transcribe_media(str(self.clip))
            self.assertTrue(result.ok)
            self.assertEqual(result.data['text'], 'local fallback')
            local.assert_called_once()
            local.reset_mock()
            self.pending = Pending(self.clock)
            with patch.dict(os.environ, {'LAPTOP_AGENT_STT': 'riva'}):
                result = transcribe.TranscribeTool().transcribe_media(str(self.clip))
            self.assertFalse(result.ok)
            self.assertIn('deadline', result.message)
            local.assert_not_called()

    def test_a_retired_model_id_is_looked_up_and_retried_with_both_rpcs_released(self):
        class NotFound(Exception):
            def code(self):
                return types.SimpleNamespace(name='NOT_FOUND')

        used = []
        client = sys.modules['riva.client']
        client.Auth = lambda **kw: (used.append(dict(kw['metadata_args'])['function-id']),
                                    types.SimpleNamespace(channel=self.channel))[1]
        transcript = types.SimpleNamespace(results=[types.SimpleNamespace(
            alternatives=[types.SimpleNamespace(transcript='found again')])])
        retired, current = Pending(self.clock, error=NotFound()), Pending(self.clock, result=transcript)
        pendings = iter([retired, current])
        client.ASRService = lambda auth: types.SimpleNamespace(
            offline_recognize=lambda audio, config, future=False: next(pendings))
        listing = [{'name': transcribe.RIVA_ASR_NAME, 'id': 'current-id', 'status': 'ACTIVE', 'createdAt': '2026-10-06'}]
        nvcf._reset()
        self.addCleanup(nvcf._reset)
        with patch.object(nvcf, '_list_functions', lambda key: listing):
            result = transcribe._riva_asr_backend(self.clip)
        self.assertEqual(result['text'], 'found again')
        self.assertEqual(used, [transcribe.RIVA_ASR_FUNCTION_ID, 'current-id'])
        self.assertTrue(retired.cancelled and current.cancelled)
        self.assertEqual(self.channel.close.call_count, 2)

    def test_a_stalled_id_lookup_stays_inside_the_riva_deadline(self):
        class NotFound(Exception):
            def code(self):
                return types.SimpleNamespace(name='NOT_FOUND')

        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.pending.error = NotFound()
        nvcf._reset()
        self.addCleanup(nvcf._reset)

        def stalled(key):
            entered.set()
            release.wait(2)
            return [{'name': transcribe.RIVA_ASR_NAME, 'id': 'current-id',
                     'status': 'ACTIVE', 'createdAt': '2026-10-07'}]

        started = time.perf_counter()
        with patch.dict(os.environ, {'RIVA_ASR_TIMEOUT_SECONDS': '.05', 'RIVA_ASR_FUNCTION_ID': ''}), \
             patch.object(transcribe.time, 'monotonic', time.perf_counter), \
             patch.object(nvcf, '_list_functions', stalled), self.assertRaises(TimeoutError):
            transcribe._riva_asr_backend(self.clip)
        self.assertTrue(entered.is_set())
        self.assertLess(time.perf_counter() - started, 1)

    def test_stop_during_id_lookup_does_not_start_a_local_fallback(self):
        class NotFound(Exception):
            def code(self):
                return types.SimpleNamespace(name='NOT_FOUND')

        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.pending.error = NotFound()
        nvcf._reset()
        self.addCleanup(nvcf._reset)

        def stalled(key):
            entered.set()
            cancel('riva-lookup-stop')
            release.wait(2)
            return []

        started = time.perf_counter()
        with patch.dict(os.environ, {'LAPTOP_AGENT_STT': 'auto', 'RIVA_ASR_FUNCTION_ID': ''}), \
             patch.object(transcribe.time, 'monotonic', time.perf_counter), \
             patch.object(transcribe, '_riva_available', return_value=True), \
             patch.object(transcribe, '_vosk_asr_backend') as local, \
             patch.object(nvcf, '_list_functions', stalled), operation('riva-lookup-stop'), \
             self.assertRaises(OperationCancelled):
            transcribe._default_asr_backend(self.clip)
        self.assertTrue(entered.is_set())
        self.assertLess(time.perf_counter() - started, 1)
        local.assert_not_called()

    def test_default_budget_grows_with_audio_and_has_a_ceiling(self):
        with patch.dict(os.environ, {'RIVA_ASR_TIMEOUT_SECONDS': ''}):
            self.assertEqual(transcribe._riva_timeout(.1), 10)
            self.assertEqual(transcribe._riva_timeout(20), 15)
            self.assertEqual(transcribe._riva_timeout(120), 65)
            self.assertEqual(transcribe._riva_timeout(3600), 120)

    def test_override_rejects_unbounded_or_invalid_values(self):
        for value in ('nan', 'inf', '-1', '0', '601', 'oops'):
            with self.subTest(value=value), patch.dict(os.environ, {'RIVA_ASR_TIMEOUT_SECONDS': value}):
                with self.assertRaisesRegex(RuntimeError, 'RIVA_ASR_TIMEOUT_SECONDS'):
                    transcribe._riva_timeout(1)
        with patch.dict(os.environ, {'RIVA_ASR_TIMEOUT_SECONDS': '60'}):
            self.assertEqual(transcribe._riva_timeout(3600), 60)

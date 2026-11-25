"""Booster K1 mic stream: mono conversion, re-chunking, abort and the voice backend switch."""
import os
import sys
import threading
import time
import unittest

import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hal.drivers.voice._internal import booster_mic  # noqa: E402
from hal.drivers.voice._internal.booster_mic import BoosterMicStream, _BoosterCapture  # noqa: E402


class _Fmt:
    def __init__(self, channels):
        self.channels = channels


class _Frame:
    def __init__(self, mono, channels=3):
        self.raw_valid = True
        self.raw_format = _Fmt(channels)
        # Booster interleaves identical channels; the SDK hands a list of ints.
        self.raw_pcm = np.repeat(np.asarray(mono, dtype=np.int16), channels).tolist()


class _FakeCapture(_BoosterCapture):
    """Shared capture without the SDK: tests push frames directly."""

    def _open(self):
        with self._lock:
            self._stream = object()


class TestBoosterMic(unittest.TestCase):
    def setUp(self):
        self.cap = _FakeCapture(np)
        booster_mic._shared = self.cap

    def tearDown(self):
        booster_mic._shared = None

    def test_to_mono_takes_channel_zero(self):
        pcm = [1, 2, 3, 4, 5, 6]
        self.assertEqual(_BoosterCapture.to_mono(pcm, 3, np).tolist(), [1, 4])

    def test_rechunks_682_sample_frames_into_1024(self):
        mic = BoosterMicStream(16000, 1, 1024, np)
        with mic:
            for i in range(4):  # 4 * 682 = 2728 >= 2 * 1024
                self.cap._on_frame(_Frame(np.arange(i * 682, (i + 1) * 682) % 30000))
            a, over = mic.read(1024)
            b, _ = mic.read(1024)
        self.assertFalse(over)
        self.assertEqual(a.shape, (1024, 1))
        self.assertEqual(a.dtype, np.int16)
        got = np.concatenate([a[:, 0], b[:, 0]])
        self.assertEqual(got.tolist(), (np.arange(2048) % 30000).tolist())

    def test_new_reader_starts_at_now(self):
        self.cap._on_frame(_Frame(np.full(682, 7)))
        mic = BoosterMicStream(16000, 1, 512, np)
        with mic:
            self.cap._on_frame(_Frame(np.full(682, 9)))
            data, _ = mic.read(512)
        self.assertTrue((data == 9).all(), "stale audio from before open leaked into the reader")

    def test_abort_unblocks_read(self):
        mic = BoosterMicStream(16000, 1, 1024, np)
        errors = []

        def reader():
            try:
                mic.read(1024)
            except IOError as e:
                errors.append(str(e))

        with mic:
            t = threading.Thread(target=reader)
            t.start()
            time.sleep(0.15)
            mic.abort()
            t.join(1.0)
        self.assertFalse(t.is_alive())
        self.assertIn("aborted", errors[0])

    def test_buffer_is_bounded(self):
        with BoosterMicStream(16000, 1, 1024, np):
            for _ in range(200):  # ~8.5 s of audio, limit is 2 s
                self.cap._on_frame(_Frame(np.zeros(682)))
            self.assertLessEqual(self.cap._buffered, int(booster_mic._MAX_BUFFER_S * 16000))

    def test_rejects_wrong_rate(self):
        with self.assertRaises(ValueError):
            BoosterMicStream(48000, 1, 1024, np)


class TestVoiceBackendSwitch(unittest.TestCase):
    def test_open_mic_picks_booster_stream(self):
        from hal.drivers.voice.voice_service import VoiceService

        vs = VoiceService.__new__(VoiceService)
        vs._mic_backend = "booster"
        vs._np = np
        vs._alsa_device = None
        self.assertIsInstance(vs._open_mic(16000, 1024), BoosterMicStream)

    def test_open_mic_default_is_unchanged(self):
        from hal.drivers.voice._internal.audio_recorder import ArecordStream
        from hal.drivers.voice.voice_service import VoiceService

        vs = VoiceService.__new__(VoiceService)
        vs._mic_backend = ""
        vs._np = np
        vs._alsa_device = "plug:device_mic"
        self.assertIsInstance(vs._open_mic(16000, 1024), ArecordStream)


if __name__ == "__main__":
    unittest.main()

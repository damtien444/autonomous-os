"""Booster K1 speaker as a sounddevice stand-in: format, pacing, silence and flush."""
import os
import sys
import time
import unittest

import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hal.drivers.voice.tts import booster_output as bo  # noqa: E402


class FakePlayer:
    def __init__(self):
        self.pushed = []
        self.flushes = 0

    def push(self, pcm):
        self.pushed.append(pcm)

    def flush(self):
        self.flushes += 1


class TestBoosterSpeaker(unittest.TestCase):
    def setUp(self):
        self.player = FakePlayer()
        self._orig = bo._player
        bo._player = self.player

    def tearDown(self):
        bo._player = self._orig

    def test_only_16k_mono_opens(self):
        sd = bo.BoosterSoundDevice()
        with self.assertRaises(sd.PortAudioError):
            sd.OutputStream(samplerate=44100, channels=1)
        with self.assertRaises(sd.PortAudioError):
            sd.OutputStream(samplerate=16000, channels=2)
        s = sd.OutputStream(samplerate=16000, channels=1, dtype="float32")
        self.assertEqual(s.samplerate, 16000)

    def test_float_is_pushed_as_int16(self):
        s = bo.BoosterOutputStream(16000)
        s.start()
        s.write(np.array([0.5, -0.5, 1.5], dtype=np.float32))
        pcm = np.frombuffer(self.player.pushed[0], dtype="<i2")
        self.assertEqual(pcm.tolist(), [16383, -16383, 32767])  # clipped at full scale

    def test_silence_is_not_pushed(self):
        s = bo.BoosterOutputStream(16000)
        s.start()
        s.write(np.zeros(320, dtype=np.float32))  # TTSService keepalive
        self.assertEqual(self.player.pushed, [])

    def test_write_paces_to_real_time(self):
        s = bo.BoosterOutputStream(16000)
        s.start()
        t = time.monotonic()
        for _ in range(10):  # 10 x 0.1 s of audio
            s.write(np.full(1600, 0.1, dtype=np.float32))
        elapsed = time.monotonic() - t
        # Blocks to ~real time minus the allowed lead; never returns instantly.
        self.assertGreater(elapsed, 1.0 - bo._LEAD_S - 0.1)
        self.assertLess(elapsed, 1.2)

    def test_stop_flushes_and_write_after_stop_fails(self):
        s = bo.BoosterOutputStream(16000)
        s.start()
        s.write(np.full(160, 0.2, dtype=np.float32))
        s.stop()
        self.assertEqual(self.player.flushes, 1)
        with self.assertRaises(bo.PortAudioError):
            s.write(np.full(160, 0.2, dtype=np.float32))

    def test_query_devices_reports_16k(self):
        info = bo.BoosterSoundDevice.query_devices(0, "output")
        self.assertEqual(info["default_samplerate"], 16000.0)


if __name__ == "__main__":
    unittest.main()

"""Booster K1 speaker as a sounddevice stand-in for HAL's TTS output.

Booster's `audio` service owns the K1 speaker. Its SDK `AudioPlayer` accepts a
PCM stream (`PushPcmStream`, 16 kHz mono int16). `TTSService` only uses
`sounddevice.OutputStream`, `query_devices` and `PortAudioError`, so
`BoosterSoundDevice` provides exactly that surface and HAL's whole speech path
(TTS, chimes, native realtime audio, AEC reference) plays on the robot.

`write()` paces itself to real time: TTSService's stall watchdog and echo
reference assume a blocking device write, and Booster's player otherwise
accepts audio far faster than it plays it.

Selected with HAL_AUDIO_OUTPUT_BACKEND=booster (robots/booster-k1).
"""
from __future__ import annotations

import logging
import os
import threading
import time

import numpy as np

logger = logging.getLogger("hal.voice.tts.booster")

RATE = 16000
# K1 player volume 0..1 (Booster's AudioPlayer.SetVolume). HAL's /audio/volume
# drives a local ALSA mixer, which does not reach the robot's speaker.
VOLUME = max(0.0, min(1.0, float(os.environ.get("HAL_BOOSTER_SPEAKER_VOLUME", "0.3"))))
# Audio queued ahead of real time before write() starts to block.
_LEAD_S = 0.25


class PortAudioError(Exception):
    """Raised on a write to a stopped/closed stream (mirrors sounddevice)."""


class _Player:
    """One Booster player session per process, (re)started lazily."""

    def __init__(self):
        self._lock = threading.Lock()
        self._manager = None
        self._player = None

    def _ensure(self):
        if self._player is not None:
            return self._player
        import booster_robotics_sdk_python as sdk

        from hal.drivers.motors.booster_service import init_booster_channel

        init_booster_channel()
        if self._manager is None:
            self._manager = sdk.AudioManager()
            self._manager.Init()
        opts = sdk.PlayerInitOptions()
        opts.source_type = sdk.AudioSourceType.kPcmStream
        opts.sample_rate_hz = RATE
        opts.channels = 1
        opts.bits_per_sample = 16
        opts.priority = sdk.PlayerPriority.kHigh
        player = self._manager.CreatePlayer()
        player.Init(opts)
        player.SetVolume(VOLUME)
        player.Start()
        self._player = player
        logger.info("[booster-speaker] player stream started (16 kHz mono, volume %.2f)", VOLUME)
        return player

    def push(self, pcm: bytes) -> None:
        with self._lock:
            player = self._ensure()
            try:
                player.PushPcmStream(pcm)
            except Exception:
                # A dropped player (robot reboot, audio service restart) is rebuilt once.
                self._drop()
                self._ensure().PushPcmStream(pcm)

    def flush(self) -> None:
        """Discard audio queued in the player (barge-in / stop)."""
        with self._lock:
            self._drop()

    def _drop(self) -> None:
        player, self._player = self._player, None
        if player is None:
            return
        for fn in ("Stop", "Destroy"):
            try:
                getattr(player, fn)()
            except Exception as e:
                logger.debug("[booster-speaker] player %s failed: %s", fn, e)


_player = _Player()


class BoosterOutputStream:
    """sounddevice.OutputStream subset used by TTSService (float32 mono)."""

    def __init__(self, samplerate=RATE, channels=1, dtype="float32", device=None, latency=None, **_):
        if int(samplerate) != RATE:
            raise PortAudioError(f"booster speaker plays {RATE} Hz only, asked for {samplerate}")
        if channels != 1:
            raise PortAudioError("booster speaker is mono")
        self.samplerate = RATE
        self.channels = 1
        self.latency = _LEAD_S
        self._active = False
        self._closed = False
        self._clock = 0.0  # monotonic time when everything pushed so far ends

    def start(self):
        self._active = True
        self._clock = time.monotonic()

    def stop(self):
        # Stop discards queued audio, like aborting a PortAudio stream.
        if self._active:
            _player.flush()
        self._active = False

    abort = stop

    def close(self):
        self.stop()
        self._closed = True

    @property
    def active(self) -> bool:
        return self._active

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def write(self, data) -> bool:
        """Play float32 (or int16 bytes) mono samples; blocks to stay near real time."""
        if self._closed or not self._active:
            raise PortAudioError("stream is not active")
        if isinstance(data, (bytes, bytearray, memoryview)):
            pcm = bytes(data)
            n = len(pcm) // 2
        else:
            a = np.asarray(data, dtype=np.float32).reshape(-1)
            n = a.size
            pcm = (np.clip(a, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        if n == 0:
            return False
        now = time.monotonic()
        underflow = self._clock < now
        if underflow:
            self._clock = now
        # Silence (keepalive writes) is not sent: the robot's player needs no keepalive.
        if any(pcm):
            _player.push(pcm)
        self._clock += n / RATE
        ahead = self._clock - time.monotonic() - _LEAD_S
        if ahead > 0:
            time.sleep(ahead)
        return False


class BoosterSoundDevice:
    """The `sounddevice` module surface TTSService touches."""

    PortAudioError = PortAudioError
    OutputStream = BoosterOutputStream

    @staticmethod
    def query_devices(device=None, kind=None):
        info = {
            "name": "Booster K1 speaker", "default_samplerate": float(RATE),
            "max_output_channels": 1, "max_input_channels": 0,
            "default_high_output_latency": _LEAD_S, "default_low_output_latency": _LEAD_S,
        }
        return info if kind or device is not None else [info]

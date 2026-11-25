"""Booster K1 microphone, read through Booster's audio service (no device ownership).

Booster's `audio` service owns the K1 mic. Its SDK capture stream delivers
frames of interleaved int16 PCM at 16 kHz, 3 channels, ~682 samples per channel
(~43 ms). On firmware 1.7.1.1 all three channels carry identical samples, so
channel 0 is used. The echo-cancelled stream (`enable_naec_pcm`) is refused by
that firmware (RPC 2001), so only raw PCM is requested.

One Booster capture session is kept per process: VoiceService opens and closes
its mic many times per minute, and each Booster session costs several RPC
round trips. `BoosterMicStream` is a cheap reader over that shared session with
the same surface as `ArecordStream` (`__enter__`, `read`, `abort`, `__exit__`).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

SOURCE_RATE = 16000
# Bound the shared buffer: a reader that falls behind loses the oldest audio
# instead of growing memory without limit.
_MAX_BUFFER_S = 2.0
_READ_TIMEOUT_S = 2.0


class _BoosterCapture:
    """Process-wide Booster capture session feeding mono int16 samples to readers."""

    def __init__(self, np):
        self._np = np
        self._lock = threading.Condition()
        self._chunks: deque = deque()
        self._buffered = 0
        self._readers = 0
        self._manager = None
        self._stream = None
        self._error: str | None = None
        self._dropped = 0
        self._last_frame = 0.0

    # --- session ----------------------------------------------------------

    def acquire(self) -> None:
        with self._lock:
            self._readers += 1
            if self._stream is not None:
                return
            self._error = None
        try:
            self._open()
        except Exception:
            with self._lock:
                self._readers -= 1
            raise

    def release(self) -> None:
        with self._lock:
            self._readers = max(0, self._readers - 1)
            # Readers come and go between turns; the session stays open so the
            # next reader starts with zero RPC latency. close() ends it.
            self._lock.notify_all()

    def _open(self) -> None:
        import booster_robotics_sdk_python as sdk

        from hal.drivers.motors.booster_service import init_booster_channel

        init_booster_channel()
        manager = sdk.AudioManager()
        manager.Init()
        stream = manager.CreateCaptureStream()
        stream.SetFrameCallback(self._on_frame)
        stream.SetErrorCallback(self._on_error)
        options = sdk.AudioCaptureStreamOptions()
        stream.Init(options)
        stream.Start()
        with self._lock:
            self._manager, self._stream = manager, stream
            self._chunks.clear()
            self._buffered = 0
        logger.info("[booster-mic] capture session started")

    def close(self) -> None:
        with self._lock:
            stream, manager = self._stream, self._manager
            self._stream = self._manager = None
            self._chunks.clear()
            self._buffered = 0
            self._error = "closed"
            self._lock.notify_all()
        for fn in (getattr(stream, "Stop", None), getattr(stream, "Destroy", None),
                   getattr(manager, "Shutdown", None)):
            if fn is None:
                continue
            try:
                fn()
            except Exception as e:
                logger.debug("[booster-mic] close step failed: %s", e)

    # --- SDK callbacks (SDK thread) ----------------------------------------

    def _on_frame(self, frame) -> None:
        if not frame.raw_valid:
            return
        channels = max(1, int(frame.raw_format.channels))
        mono = self.to_mono(frame.raw_pcm, channels, self._np)
        with self._lock:
            self._chunks.append(mono)
            self._buffered += mono.size
            self._last_frame = time.monotonic()
            limit = int(_MAX_BUFFER_S * SOURCE_RATE)
            while self._buffered > limit and self._chunks:
                old = self._chunks.popleft()
                self._buffered -= old.size
                self._dropped += old.size
            self._lock.notify_all()

    def _on_error(self, err) -> None:
        msg = str(err)
        logger.warning("[booster-mic] capture error: %s", msg)
        with self._lock:
            self._error = msg
            self._lock.notify_all()

    @staticmethod
    def to_mono(pcm, channels: int, np):
        """Interleaved int16 samples (list or array) -> mono int16 (channel 0)."""
        data = np.asarray(pcm, dtype=np.int16)
        if channels > 1:
            data = data[: data.size - data.size % channels].reshape(-1, channels)[:, 0]
        return np.ascontiguousarray(data)

    # --- reader side -------------------------------------------------------

    def read(self, frames: int, aborted: threading.Event):
        """Block until `frames` mono samples are buffered; return them as int16."""
        np = self._np
        deadline = time.monotonic() + _READ_TIMEOUT_S
        with self._lock:
            while self._buffered < frames:
                if aborted.is_set():
                    raise IOError("booster mic read aborted")
                if self._error is not None:
                    raise IOError(f"booster mic capture failed: {self._error}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise IOError(f"booster mic: no audio for {_READ_TIMEOUT_S:.1f}s")
                self._lock.wait(min(remaining, 0.1))
            out = np.empty(frames, dtype=np.int16)
            filled = 0
            while filled < frames:
                chunk = self._chunks[0]
                take = min(frames - filled, chunk.size)
                out[filled:filled + take] = chunk[:take]
                filled += take
                if take == chunk.size:
                    self._chunks.popleft()
                else:
                    self._chunks[0] = chunk[take:]
                self._buffered -= take
            return out

    def drain(self) -> None:
        """Drop buffered audio so a new reader starts at 'now', not at stale audio."""
        with self._lock:
            self._chunks.clear()
            self._buffered = 0


_shared: _BoosterCapture | None = None
_shared_lock = threading.Lock()


def shared_capture(np) -> _BoosterCapture:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = _BoosterCapture(np)
        return _shared


class BoosterMicStream:
    """Drop-in for ArecordStream: mono int16 at 16 kHz from the K1 microphone."""

    def __init__(self, rate: int, channels: int, blocksize: int, np, *, low_latency: bool = False):
        if rate != SOURCE_RATE:
            raise ValueError(f"booster mic delivers {SOURCE_RATE} Hz, asked for {rate} Hz")
        if channels != 1:
            raise ValueError("booster mic stream is mono")
        self._np = np
        self._blocksize = blocksize
        self._capture = shared_capture(np)
        self._aborted = threading.Event()
        self._open = False

    def __enter__(self):
        self._capture.acquire()
        self._capture.drain()
        self._open = True
        return self

    def abort(self):
        """Unblock a concurrent read."""
        self._aborted.set()
        with self._capture._lock:
            self._capture._lock.notify_all()

    def __exit__(self, *args):
        self.abort()
        if self._open:
            self._open = False
            self._capture.release()

    def read(self, frames):
        if not self._open:
            raise IOError("booster mic stream is closed")
        data = self._capture.read(frames, self._aborted)
        return data.reshape(frames, 1), False

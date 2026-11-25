"""Camera driver for the Booster K1 head camera, read from Booster's video stream.

The K1 exposes no V4L2 device. Booster's `booster_camera` service publishes the
head camera as JPEG on the DDS topic `rt/booster_video_stream` (~10 Hz,
544x306 on firmware 1.7.1.1). This driver subscribes through Booster's SDK, so
HAL never takes ownership of the sensor and Booster's own services keep it.

Works on the robot and from a dev machine on the K1 Ethernet link, provided the
K1's DDS profiles whitelist that link (robots/booster-k1/docs/runtime.md).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import override

import numpy as np
import numpy.typing as npt

from .models import VideoCaptureDeviceInfo, VideoCaptureDeviceResponse
from .video_capture_device import VideoCaptureDeviceBase

logger = logging.getLogger(__name__)

_TOPIC = os.environ.get("BOOSTER_VIDEO_TOPIC", "rt/booster_video_stream")


def _init_channel_factory() -> None:
    """Initialise Booster's DDS participant once per process (shared with the head driver)."""
    from hal.drivers.motors.booster_service import init_booster_channel

    init_booster_channel()


class BoosterVideoCaptureDevice(VideoCaptureDeviceBase):
    """JPEG frames from Booster's video stream, decoded on arrival."""

    runable: bool = True
    requires_v4l2_index: bool = False

    def __init__(self, device_info: VideoCaptureDeviceInfo, name: str | None = None):
        super().__init__(device_info, name)
        self._lock = threading.Lock()
        self._last_response: VideoCaptureDeviceResponse | None = None
        self._last_frame_monotonic = 0.0
        self._sub = None
        self.zoom: float = 1.0
        self.actual_width: int | None = None
        self.actual_height: int | None = None
        self.actual_fps: float | None = None
        self._frames = 0
        self._first_frame_monotonic = 0.0

    # --- frame access (same surface as the other capture devices) ---------

    @property
    def last_frame(self) -> npt.NDArray[np.uint8] | None:
        with self._lock:
            if self._last_response and self._last_response.frame is not None:
                return self._last_response.frame.copy()
            return None

    @property
    def last_frame_ts(self) -> float:
        with self._lock:
            return self._last_frame_monotonic

    @property
    def last_frame_description(self) -> str | None:
        with self._lock:
            return self._last_response.frame_description if self._last_response else None

    @property
    def last_response(self) -> VideoCaptureDeviceResponse | None:
        with self._lock:
            return self._last_response.model_copy(deep=True) if self._last_response else None

    # Booster's stream has a fixed rate; consumer counting has nothing to throttle.
    def acquire_consumer(self) -> None:
        pass

    def release_consumer(self) -> None:
        pass

    @override
    def capture(self, need_description: bool = False) -> VideoCaptureDeviceResponse | None:
        if self._sub is None:
            raise RuntimeError(f"{self.__class__.__name__} has not started")
        return self.last_response

    # --- lifecycle --------------------------------------------------------

    @override
    def start(self) -> None:
        if self._sub is not None:
            return
        import booster_robotics_sdk_python as sdk

        _init_channel_factory()
        self._sub = sdk.CompressedImageSubscriber(self._on_message, _TOPIC)
        self._sub.InitChannel()
        self.running = True
        logger.info("[booster-camera] subscribed to %s", _TOPIC)

    @override
    def stop(self) -> None:
        super().stop()
        sub, self._sub = self._sub, None
        if sub is not None:
            try:
                sub.CloseChannel()
            except Exception as e:
                logger.warning("[booster-camera] CloseChannel failed: %s", e)

    # --- internals --------------------------------------------------------

    def _on_message(self, msg) -> None:
        """SDK callback thread: decode one JPEG and publish it as the latest frame."""
        frame = self.decode(msg.format, msg.data)
        if frame is None:
            return
        if self.zoom and self.zoom > 1.0:
            frame = self._apply_zoom(frame, self.zoom)
        now = time.monotonic()
        with self._lock:
            self._last_response = VideoCaptureDeviceResponse(frame=frame)
            self._last_frame_monotonic = now
        self.actual_height, self.actual_width = frame.shape[:2]
        self._frames += 1
        if self._frames == 1:
            self._first_frame_monotonic = now
            logger.info("[booster-camera] first frame %dx%d", self.actual_width, self.actual_height)
        elif now > self._first_frame_monotonic:
            self.actual_fps = (self._frames - 1) / (now - self._first_frame_monotonic)

    @staticmethod
    def decode(fmt: str, data) -> npt.NDArray[np.uint8] | None:
        """JPEG bytes (or the SDK's list of ints) to a BGR frame, or None."""
        import cv2

        if "jp" not in (fmt or "").lower():
            logger.debug("[booster-camera] unsupported format %r", fmt)
            return None
        buf = np.frombuffer(bytes(data), dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            logger.debug("[booster-camera] JPEG decode failed (%d bytes)", buf.size)
        return frame

    @staticmethod
    def _apply_zoom(frame: npt.NDArray[np.uint8], zoom: float) -> npt.NDArray[np.uint8]:
        import cv2

        h, w = frame.shape[:2]
        ch, cw = int(h / zoom), int(w / zoom)
        y0, x0 = (h - ch) // 2, (w - cw) // 2
        return cv2.resize(frame[y0:y0 + ch, x0:x0 + cw], (w, h))

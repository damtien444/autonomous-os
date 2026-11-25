"""Booster K1 camera driver: JPEG decode and latest-frame publishing (no SDK needed)."""
import os
import sys
import unittest

import cv2
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hal.drivers.camera.booster_capture_device import BoosterVideoCaptureDevice  # noqa: E402
from hal.drivers.camera.factory import resolve_camera_class  # noqa: E402
from hal.drivers.camera.models import VideoCaptureDeviceInfo  # noqa: E402


class _Msg:
    def __init__(self, fmt, data):
        self.format = fmt
        self.data = data


def _jpeg(w=544, h=306):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, : w // 2] = (0, 0, 255)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


class TestBoosterCamera(unittest.TestCase):
    def _dev(self):
        return BoosterVideoCaptureDevice(VideoCaptureDeviceInfo(device_id="booster"))

    def test_factory_resolves_booster(self):
        self.assertIs(resolve_camera_class("booster", required=True), BoosterVideoCaptureDevice)

    def test_decodes_sdk_list_payload(self):
        # The SDK hands data over as a list of ints, not bytes.
        frame = BoosterVideoCaptureDevice.decode("jpeg", list(_jpeg()))
        self.assertEqual(frame.shape, (306, 544, 3))

    def test_rejects_non_jpeg_and_garbage(self):
        self.assertIsNone(BoosterVideoCaptureDevice.decode("h264", list(_jpeg())))
        self.assertIsNone(BoosterVideoCaptureDevice.decode("jpeg", [1, 2, 3]))

    def test_message_becomes_last_frame(self):
        dev = self._dev()
        self.assertIsNone(dev.last_frame)
        dev._on_message(_Msg("jpeg", list(_jpeg())))
        self.assertEqual(dev.last_frame.shape, (306, 544, 3))
        self.assertGreater(dev.last_frame_ts, 0)
        self.assertEqual((dev.actual_width, dev.actual_height), (544, 306))

    def test_capture_before_start_raises(self):
        with self.assertRaises(RuntimeError):
            self._dev().capture()


if __name__ == "__main__":
    unittest.main()

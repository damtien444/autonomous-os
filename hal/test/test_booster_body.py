"""Booster K1 body: arm gestures and bounded walking always end safely."""
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hal.drivers.motors import booster_service as bs  # noqa: E402
from hal.drivers.motors.booster_service import BoosterBody  # noqa: E402


class FakeClient:
    def __init__(self, mode="kWalking", fail_move=False):
        self.calls = []
        self._mode = mode
        self._fail_move = fail_move

    def GetMode(self):
        return SimpleNamespace(mode=SimpleNamespace(name=self._mode))

    def Move(self, vx, vy, vyaw):
        self.calls.append(("Move", round(vx, 3), round(vy, 3), round(vyaw, 3)))
        if self._fail_move and (vx, vy, vyaw) != (0.0, 0.0, 0.0):
            raise RuntimeError("API call failed, code = 400")

    def WaveHand(self, a):
        self.calls.append(("WaveHand", a))

    def Handshake(self, a):
        self.calls.append(("Handshake", a))

    def HandOnChestGreeting(self, up):
        self.calls.append(("HandOnChestGreeting", up))


SDK = SimpleNamespace(HandAction=SimpleNamespace(kHandOpen="open", kHandClose="close"))


class TestBoosterBody(unittest.TestCase):
    def test_walk_is_clamped_and_always_ends_at_zero(self):
        c = FakeClient()
        applied = BoosterBody(c, SDK).walk(5.0, -5.0, 9.0, 0.25)
        self.assertEqual(applied["vx"], bs.WALK_MAX_VX)
        self.assertEqual(applied["vy"], -bs.WALK_MAX_VX)
        self.assertEqual(applied["vyaw"], bs.WALK_MAX_VYAW)
        moving = [x for x in c.calls if x[0] == "Move" and x[1:] != (0.0, 0.0, 0.0)]
        self.assertTrue(moving)
        self.assertEqual(c.calls[-1], ("Move", 0.0, 0.0, 0.0))

    def test_walk_duration_is_capped(self):
        c = FakeClient()
        t = time.monotonic()
        applied = BoosterBody(c, SDK).walk(0.1, 0, 0, 999)
        self.assertEqual(applied["seconds"], bs.WALK_MAX_S)
        self.assertLess(time.monotonic() - t, bs.WALK_MAX_S + 1.0)

    def test_walk_error_still_sends_stop(self):
        c = FakeClient(fail_move=True)
        with self.assertRaises(RuntimeError):
            BoosterBody(c, SDK).walk(0.2, 0, 0, 1.0)
        self.assertEqual(c.calls[-1], ("Move", 0.0, 0.0, 0.0))

    def test_stop_cancels_a_walk_in_progress(self):
        c = FakeClient()
        body = BoosterBody(c, SDK)
        t = threading.Thread(target=body.walk, args=(0.2, 0, 0, 3.0))
        start = time.monotonic()
        t.start()
        time.sleep(0.3)
        body.stop_walking()
        t.join(2.0)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertEqual(c.calls[-1], ("Move", 0.0, 0.0, 0.0))

    def test_refused_outside_walking_mode(self):
        c = FakeClient(mode="kDamping")
        body = BoosterBody(c, SDK)
        with self.assertRaises(RuntimeError):
            body.walk(0.2, 0, 0, 1.0)
        with self.assertRaises(RuntimeError):
            body.gesture("wave", 0.5)
        self.assertFalse([x for x in c.calls if x[0] != "Move"])

    def test_gesture_starts_and_ends(self):
        c = FakeClient()
        BoosterBody(c, SDK).gesture("wave", 0.5)
        self.assertEqual(c.calls, [("WaveHand", "open"), ("WaveHand", "close")])
        c2 = FakeClient()
        BoosterBody(c2, SDK).gesture("greet", 0.5)
        self.assertEqual(c2.calls, [("HandOnChestGreeting", True), ("HandOnChestGreeting", False)])

    def test_unknown_gesture_rejected(self):
        with self.assertRaises(ValueError):
            BoosterBody(FakeClient(), SDK).gesture("backflip", 0.5)


if __name__ == "__main__":
    unittest.main()

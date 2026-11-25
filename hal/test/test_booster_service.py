"""Booster K1 head driver: clamps, speed ceiling, halt, and no non-head SDK calls."""
import os
import sys
import time
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import hal.presets as P  # noqa: E402
from hal.drivers.motors.booster_service import (  # noqa: E402
    PITCH, YAW, BoosterHeadMotionService,
)
from hal.safety.policy import MotionBounds, SafetyPolicy  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeHead:
    """Stands in for _BoosterHead; records every absolute target written."""

    def __init__(self):
        self.writes = []

    def rotate(self, yaw_deg, pitch_deg):
        self.writes.append((time.monotonic(), yaw_deg, pitch_deg))


def _svc(max_speed=None, limits=None):
    policy = SafetyPolicy(schema="autonomous.safety.v1", motion=MotionBounds(
        max_speed=max_speed, stop_always=True, joint_limits=limits))
    head = FakeHead()
    svc = BoosterHeadMotionService(safety_policy=policy, head=head)
    svc.start()
    return svc, head


class TestBoosterHead(unittest.TestCase):
    def test_declared_joint_limits_clamp_every_write(self):
        svc, head = _svc(limits={"head_yaw": (-30.0, 30.0), "head_pitch": (-10.0, 10.0)})
        svc.send_positions({YAW: 90.0, PITCH: -50.0})
        _, yaw, pitch = head.writes[-1]
        self.assertEqual((yaw, pitch), (30.0, -10.0))

    def test_glide_respects_max_speed(self):
        svc, head = _svc(max_speed=100)
        t0 = time.monotonic()
        svc.move_to({YAW: 20.0}, duration=0.01)   # asks for ~2000 deg/s
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.18)     # 20 deg at 100 deg/s = 0.2 s
        self.assertAlmostEqual(head.writes[-1][1], 20.0)
        for (t_a, y_a, _), (t_b, y_b, _) in zip(head.writes, head.writes[1:]):
            # 25 Hz steps of 4 deg; never a jump to the target.
            self.assertLessEqual(abs(y_b - y_a), 4.01)

    def test_nudge_is_relative_and_clamped(self):
        svc, _ = _svc(limits={"head_yaw": (-30.0, 30.0), "head_pitch": (-10.0, 10.0)})
        svc.send_positions({YAW: 25.0, PITCH: 0.0})
        final = svc.nudge(20.0, 5.0, 0.15, svc.get_positions(), None)
        self.assertEqual(final, {YAW: 30.0, PITCH: 5.0})
        self.assertEqual(svc.get_positions(), final)

    def test_aim_preset(self):
        svc, _ = _svc()
        final = svc.aim(P.AIM_LEFT, 0.15, svc.get_positions(), None)
        self.assertEqual(final[YAW], -45.0)  # HAL: negative yaw = left

    def test_halt_stops_a_gesture_and_holds(self):
        svc, head = _svc(max_speed=30)
        svc.dispatch(P.SERVO_CMD_PLAY, P.SERVO_SCANNING)
        time.sleep(0.15)
        svc.halt()
        n = len(head.writes)
        here = svc.get_positions()
        time.sleep(0.2)
        self.assertEqual(len(head.writes), n, "gesture kept streaming after halt")
        self.assertEqual(head.writes[-1][1:], (here[YAW], here[PITCH]))

    def test_release_centers_and_marks_released(self):
        svc, head = _svc()
        svc.send_positions({YAW: 10.0, PITCH: 5.0})
        self.assertEqual(svc.release(), {})
        self.assertEqual(head.writes[-1][1:], (0.0, 0.0))
        self.assertEqual(svc.motion_mode, "released")
        svc.dispatch(P.SERVO_CMD_PLAY, P.SERVO_NOD)
        self.assertIsNone(svc._current_recording)
        svc.resume()
        self.assertIsNone(svc.motion_mode)

    def test_driver_never_calls_non_head_sdk_apis(self):
        path = os.path.join(ROOT, "hal", "drivers", "motors", "booster_service.py")
        src = open(path, encoding="utf-8").read()
        # Walking (Move) and built-in arm gestures are allowed through BoosterBody;
        # mode changes, get-up/lie-down, torque and raw joint control never are.
        for api in ("MoveCommand(", "ChangeMode(", "LieDown(", "GetUp(",
                    "ZeroTorqueDrag(", "MoveHandEndEffector", "LowCmd", "Dance("):
            self.assertNotIn(api, src, f"head-only driver must not call {api}")

    def test_satisfies_motion_service_protocol(self):
        from hal.drivers.motors.base import MotionService
        svc, _ = _svc()
        self.assertIsInstance(svc, MotionService)


if __name__ == "__main__":
    unittest.main()

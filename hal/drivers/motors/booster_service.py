"""Booster K1 head driver — wraps Booster's `booster_robotics_sdk_python`.

Scope: the 2-DOF head (yaw, pitch), plus Booster's built-in arm gestures and
short bounded walking steps through `BoosterBody` (see below). This driver never
calls a mode-change, get-up, lie-down, torque or raw joint API: the K1 stands and
balances under Booster's own controller, and nothing here may change that.

Every loco-client call goes through `_BoosterHead`. SDK names verified against
`booster-robotics-sdk-python` 1.6.5 and a K1 on firmware 1.7.1.1.

Positions are the last commanded target, not a measurement: the low-state
subscriber's head-joint indices are unverified on hardware, so the driver does
not claim to read them yet.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import hal.presets as P

logger = logging.getLogger(__name__)

YAW = "head_yaw.pos"
PITCH = "head_pitch.pos"
JOINT_KEYS: Set[str] = {YAW, PITCH}

# Fallback head range in degrees, used when SAFETY.md declares no joint_limits.
# HAL convention (Lamp presets, /servo/nudge): -yaw = left, +pitch = up. Third-party published K1 limits are
# yaw ±90 and pitch -40..+30; these defaults stay inside them. Verify on hardware.
_DEFAULT_LIMITS: Dict[str, Tuple[float, float]] = {
    YAW: (-80.0, 80.0),
    PITCH: (-35.0, 25.0),
}

# The SDK's RotateHead(pitch=+) looks DOWN and (yaw=+) looks LEFT (REP-103);
# HAL's +pitch looks up and +yaw looks right. Flip both on the wire.
# Both signs verified on a K1 (2026-11-25): HAL yaw=-10 turned the head left,
# HAL pitch=+15 tilted it up.
_PITCH_SIGN = float(os.environ.get("HAL_BOOSTER_PITCH_SIGN", "-1"))
_YAW_SIGN = float(os.environ.get("HAL_BOOSTER_YAW_SIGN", "-1"))

_MIN_MOVE_DURATION_S = 0.15

_AIM_TARGETS: Dict[str, Dict[str, float]] = {
    P.AIM_CENTER: {YAW: 0.0, PITCH: 0.0},
    P.AIM_DESK:   {YAW: 0.0, PITCH: -30.0},
    P.AIM_WALL:   {YAW: 0.0, PITCH: 15.0},
    P.AIM_LEFT:   {YAW: -45.0},
    P.AIM_RIGHT:  {YAW: 45.0},
    P.AIM_UP:     {PITCH: 20.0},
    P.AIM_DOWN:   {PITCH: -30.0},
    P.AIM_USER:   {YAW: 0.0, PITCH: 10.0},
}

# Small head-only gestures, as (yaw_deg, pitch_deg, seconds-to-reach) keyframes
# relative to where the head already is. Amplitudes are kept small on purpose:
# a glance or a nod, never a sweep.
_GESTURES: Dict[str, List[Tuple[float, float, float]]] = {
    P.SERVO_NOD:         [(0, -10, 0.25), (0, 4, 0.25), (0, -6, 0.2), (0, 0, 0.25)],
    P.SERVO_ACKNOWLEDGE: [(0, -7, 0.25), (0, 0, 0.3)],
    P.SERVO_HEADSHAKE:   [(12, 0, 0.25), (-12, 0, 0.35), (8, 0, 0.3), (0, 0, 0.25)],
    P.SERVO_CURIOUS:     [(8, 6, 0.5), (8, 6, 0.6), (0, 0, 0.5)],
    P.SERVO_LISTENING:   [(0, 5, 0.5)],
    P.SERVO_GREETING:    [(0, 6, 0.3), (0, -6, 0.3), (0, 0, 0.3)],
    P.SERVO_SCANNING:    [(20, 0, 0.8), (20, 0, 0.4), (-20, 0, 1.2), (-20, 0, 0.4), (0, 0, 0.8)],
}
# `idle` is not a gesture here: HAL plays it at the end of every voice turn, and a
# head move toward "where the head already is" would cancel a glance the person just
# asked for. The K1 head simply stays where it was last put.
_NO_OP_RECORDINGS = frozenset({P.SERVO_IDLE})


_channel_lock = threading.Lock()
_channel_ready = False


def init_booster_channel() -> None:
    """Initialise Booster's DDS participant once per process.

    Shared by the head driver and the camera driver: the SDK allows one
    ChannelFactory.Init per process. BOOSTER_NET_IFACE names the interface that
    reaches the robot's services (a NIC name or address; empty = SDK default).
    """
    global _channel_ready
    with _channel_lock:
        if _channel_ready:
            return
        import booster_robotics_sdk_python as sdk  # imported lazily: optional extra

        domain = int(os.getenv("BOOSTER_DDS_DOMAIN", "0"))
        iface = os.getenv("BOOSTER_NET_IFACE", "")
        sdk.ChannelFactory.Instance().Init(domain, iface)
        _channel_ready = True
        logger.info("[booster] DDS channel ready (domain %s, iface %r)", domain, iface)


class _BoosterHead:
    """The only place that touches the Booster loco client."""

    def __init__(self, wait_ms: int):
        import booster_robotics_sdk_python as sdk

        init_booster_channel()
        self._client = sdk.B1LocoClient()
        self._client.Init()
        if not self._client.WaitForService(wait_ms):
            raise RuntimeError(
                f"Booster loco service not reachable within {wait_ms} ms "
                "(check BOOSTER_NET_IFACE and the K1 DDS whitelist)"
            )
        info = self._client.GetRobotInfo()
        logger.info("[booster] connected to %s (%s), firmware %s",
                    info.nickname, info.model, info.version)
        self.body = BoosterBody(self._client, sdk)

    def mode(self) -> str:
        """Current RobotMode name, e.g. 'kDamping' (read-only)."""
        return self._client.GetMode().mode.name

    def rotate(self, yaw_deg: float, pitch_deg: float) -> None:
        """Absolute head target in HAL degrees; Booster interpolates on the robot.

        The binding raises RuntimeError on a refused or lost request.
        """
        self._client.RotateHead(
            math.radians(pitch_deg) * _PITCH_SIGN, math.radians(yaw_deg) * _YAW_SIGN,
        )


# Built-in Booster arm gestures, by HAL name: (start_call, stop_call, hold_s).
# Each is a firmware animation; the robot keeps balancing while it plays.
ARM_GESTURES = ("wave", "handshake", "greet")

# Bounded walking: every step is a velocity held for a fixed time, then zero.
# Booster clamps velocities by mode and model; these caps are tighter still.
WALK_MAX_VX = float(os.environ.get("HAL_BOOSTER_WALK_MAX_VX", "0.3"))      # m/s
WALK_MAX_VYAW = float(os.environ.get("HAL_BOOSTER_WALK_MAX_VYAW", "0.6"))  # rad/s
WALK_MAX_S = float(os.environ.get("HAL_BOOSTER_WALK_MAX_S", "3.0"))        # per command


class BoosterBody:
    """Arm gestures and bounded walking on the K1, through Booster's loco client.

    Only usable while the robot is in walking mode (Booster rejects these in
    damping/prepare). Every walk command is time-boxed and always ends with a
    zero-velocity command, also when cancelled or on error.
    """

    def __init__(self, client, sdk):
        self._c = client
        self._sdk = sdk
        self._lock = threading.Lock()
        self._walk_gen = 0

    def mode(self) -> str:
        return self._c.GetMode().mode.name

    def _require_walking(self) -> None:
        mode = self.mode()
        if mode != "kWalking":
            raise RuntimeError(f"robot is in {mode}; stand it up (walking mode) first")

    def gesture(self, name: str, hold_s: float = 3.0) -> None:
        """Play a built-in arm gesture, blocking for its duration."""
        sdk = self._sdk
        self._require_walking()
        if name == "wave":
            start = lambda: self._c.WaveHand(sdk.HandAction.kHandOpen)
            stop = lambda: self._c.WaveHand(sdk.HandAction.kHandClose)
        elif name == "handshake":
            start = lambda: self._c.Handshake(sdk.HandAction.kHandOpen)
            stop = lambda: self._c.Handshake(sdk.HandAction.kHandClose)
        elif name == "greet":
            start = lambda: self._c.HandOnChestGreeting(True)
            stop = lambda: self._c.HandOnChestGreeting(False)
        else:
            raise ValueError(f"unknown arm gesture {name!r} (known: {', '.join(ARM_GESTURES)})")
        with self._lock:
            start()
        try:
            time.sleep(max(0.5, min(hold_s, 8.0)))
        finally:
            with self._lock:
                stop()

    def walk(self, vx: float, vy: float, vyaw: float, seconds: float) -> dict:
        """Walk at a clamped velocity for a bounded time, then stop. Blocks."""
        self._require_walking()
        vx = max(-WALK_MAX_VX, min(WALK_MAX_VX, vx))
        vy = max(-WALK_MAX_VX, min(WALK_MAX_VX, vy))
        vyaw = max(-WALK_MAX_VYAW, min(WALK_MAX_VYAW, vyaw))
        seconds = max(0.0, min(WALK_MAX_S, seconds))
        with self._lock:
            self._walk_gen += 1
            gen = self._walk_gen
        try:
            end = time.monotonic() + seconds
            # Re-send at 10 Hz: Booster treats a stale velocity command as stop.
            while time.monotonic() < end and gen == self._walk_gen:
                with self._lock:
                    self._c.Move(vx, vy, vyaw)
                time.sleep(0.1)
        finally:
            self.stop_walking()
        return {"vx": vx, "vy": vy, "vyaw": vyaw, "seconds": seconds}

    def stop_walking(self) -> None:
        """Zero velocity now. Never gated: always safe to call."""
        with self._lock:
            self._walk_gen += 1
            try:
                self._c.Move(0.0, 0.0, 0.0)
            except Exception as e:
                logger.warning("[booster] stop-walk command failed: %s", e)


class BoosterHeadMotionService:
    """MotionService for the Booster K1 head (yaw + pitch)."""

    def __init__(self, safety_policy: Any = None, head: Any = None):
        self._safety_policy = safety_policy
        self._head = head                     # tests inject a fake
        self._wait_ms = int(os.getenv("BOOSTER_WAIT_SERVICE_MS", "10000"))
        self._limits = dict(_DEFAULT_LIMITS)
        bounds = getattr(safety_policy, "motion", None)
        declared = getattr(bounds, "joint_limits", None) or {}
        for key, name in ((YAW, "head_yaw"), (PITCH, "head_pitch")):
            if name in declared:
                self._limits[key] = declared[name]
        self._max_speed = getattr(bounds, "max_speed", None)

        self._lock = threading.RLock()
        self._target: Dict[str, float] = {YAW: 0.0, PITCH: 0.0}
        self._gen = 0                         # bumped by every new command; stale moves stop
        self._move_thread: Optional[threading.Thread] = None
        self._current_recording: Optional[str] = None
        self._music_playing = False
        self._released = False
        self._zero_mode = False
        self._hold_mode = False
        self._hold_explicit = False
        self._frozen = False
        self._tracking_flag = False
        self._body_owners = 0

    # --- lifecycle -------------------------------------------------------

    def start(self, skip_wake: bool = False) -> None:
        with self._lock:
            if self._head is None:
                self._head = _BoosterHead(self._wait_ms)
        # No wake performance: the head stays wherever Booster left it until asked.
        logger.info("[booster] head driver ready (max_speed %s)", self._max_speed)

    def stop(self, timeout: float = 5.0) -> None:
        self._music_playing = False
        self._take()
        t = self._move_thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        self._head = None

    @property
    def is_connected(self) -> bool:
        return self._head is not None

    @property
    def body(self) -> Optional[BoosterBody]:
        """Arm gestures + bounded walking, or None before start / in tests."""
        return getattr(self._head, "body", None)

    def ensure_running(self) -> None:
        pass

    # --- modes -----------------------------------------------------------

    @property
    def is_suppressed(self) -> bool:
        return self._zero_mode or self._hold_mode or self._released

    @property
    def motion_mode(self) -> Optional[str]:
        if self._released:
            return "released"
        if self._zero_mode:
            return "zero"
        if self._hold_mode:
            return "hold"
        return None

    def freeze(self) -> None:
        self._frozen = True

    def unfreeze(self) -> None:
        self._frozen = False

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    @property
    def _tracking_active(self) -> bool:
        return self._tracking_flag or self._body_owners > 0

    @_tracking_active.setter
    def _tracking_active(self, value: bool) -> None:
        self._tracking_flag = bool(value)

    def acquire_body(self) -> None:
        with self._lock:
            self._body_owners += 1

    def release_body(self) -> None:
        with self._lock:
            self._body_owners = max(0, self._body_owners - 1)

    # --- joints ----------------------------------------------------------

    def get_joint_names(self) -> Set[str]:
        return set(JOINT_KEYS)

    def get_positions(self) -> Dict[str, float]:
        return dict(self._target)

    def joint_status(self) -> Dict[str, dict]:
        online = self.is_connected
        return {
            "head_yaw": {"online": online, "id": 1, "angle": round(self._target[YAW], 1)},
            "head_pitch": {"online": online, "id": 2, "angle": round(self._target[PITCH], 1)},
        }

    def send_positions(self, positions: Dict[str, float]) -> None:
        """Tracker streaming path: one absolute target per call, no interpolation here."""
        self._take()
        self._write({**self._target, **positions})

    def move_to(self, target_positions: Dict[str, float], duration: float = 2.0) -> None:
        gen = self._take()
        self._glide({**self._target, **target_positions}, duration, gen)

    def move_and_hold(self, target_positions: Dict[str, float], duration: float = 2.0) -> None:
        self.move_to(target_positions, duration)

    def aim(self, direction: str, duration: float, current_positions: Dict[str, float],
            safety_policy: Any) -> Dict[str, float]:
        target = _AIM_TARGETS.get(direction)
        if target is None:
            logger.warning("[booster] unknown aim direction %r — centering", direction)
            target = _AIM_TARGETS[P.AIM_CENTER]
        final = self._clamp({**self._target, **target})
        self.move_to(final, duration)
        return final

    def nudge(self, yaw: float, pitch: float, duration: float,
              current_positions: Dict[str, float],
              safety_policy: Any) -> Dict[str, float]:
        base = {**self._target, **{k: v for k, v in current_positions.items() if k in JOINT_KEYS}}
        final = self._clamp({YAW: base[YAW] + yaw, PITCH: base[PITCH] + pitch})
        self.move_to(final, duration)
        return final

    # --- recovery (never safety-gated) -----------------------------------

    def halt(self) -> None:
        """Stop gliding, stop any walking step, and pin the head at the last target."""
        self._take()
        body = self.body
        if body is not None:
            body.stop_walking()
        try:
            self._write(dict(self._target))
        except Exception as e:
            logger.warning("[booster] halt could not re-pin the head: %s", e)
        logger.info("[booster] head halted")

    def zero_pose(self) -> None:
        self._zero_mode = True
        gen = self._take()
        self._glide({YAW: 0.0, PITCH: 0.0}, 1.5, gen)

    def release(self) -> Dict[str, str]:
        """Center the head. Leg/body torque is Booster's and is never touched here."""
        errors: Dict[str, str] = {}
        gen = self._take()
        try:
            self._glide({YAW: 0.0, PITCH: 0.0}, 1.5, gen)
        except Exception as e:
            errors["head"] = str(e)
        self._released = True
        return errors

    def resume(self) -> None:
        self._zero_mode = False
        self._hold_mode = False
        self._hold_explicit = False
        self._released = False

    def hold(self, explicit: bool = False) -> None:
        self._hold_mode = True
        self._hold_explicit = explicit
        self._take()

    # --- gestures --------------------------------------------------------

    def dispatch(self, event_type: str, payload: Any) -> None:
        if event_type == P.SERVO_CMD_PLAY and payload:
            self._play(str(payload))
        elif event_type == P.SERVO_CMD_MUSIC_STOP:
            self._music_playing = False
            self._take()
        else:
            logger.debug("[booster] dispatch %s ignored", event_type)

    def get_available_recordings(self) -> List[str]:
        return sorted(set(_GESTURES) | _NO_OP_RECORDINGS)

    def add_recording(self, name: str, actions: List[Dict[str, float]]) -> None:
        logger.warning("[booster] add_recording('%s') not supported on this backend", name)

    def _play(self, name: str) -> None:
        if name in _NO_OP_RECORDINGS:
            return  # never interrupts a head move in flight (see _NO_OP_RECORDINGS)
        frames = _GESTURES.get(name)
        if frames is None:
            logger.info("[booster] no head gesture for '%s' — skipped", name)
            return
        if self._released or self._frozen:
            logger.info("[booster] gesture '%s' refused (%s)", name, self.motion_mode or "frozen")
            return
        gen = self._take()
        origin = dict(self._target)

        def _run():
            self._current_recording = name
            try:
                for dyaw, dpitch, secs in frames:
                    if gen != self._gen:
                        return
                    self._glide({YAW: origin[YAW] + dyaw, PITCH: origin[PITCH] + dpitch}, secs, gen)
            except Exception as e:
                logger.warning("[booster] gesture '%s' failed: %s", name, e)
            finally:
                if gen == self._gen:
                    self._current_recording = None

        self._move_thread = threading.Thread(target=_run, daemon=True, name="booster-gesture")
        self._move_thread.start()

    # --- internals -------------------------------------------------------

    def _take(self) -> int:
        """Claim the head for a new command; any glide or gesture in flight stops."""
        with self._lock:
            self._gen += 1
            self._current_recording = None
            return self._gen

    def _clamp(self, positions: Dict[str, float]) -> Dict[str, float]:
        out = {}
        for key in JOINT_KEYS:
            lo, hi = self._limits[key]
            out[key] = min(hi, max(lo, float(positions.get(key, self._target[key]))))
        return out

    def _duration(self, target: Dict[str, float], requested: float) -> float:
        """Stretch so the fastest joint stays within motion.max_speed."""
        from hal.safety.policy import min_move_duration

        eff = min_move_duration(self._safety_policy, target, self._target, requested)
        return max(eff, _MIN_MOVE_DURATION_S)

    def _write(self, positions: Dict[str, float]) -> None:
        target = self._clamp(positions)
        with self._lock:
            if self._head is None:
                raise RuntimeError("Booster head not connected")
            self._head.rotate(target[YAW], target[PITCH])
            self._target = target

    def _glide(self, positions: Dict[str, float], duration: float, gen: int) -> None:
        """Step toward `positions` at <= max_speed, stopping if superseded.

        RotateHead has no duration argument in the Python binding, so the speed
        ceiling is enforced here by streaming intermediate targets at ~25 Hz.
        """
        target = self._clamp(positions)
        start = dict(self._target)
        secs = self._duration(target, duration)
        steps = max(1, int(secs * 25))
        t0 = time.monotonic()
        for i in range(1, steps + 1):
            if gen != self._gen:
                return
            a = i / steps
            self._write({k: start[k] + (target[k] - start[k]) * a for k in JOINT_KEYS})
            sleep = t0 + secs * a - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)

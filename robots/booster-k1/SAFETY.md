---
schema: autonomous.safety.v1

# Booster K1 head bounds. PROVISIONAL until measured on a K1. Only the head is
# driven by Autonomous; balance, legs and arms stay under Booster's controller.

motion:
  max_speed: 150             # deg/s head ceiling; the driver stretches every glide so neither joint exceeds it
  stop_always: true          # stop/halt/release are never gated
  # deg the head may be commanded to; HAL convention -yaw = left, +pitch = up.
  # Inside the published K1 range (yaw ±90, pitch -40..+30) with margin.
  joint_limits:
    head_yaw:   [-80, 80]
    head_pitch: [-35, 25]

#thermal:
#  max_temp_c: 85            # (reserved) measure the K1's Jetson trip points first
---

# SAFETY.md: Booster K1

Safety sits below the brain: it is enforced in HAL by the OS, not by prompting
the agent.

## motion

- Autonomous commands **only the head**. The driver
  (`hal/drivers/motors/booster_service.py`) calls only the SDK's head command,
  never locomotion, arm, mode-change, get-up, lie-down or torque APIs.
- `max_speed` is enforced in the driver: every move is streamed as small
  steps at about 25 Hz, so the head never turns faster than the ceiling, whatever the
  requested duration.
- `joint_limits` clamps every head target, whoever asked (agent, tracker,
  web).
- `/servo/stop` (halt) stops any glide or gesture and pins the head where it is.
- `/servo/release` centers the head. It does **not** cut torque: a humanoid that
  loses torque falls. The real e-stop is Booster's own remote or damping mode.
- **Where 150 comes from:** 90 deg/s (the first placeholder) made a 30 deg
  glance take ~0.35 s and read as sluggish on the robot (2026-11-26). 150 keeps a
  glance at ~0.2 s, well under the URDF joint velocity limit (18 rad/s). It is
  still not a hardware measurement.

## autonomy

- Unprompted head motion stays small: a glance, not a room scan.
- On network loss: stop agent-driven tracking and start no new agent motion.
  Booster keeps the body balanced regardless.

## fail-safe states

| Condition | Behavior | Enforced |
|-----------|----------|----------|
| Network / gateway loss | Stop tracking, no new agent motion | **yes** |
| SDK unreachable | Motion capability reports offline; audio and vision keep running | **yes** |
| Fall / balance fault | Booster's controller | not by Autonomous |

---
schema: autonomous.device.v1
id: booster-k1
name: Booster K1
type: humanoid
manufacturer: Booster Robotics
boards: [booster_k1]
gateway:
  default: openclaw
  protocol: websocket
capabilities:
  audio:  { routes: [audio, speaker, voice], required: true }
  vision: { routes: [camera], driver: booster, required: true }
  motion: { routes: [servo], driver: booster_sdk, required: false, safety: SAFETY.md#motion }
  system: { routes: [system], required: true }
soul_ref:   SOUL.md
safety_ref: SAFETY.md
memory:     { backend: local }
---

# Booster K1

> **Status: experimental port, not validated on hardware.** The driver and the
> profile are written against Booster's published SDK. Nothing here has run on
> a K1 yet. See [docs/runtime.md](docs/runtime.md).

A **third-party** kid-sized humanoid from Booster Robotics, running
Autonomous on its onboard computer. Booster's own controller keeps the body
standing and balanced. Autonomous adds the voice, the eyes and the head.

## What the OS drives

- **audio**: the K1's microphone and speaker, through the standard HAL voice pipeline.
- **vision**: the head camera, read from Booster's JPEG video stream through
  Booster's SDK (`driver: booster`, `hal/drivers/camera/booster_capture_device.py`).
  Booster's services keep the sensor.
- **motion**: the **head only** (yaw and pitch), through `driver: booster_sdk`
  (`hal/drivers/motors/booster_service.py`). `/servo/aim`, `/servo/nudge`,
  `/servo/move` and short gestures (nod, headshake, a glance) move the head.

## What it does not drive

- **No walking, no arms, no mode changes.** The driver calls only the SDK's head
  command. It never calls locomotion, arm, get-up, lie-down or torque APIs.
  Walking needs a `LocomotionService`, which the OS does not have yet
  ([docs/not-built-yet.md](../../docs/not-built-yet.md)).
- `/servo/release` centers the head. It never cuts torque, because the legs
  and the balance controller belong to Booster.

## What the agent should assume

- It stands where it was placed. It turns its head toward people and sounds,
  but it cannot walk over.
- The camera is in the head, so turning the head steers what it sees.
- It has no light ring and no screen.

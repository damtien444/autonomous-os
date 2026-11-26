# Booster K1 runtime notes

How to run Autonomous OS on a Booster K1. Scope: **voice, camera and head
only**. Booster's own controller keeps the robot standing. Autonomous never
walks it, moves its arms or changes its mode.

> **Status: experimental, not validated on hardware.** The SDK calls follow
> Booster's published SDK. Run the recon below before you trust any default.


## References

| Topic | Reference |
|-------|-----------|
| Porting steps in general | [`docs/porting-a-robot.md`](../../../docs/porting-a-robot.md) |
| `ROBOT.md` schema, capabilities | [`robots/contract/ROBOT-SPEC.md`](../../contract/ROBOT-SPEC.md), [`capabilities.md`](../../contract/capabilities.md) |
| Safety engine | [`docs/safety.md`](../../../docs/safety.md) |
| Booster SDK (C++ + Python, FastDDS) | <https://github.com/BoosterRobotics/booster_robotics_sdk> |
| Booster developer manual (login required) | Booster Feishu wiki |

## What this profile declares

| Capability | Routes | Required | Note |
|------------|--------|----------|------|
| `audio` | `audio`, `speaker`, `voice` | yes | K1 mic + speaker via ALSA; set the PCM names in `rootfs/opt/hal/.env` |
| `vision` | `camera` | yes | Head camera via OpenCV (`HAL_CAMERA_INDEX`) |
| `motion` | `servo` | no | `driver: booster_sdk`, head yaw + pitch only |
| `system` | `system` | yes | Standard |

`motion` is `required: false`: if the SDK is missing or unreachable, HAL still
boots with voice and camera, and the motion routes report offline.

The board is `booster_k1`, detected from `/proc/device-tree/model`
(`Booster K1 AGX Orin V1.0`, matched on `booster k1`).

## Recon results (2026-11-25)

Measured on one K1 over the development Ethernet link (host `192.168.10.11`,
K1 `enP1p1s0` `192.168.10.102`).

| Item | Result |
|------|--------|
| Compute | NVIDIA Jetson AGX Orin, `aarch64`, Ubuntu 22.04.5, 389 GB free |
| System Python | 3.10.12 |
| Booster SDK | `booster_robotics_sdk_python.cpython-310-aarch64-linux-gnu.so` in `/usr/local/lib/python3.10/dist-packages` |
| Microphone | ALSA card 1 `XFMDPV0018` (USB voice module) |
| Speaker | ALSA card 0 `Device` (USB Audio Device) |
| Camera | **no `/dev/video*`**: not a V4L2 device |
| Booster services | `booster-daemon`, `booster-agent-manager`, `booster-update-engine`, `booster-btmon`, `joystick_ros2` |
| Other interfaces | `nv_eth0` 192.168.13.101, `usb_eth0` 192.168.127.101, Wi-Fi |

**Open blockers:**

1. **Python ABI.** The SDK binding is built for CPython 3.10 only. HAL runs
   in its own CPython 3.12 venv (`hal/pyproject.toml`), so the in-process
   driver cannot import it. The head commands need either a 3.12 build of the
   binding or a small out-of-process bridge that runs under the system Python 3.10.
2. **Camera source.** With no V4L2 node, `driver: opencv` has nothing to open.
   The head camera is published on ROS 2 (below), so `vision` needs a ROS 2
   subscriber backend.
3. **Audio source.** Autonomous does not take ownership of the K1's sensors.
   Booster exposes audio as RPC topics, not as a PCM stream topic, so how
   captured audio is delivered is still unknown.

### ROS 2 topics (listed on the K1)

| Purpose | Topic | Type |
|---------|-------|------|
| Head RGB | `/boostercamera/head/rgb`, `/boostercamera/head/raw/rgb` | `sensor_msgs/msg/Image` |
| Head right RGB (stereo) | `/boostercamera/head/right/rgb` | `sensor_msgs/msg/Image` |
| Head depth | `/boostercamera/head/depth` | `sensor_msgs/msg/Image` |
| Intrinsics | `.../camera_info` | `sensor_msgs/msg/CameraInfo` |
| Compressed stream | `/booster_video_stream` | `sensor_msgs/msg/CompressedImage` |
| Audio capture control | `/booster/audio/{register_client,init_capture_stream,start_capture_stream,stop_capture_stream,destroy_capture_stream}Req/Resp` | `booster_msgs/msg/RpcReqMsg` / `RpcRespMsg` |
| Audio playback | `/booster/audio/{init_player,start_player,send_pcm_data,stop_player,destroy_player}Req/Resp` | `booster_msgs/msg/RpcReqMsg` / `RpcRespMsg` |
| Booster's own ASR output | `/lui_asr_chunk` | `booster_interface/msg/AsrChunk` |

`rclpy` on the K1 is ROS 2 Humble under the system Python 3.10, the same ABI
problem as the SDK. A bridge process on Python 3.10 can serve both the
head commands and the camera frames to HAL.

### Reaching the K1 from a dev machine over Ethernet

By default Booster's DDS profiles whitelist only `127.0.0.1` and the internal
`192.168.127.x` link, so nothing is reachable on the dev Ethernet port
(`enP1p1s0`, `192.168.10.102`). For laptop development (HAL on the dev
machine), add `<address>192.168.10.102</address>` to the `interfaceWhiteList`
of every profile Booster's processes load, then reboot the K1 (robot in
damping or on its stand):

| Profile on the K1 | Loaded by |
|-------------------|-----------|
| `/opt/booster/BoosterRos2/fastdds_profile.xml` | Booster agent, joystick, the `booster` login shell |
| `/opt/booster/config/dds_configs/single-board_configs/fastdds_profile.xml` | `booster_camera` |
| `/opt/booster/config/dds_configs/single-board_configs/fastdds_profile_udp_only.xml` | `audio`, motion and perception RPC bridges |

Keep a `.bak` next to each file. A Booster update may overwrite them.

Dev machine side: give the K1-facing interface `192.168.10.10/24`, allow UDP
from `192.168.10.102` through the host firewall, use domain 0, and use
`robots/booster-k1/tools/ros2/fastdds_k1.xml` for ROS 2 (`pixi run topics`).
The Booster SDK (PyPI `booster_robotics_sdk_python`, cp312 wheels exist) is
initialised with `ChannelFactory.Instance().Init(0, "<k1 interface name>")`
and without that profile file.

Verified 2026-11-25 over the cable: `B1LocoClient` `WaitForService` true,
`GetRobotInfo` = Booster K1, firmware `1.7.1.1-release-00229-2026-07-18-global`,
mode `kDamping`. `/booster_video_stream` delivers JPEG 544x306 at about 10 Hz
(`frame_id head_color_optical_frame`). Raw `/boostercamera/head/rgb` and its
`camera_info` sent no sample to the dev machine within 15 s (large frames over
UDP, not investigated yet).

Head commands: Booster rejects `RotateHead` with RPC status 400 while the robot
is in `kDamping`, and accepts it in `kWalking` (body control `kWBCGait`). The
driver does not change the robot's mode; stand the robot up from the remote
first (LT+START for Prepare, then LT+UP to stand).

### Camera and audio status (checked 2026-11-25, robot walking)

Camera: Booster's camera service (`CameraClient.GetCameras`) reports the head
camera as model `CAMERA_Model_BOOSTER_MIPI`, 544x448 color, frame
`head_color_optical_frame`, with `state: 2` = `CAMERA_CONNECTION_LOST`. While
in that state `/booster_video_stream` still publishes at 10 Hz but repeats one
black 14.7 KB JPEG, and `/boostercamera/head/rgb` delivers nothing. The
earlier live 544x306 frames were captured before this state. The cause is on
the robot side (sensor link). It is not a host or DDS problem. A K1 reboot
cleared it: the camera reported `state: 2` for the first part of boot, then
`state: 1` (connected) about 30 s later, and HAL's `/camera/snapshot` went
live again without a HAL restart. After a reboot, wait for `state: 1` before
treating a black stream as a fault.

Audio works through the SDK's `AudioManager` over the cable, without taking
ownership of the devices:

| Item | Value |
|------|-------|
| Default input | `XFM-DP-V0.0.18 Mono` (iflytek USB mic module, PulseAudio) |
| Default output | `USB Audio Device Digital Stereo (IEC958)` (C-Media USB) |
| Also present | Built-in analog stereo in/out |
| System volume / mute | 1.0 / false |
| Capture stream (default options) | raw PCM, 16 kHz, 16-bit, **3 channels**, 682 samples/channel per frame (~43 ms), 0 dropped |
| Echo-cancelled stream | `enable_naec_pcm` option, off by default, not tested |

The capture frame payload is a Python list of int16 samples, interleaved by
channel. All three channels carry identical samples, so the driver uses
channel 0. Playback (`AudioPlayer`, `PushPcmStream`) has not been tested.

### HAL mic backend

`HAL_AUDIO_INPUT_BACKEND=booster` makes VoiceService read the mic through
`hal/drivers/voice/_internal/booster_mic.py` (`BoosterMicStream`, the same
surface as `ArecordStream`) instead of ALSA. All voice mic opens go through
`VoiceService._open_mic`. One Booster capture session is shared per process;
each mic open only attaches a reader and drops stale audio. Not covered yet:
sound sensing, `/audio` test routes and speaker enrolment still open local
devices.

Measured over the dev cable (2026-11-25):

| | |
|---|---|
| First open (Booster session start + first 1024-sample block) | 828 ms, 622 ms |
| Reopen + first block | 80-91 ms |
| Sustained throughput | 6.02 s of audio in 6.06 s wall, 0 dropped |
| Block wait | median 79 ms, max 91 ms for 64 ms blocks (Booster delivers ~43 ms frames) |

Arms and hands are not driven by HAL. Booster's built-in `WaveHand` was
accepted in `kWalking` (gait switches to `kHumanlikeGait` while waving, then
returns to `kWBCGait`), tested from a standalone SDK script. While the robot was walking,
`/booster_video_stream` kept publishing at 10 Hz but every frame was the same
all-black 14.7 KB JPEG (cause not investigated).

## Head driver

`hal/drivers/motors/booster_service.py` (`BoosterHeadMotionService`), registered
as `booster_sdk` in `hal/drivers/motors/factory.py`.

| HAL | SDK |
|-----|-----|
| joints `head_yaw.pos`, `head_pitch.pos` (deg, -yaw left, +pitch up) | `B1LocoClient.RotateHead(pitch_rad, yaw_rad)` |
| connect | `ChannelFactory.Instance().Init(BOOSTER_DDS_DOMAIN, BOOSTER_NET_IFACE)`, `B1LocoClient().Init()` |

- **Only `RotateHead` is called.** A test (`hal/test/test_booster_service.py`)
  fails if the driver source mentions a locomotion, arm, mode or torque API.
- **Speed:** each move is streamed as about 25 Hz steps, stretched by
  `min_move_duration`, so neither joint exceeds `SAFETY.md` `motion.max_speed`
  (150 deg/s, provisional; 90 felt sluggish on the robot).
- **Range:** every write is clamped to `SAFETY.md` `motion.joint_limits`
  (yaw -80..80, pitch -35..25).
- **Positions** are the last commanded target, not a measurement. Reading the
  head joints from the low-state subscriber waits on confirmed joint indices.
- **Gestures:** `nod`, `acknowledge`, `headshake`, `curious`, `listening`,
  `greeting`, `scanning` are small head-only keyframes. Other emotions skip
  motion. `idle` is a no-op: HAL plays it at the end of every voice turn, and
  moving the head then would cancel a glance the person just asked for.
- **halt** (`/servo/stop`) cancels any glide or gesture and pins the head.
  **release** centers the head and does not cut torque. A humanoid without
  torque falls. The physical e-stop is Booster's remote or damping mode.
- Sign conventions: `HAL_BOOSTER_PITCH_SIGN` (default `-1`, because the SDK's
  +pitch looks down) and `HAL_BOOSTER_YAW_SIGN` (default `-1`, because the
  SDK's +yaw looks left while HAL's +yaw looks right). Yaw verified on a K1:
  HAL `nudge yaw=-10` turned the head left and `nudge pitch=+15` tilted it up,
  so both defaults are correct. Flip one if the
  head moves the wrong way.

## Recon (do this first, read-only)

On the K1 (ask before any SSH):

```bash
cat /proc/device-tree/model; uname -m; lsb_release -a; df -h /
python3 -c "import booster_robotics_sdk_python as s; print(s.__file__)"
arecord -l; aplay -l; v4l2-ctl --list-devices
sudo fuser -v /dev/video* /dev/snd/* 2>&1   # does a Booster service hold camera/mic?
```

- If a Booster service holds the camera or mic, add `owner: booster` to that
  capability and write a `MediaOwner` (see `hal/drivers/media_owner/pollen.py`).
- Fill in the ALSA names and camera index in `rootfs/opt/hal/.env`.
- With the robot standing and supported, check that `RotateHead(0.2, 0)` tilts the
  head **down** and `RotateHead(0, 0.3)` turns it **left**. Otherwise flip the sign envs.

## Install

The one-line installer does not ship this body yet. Follow
[`docs/porting-a-robot.md`](../../../docs/porting-a-robot.md) step 6:

1. Install with `DEVICE_TYPE=intern-v2` (a mic-and-speaker body).
2. `pip install booster_robotics_sdk_python` into HAL's venv (`/opt/hal/.venv`).
3. Copy `robots/booster-k1/` to `/opt/devices/booster-k1` and
   `rootfs/opt/hal/.env` to `/opt/hal/.env`.
4. Set `DEVICE_TYPE=booster-k1` in `/etc/systemd/system/os-server.service`,
   then `systemctl daemon-reload && systemctl restart hal os-server`.

## Verify

```bash
make cts                                          # laptop, static half
python3 -m pytest hal/test/test_booster_service.py
# on the K1, through a tunnel:
ssh -N -L 5001:127.0.0.1:5001 -L 5000:127.0.0.1:5000 <user>@<k1>
make cts-runtime TARGET=127.0.0.1
curl -X POST localhost:5001/servo/aim -H 'content-type: application/json' -d '{"direction":"left"}'
curl -X POST localhost:5001/servo/stop
```

Then check a voice round-trip, a camera snapshot, and the time from wake word
to head glance.

## Not covered

Walking (`LocomotionService`, #205), arms and hands, falls, reading head
joints from low state, and Booster's own sound service (HAL plays through ALSA).

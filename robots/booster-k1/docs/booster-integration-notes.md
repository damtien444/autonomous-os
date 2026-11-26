# Booster K1 integration notes

What had to be found, changed or built to make Autonomous OS talk to a Booster
K1 (2026-11-25). Read this before starting any new Booster project: it lists
the robot-side facts, the changes on the robot, and every file added or changed
in `autonomous-os` and `realtime-voice-agent`.

How to run the setup: [run-k1-voice-pipeline.md](run-k1-voice-pipeline.md).

## 1. The robot

| Item | Value |
|------|-------|
| Robot | Booster K1, nickname `Booster K1-218`, firmware `1.7.1.1-release-00229-2026-07-18-global` |
| Compute | NVIDIA Jetson AGX Orin, aarch64, Ubuntu 22.04.5, system Python 3.10 |
| Device-tree model | `Booster K1 AGX Orin V1.0` |
| Dev Ethernet | robot `enP1p1s0` = `192.168.10.102`; dev PC must be `192.168.10.10/24` |
| Other robot interfaces | `nv_eth0` 192.168.13.101, `usb_eth0` 192.168.127.101 (internal), Wi-Fi |
| SSH | `booster@192.168.10.102`, password only (no key installed) |
| SDK | PyPI `booster-robotics-sdk-python` 1.6.5. Has **cp312** wheels for x86_64 and aarch64 (the copy installed on the robot is cp310 only) |
| Transport | Fast DDS, domain **0** for Booster's services. The `booster` login shell's `.bashrc` sets `ROS_DOMAIN_ID=25`, which is **not** what the services use |
| ROS 2 | Humble on the robot. Booster's own message types (`booster_interface/*`, `booster_msgs/*`) are not in the standard distro |

### Modes and the remote

| Mode | Meaning | Remote |
|------|---------|--------|
| `kDamping` | limp; rejects head, gesture and walk commands (RPC 400) | LT + BACK |
| `kPrepare` | stiff, standing pose | LT + START |
| `kWalking` | balancing; accepts head, arm gestures and `Move` | LT + UP stands up and enters walking |
| `kCustom` | user joint control (`LowCmd`), entered from code only. **Not used.** | none |

The remote shows up as `/joy` (6 axes, 16 buttons). Axis 4 (LT) reads as
active at rest. A reboot drops the robot to damping.

## 2. Changes on the robot (needs sudo)

Booster's Fast DDS profiles whitelist only `127.0.0.1` and the internal
`192.168.127.101`. Nothing on the robot is reachable over the dev cable until
`<address>192.168.10.102</address>` is added to the `interfaceWhiteList` of
**all three** profiles below. Then reboot. Keep a `.bak` of each, because a
Booster update may overwrite them.

| Profile | Loaded by |
|---------|-----------|
| `/opt/booster/BoosterRos2/fastdds_profile.xml` | Booster agent, joystick, `booster` login shell |
| `/opt/booster/config/dds_configs/single-board_configs/fastdds_profile.xml` | `booster_camera` |
| `/opt/booster/config/dds_configs/single-board_configs/fastdds_profile_udp_only.xml` | `audio`, motion RPC bridge (loco API), perception bridge |

```bash
cd /opt/booster/config/dds_configs/single-board_configs
for f in fastdds_profile.xml fastdds_profile_udp_only.xml; do
  sudo cp -n "$f" "$f.bak"
  grep -q 192.168.10.102 "$f" || sudo sed -i '/<address>127.0.0.1<\/address>/a\                <address>192.168.10.102</address>' "$f"
done
# repeat for /opt/booster/BoosterRos2/fastdds_profile.xml, then: sudo reboot
```

To find which profile a process uses:
`sudo cat /proc/<pid>/environ | tr '\0' '\n' | grep FASTRTPS`.

Lessons:
- A typo (`192.168.127.102`) silently did nothing.
- Editing only the BoosterRos2 file exposed the agent and joystick but not
  the camera, audio or loco API.

## 3. Changes on the dev PC

- The NIC on the robot cable is `enx00e04cfbda07` (RTL8153 USB Ethernet) on
  this machine, set to `192.168.10.10/24`. Another machine's NIC name will differ.
- The host firewall (`ufw`) must allow UDP from `192.168.10.102`. Discovery
  failed silently until it did.
- For the SDK, call `ChannelFactory.Instance().Init(0, "<NIC name>")`. Pass the
  NIC name and **no** Fast DDS profile file: with a custom profile the SDK fails
  with "Failed to create participant" or "Failed to set reader".
- For ROS 2 CLI inspection, use `robots/booster-k1/tools/ros2/fastdds_k1.xml`
  (UDP whitelist `127.0.0.1` + `192.168.10.10`).

## 4. What the robot exposes, and how we use it

| Need | Booster interface | Notes |
|------|-------------------|-------|
| Head | `B1LocoClient.RotateHead(pitch_rad, yaw_rad)` | SDK +pitch = down, +yaw = left. HAL flips both: HAL `yaw=-10` turned left, `pitch=+15` up (verified) |
| Arm gestures | `WaveHand(kHandOpen/kHandClose)`, `Handshake(...)`, `HandOnChestGreeting(True/False)` | firmware animations, robot keeps balancing. During a wave the gait switches `kWBCGait` → `kHumanlikeGait` |
| Walking | `B1LocoClient.Move(vx, vy, vyaw)` | m/s and rad/s; Booster clamps further. We re-send at 10 Hz and always end with `Move(0,0,0)` |
| Mode | `GetMode()`, `GetStatus()`, `ChangeMode(...)` | only the stop page calls `ChangeMode` |
| Camera | `CompressedImageSubscriber(handler, "rt/booster_video_stream")` | JPEG 544x306, ~10 Hz, `frame_id head_color_optical_frame`. No `/dev/video*` on the robot |
| Camera health | `CameraClient.GetCameras()` → `state` | 1 = connected, 2 = lost. After boot, ~30 s of state 2 and black frames. Raw `/boostercamera/head/rgb` did not reach the dev PC |
| Mic | `AudioManager.CreateCaptureStream()` | raw PCM 16 kHz int16, **3 identical channels**, 682 samples/ch per frame (~43 ms). Echo-cancelled stream (`enable_naec_pcm`) is **refused, RPC 2001** |
| Speaker | `AudioManager.CreatePlayer()` + `PushPcmStream(bytes)` | `PlayerInitOptions`: `source_type=kPcmStream`, 16 kHz mono 16-bit |
| Remote | ROS 2 `/joy` | the SDK `B1RemoteControllerStateSubscriber` received nothing |

RPC status codes: 0 ok, 100 timeout (service not reachable: check the DDS
whitelist and NIC), 400 bad request (wrong mode), 2001 feature unsupported.
Booster's audio RPCs occasionally exceed 5 s; a single timeout is not fatal.

## 5. Files changed in `autonomous-os` (uncommitted)

| File | Change |
|------|--------|
| `robots/booster-k1/ROBOT.md`, `SAFETY.md`, `SOUL.md` | new device profile: audio, vision (`driver: booster`), motion (`driver: booster_sdk`), system. SAFETY: head 150 deg/s, yaw ±80, pitch -35..25 |
| `robots/booster-k1/rootfs/opt/hal/.env` | on-robot HAL settings (`HAL_AUDIO_INPUT_BACKEND=booster`, SDK interface, camera size) |
| `robots/booster-k1/README.md`, `docs/runtime.md` | runbook, recon results, verified behavior |
| `hal/board/boards.json` | `booster_k1` board, matched on `booster k1` |
| `hal/drivers/motors/booster_service.py` | **new.** `BoosterHeadMotionService` (MotionService: head glide at ≤ max_speed, clamping, gestures) and `BoosterBody` (arm gestures, bounded walking `Move` capped at 0.3 m/s, 0.6 rad/s, 3 s, always ends at zero). `init_booster_channel()` shares one DDS init per process |
| `hal/drivers/motors/factory.py` | registers `booster_sdk` |
| `hal/drivers/camera/booster_capture_device.py` + `factory.py` | **new** `booster` camera driver: SDK JPEG subscriber → HAL frames |
| `hal/drivers/voice/_internal/booster_mic.py` | **new** `BoosterMicStream`: one shared Booster capture session, channel 0 → mono, re-chunked to 1024-sample blocks, 2 s bounded buffer |
| `hal/drivers/voice/voice_service.py`, `hal/config.py` | `HAL_AUDIO_INPUT_BACKEND=booster` selects the K1 mic; the mic opens go through `_open_mic` |
| `hal/routes/booster_body.py` + `hal/runtime.py` | **new** `/booster/gesture`, `/booster/walk`, `/booster/stop`, `/booster/status`, mounted only for the `booster_sdk` driver |
| `hal/test/test_booster_*.py`, `test_motion_factory.py`, `test_device_pipeline_integration.py` | tests (43 pass). The suite's 4 other failures predate this work |
| `robots/booster-k1/tools/k1_voice_bridge.py` | **new** dev tool: K1 mic → voice agent WebSocket → K1 speaker, half duplex, 4x mic gain |
| `robots/booster-k1/tools/k1_estop.py` | **new** standalone stop page (`:8099`): `Move(0,0,0)` then `kPrepare` (stays standing) |
| `robots/booster-k1/tools/run_hal_dev.sh`, `dev-hal.env` | run HAL on the dev PC against the robot |
| `robots/booster-k1/tools/ros2/pixi.toml`, `fastdds_k1.xml` | project-local ROS 2 Humble for inspection |
| `CLAUDE.md`, `.gitignore` | K1 doc row (English only); ignore the pixi env |

`hal/pyproject.toml` and `uv.lock` are **unchanged**: the SDK is installed into
`hal/.venv` by hand (see the run doc). Adding a `booster` extra makes `uv lock`
also pull unrelated updates (`pipecat-ai`, `aiohttp`, `yarl`, `sentencex`),
so that needs a separate decision.

## 6. Files changed in `realtime-voice-agent` (uncommitted)

| File | Change |
|------|--------|
| `robot.py` | **new.** Active only with `ROBOT_HAL_URL`. K1 persona; attaches a HAL camera frame when the question is visual; maps reply tags to HAL calls: head `[nod] [shake] [look_*] [center]`, arms `[wave] [handshake] [greet]`, walking `[step_forward] [step_back] [turn_left] [turn_right]`. Walking runs only if the user's words in that turn ask to move |
| `server.py` | uses the persona; sends the image only with the current turn (history stays text); strips tags from speech and fires them as HAL calls |
| `serve_llm.sh` | **not edited.** It disables images (`--limit-mm-per-prompt '{"image":0,"video":0}'`). Start vLLM by hand with `"image":1` (see the run doc) |

The model `nvidia/Qwen3.8-27B-NVFP4` is a vision-language model
(`Qwen3_5ForConditionalGeneration`). With an attached K1 frame it described the
room correctly, first token in ~90-170 ms.

## 7. Known issues / next steps

- **GPU memory.** vLLM with images + Qwen3-ASR + Chatterbox fill the RTX 5090
  (32.1 / 32.6 GB). The ASR then failed with `cuDNN error:
  CUDNN_STATUS_INTERNAL_ERROR`, which cut replies short. Try vLLM
  `--gpu-memory-utilization 0.70`.
- **Gesture-only replies.** When the model answers with just a tag, the robot
  moves silently. A tag fired as the turn ends can be lost if the user speaks
  immediately: a `turn_left` never reached HAL. Await gesture tasks before
  ending the turn and ask the model to always speak with its action.
- **No echo cancellation.** The firmware refuses the echo-cancelled stream, so
  the bridge mutes the mic while the robot speaks. Barge-in is limited.
- **Mic is quiet.** It needs ~4x gain. The ASR is set to English only.
- **Untested:** the stop page's stop-and-stand on the robot (the earlier
  damping version was pressed once and worked). `RotateHeadWithTime` is unused.
  The low-state joint readback is not wired, so head positions are commanded,
  not measured.
- **Not built:** a HAL speaker backend (the bridge plays audio itself), a
  HAL `LocomotionService`, an on-robot install (needs a cp312 HAL venv on the
  Orin), and commits.

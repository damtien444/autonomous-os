# Run the K1 voice pipeline (dev PC + cable)

Talk to a Booster K1. It answers with the local LLM, can see through its head
camera, and moves its head, arms and feet. Everything except Booster's own
services runs on the dev PC.

```text
K1 mic ─► k1_voice_bridge ─► voice agent (VAD, ASR, LLM, TTS) ─► k1_voice_bridge ─► K1 speaker
                                   │ camera frame / gesture tags
                                   ▼
                       HAL :5001 ─► Booster SDK ─► K1 head, arms, walking
stop page :8099 ─► Booster SDK (independent)
```

Background and the list of changes: [booster-integration-notes.md](booster-integration-notes.md).

## 0. One-time setup

1. **Robot DDS whitelist.** Add `192.168.10.102` to Booster's three Fast DDS
   profiles and reboot (integration notes, §2). Skip this if it's already done.
2. **Dev PC NIC.** Plug the cable in and give that NIC `192.168.10.10/24`.
   Check with `ip -br addr` and `ping 192.168.10.102`.
3. **Firewall.** Allow UDP from the robot:
   `sudo ufw allow in on <nic> from 192.168.10.102 proto udp`.
4. **NIC name.** Put it in `robots/booster-k1/tools/dev-hal.env`
   (`BOOSTER_NET_IFACE=`). The commands below use `enx00e04cfbda07`; replace it.
5. **HAL venv with the Booster SDK** (repo root):
   ```bash
   sudo apt install -y portaudio19-dev            # pyaudio build dependency
   cd hal && uv sync --inexact --frozen --extra dev
   uv pip install --python .venv/bin/python booster-robotics-sdk-python==1.6.5
   .venv/bin/python -c "import booster_robotics_sdk_python"   # must not fail
   ```
6. **Voice agent** (`/home/tdam/tien-project/realtime-voice-agent`): run
   `bash setup.sh` once (envs + model downloads).
7. **Optional:** `cd robots/booster-k1/tools/ros2 && pixi install` for ROS 2 CLI
   inspection.

## 1. Robot

1. Power on. Clear at least 2 m × 2 m around it. Keep the remote in hand.
2. Wait ~1 min, plus ~30 s more for the camera.
3. Stand it up: **LT + START** (Prepare), then **LT + UP** (stand → walking mode).
   Head, arm and walk commands only work in walking mode.

## 2. Start the processes (each in its own terminal, in this order)

All paths are relative to the `autonomous-os` repo root unless noted.

**a. Stop page first.** It's independent of everything else.
```bash
BOOSTER_NET_IFACE=enx00e04cfbda07 hal/.venv/bin/python robots/booster-k1/tools/k1_estop.py
```
Open <http://127.0.0.1:8099>. The page should show `mode: kWalking`. STOP
(or Space/Enter) sends zero velocity, then Prepare: the robot stops and stays
standing. The remote's LT + BACK is the limp stop.

**b. LLM with images enabled** (in `realtime-voice-agent`; don't use
`serve_llm.sh`, which disables images):
```bash
.venv-vllm/bin/vllm serve nvidia/Qwen3.8-27B-NVFP4 --port 8011 --served-model-name llm \
  --gpu-memory-utilization 0.75 --max-model-len 8192 --max-num-seqs 8 \
  --limit-mm-per-prompt '{"image":1,"video":0}'
```
It's ready when `curl -s localhost:8011/v1/models` answers (~1-2 min).

**c. HAL as the K1** (body + camera only; the bridge owns the mic and speaker):
```bash
robots/booster-k1/tools/run_hal_external.sh
```
This is `run_hal_dev.sh` with `dev-hal-external.env` on top: HAL's own voice loop
and its K1 mic/speaker backends are off. Plain `run_hal_dev.sh` runs HAL's
built-in voice pipeline instead and holds the mic, so the bridge fails with
`API call failed, code = 5001`.
Check it (log: `~/.autonomous-sim/k1/hal.log`):
```bash
curl -s localhost:5001/booster/status          # {"mode":"kWalking","busy":false}
curl -s localhost:5001/servo/status            # head_yaw / head_pitch online
curl -s -o /tmp/k1.jpg localhost:5001/camera/snapshot   # a live 544x306 frame
```

**d. Voice agent with robot features** (in `realtime-voice-agent`):
```bash
ROBOT_HAL_URL=http://127.0.0.1:5001 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  .venv/bin/python server.py
```
It's ready when port 8090 listens (`ss -ltn | grep 8090`), ~1-2 min.

**e. Bridge**: start this last, after the agent is listening:
```bash
BOOSTER_NET_IFACE=enx00e04cfbda07 PYTHONPATH=. hal/.venv/bin/python -u \
  robots/booster-k1/tools/k1_voice_bridge.py --minutes 15 --volume 0.5 --gain 4
```
It logs `connected to agent` and a `mic:` level line every 5 s. Talk to the
robot and wait for it to finish speaking before you answer: the mic is muted
while it talks.

## 3. Things to say

| Say | Robot |
|-----|-------|
| "Hello!" / "Bye!" | waves |
| "Let's shake hands" | handshake |
| "Thank you" | hand on chest |
| "Do you agree?" | nods / shakes its head |
| "Look to your left" | turns its head |
| "What do you see?" | describes the camera view |
| "Come closer" / "Step back" / "Turn around" | 1.5 s slow step or turn |

It only walks when your words in that turn ask it to move.

## 4. Manual control (no voice)

```bash
curl -X POST localhost:5001/servo/nudge -H 'content-type: application/json' -d '{"yaw":-10}'   # head left
curl -X POST localhost:5001/servo/aim   -H 'content-type: application/json' -d '{"direction":"center"}'
curl -X POST localhost:5001/booster/gesture -H 'content-type: application/json' -d '{"name":"wave","hold_s":3}'
curl -X POST localhost:5001/booster/walk -H 'content-type: application/json' -d '{"vx":0.2,"seconds":1.5}'
curl -X POST localhost:5001/booster/stop     # stop walking now
```

## 5. Shut down

1. Stop the bridge, the voice agent, HAL and vLLM (Ctrl-C each).
2. On the remote: **LT + START** (Prepare), lay the robot down, then hold the
   power button 3 s. Leave the stop page running until the robot is down.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| SDK `WaitForService` false, RPC code 100 | robot DDS whitelist missing `192.168.10.102`, wrong `BOOSTER_NET_IFACE`, PC IP not `.10.10`, or the firewall |
| Head / gesture / walk → HTTP 409 or RPC 400 | robot not in walking mode; stand it up from the remote |
| Black camera frames | camera state 2 (lost): wait ~30 s after boot, else reboot the robot |
| Agent never replies | start the bridge only after the agent listens on 8090; check `mic:` levels rise while talking |
| Replies cut off, `CUDNN_STATUS_INTERNAL_ERROR` in the agent log | GPU full: lower vLLM `--gpu-memory-utilization` (not below 0.75: vLLM then fails at startup with `No available memory for the cache blocks`) |
| Robot stops responding after a few turns; only a bridge restart helps | stuck Silero VAD state; fixed by `QuietResetModel` in `realtime-voice-agent/server.py` (integration notes, §7) |
| Bridge: `API call failed, code = 5001` on start | another process holds the K1 mic (HAL with `HAL_AUDIO_INPUT_BACKEND=booster`, or a killed one). Start HAL with `run_hal_external.sh`; reboot the robot if a dead process still holds it |
| `ros2 topic list` empty from the PC | use `FASTRTPS_DEFAULT_PROFILES_FILE=robots/booster-k1/tools/ros2/fastdds_k1.xml`, `ROS_DOMAIN_ID=0` |

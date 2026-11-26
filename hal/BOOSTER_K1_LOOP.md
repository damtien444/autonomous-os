# Booster K1: voice agent ↔ HAL ↔ robot loop

How the 2026-11-25 setup connects a real Booster K1 to the realtime voice agent
(`/home/tdam/tien-project/realtime-voice-agent`) through HAL running on a dev PC.
Yellow = code added in HAL, blue = added in the voice agent or the dev tools
(`robots/booster-k1/tools/`), gray = the robot. Everything is uncommitted.

How to run it: [robots/booster-k1/docs/run-k1-voice-pipeline.md](../robots/booster-k1/docs/run-k1-voice-pipeline.md).
Robot facts: `/home/tdam/tien-project/booster-k1/`.

```mermaid
flowchart LR
  subgraph K1["Booster K1 · 192.168.10.102 · DDS domain 0"]
    direction TB
    MIC["Mic<br/>audio service capture stream"]
    SPK["Speaker<br/>audio player"]
    CAM["Head camera<br/>rt/booster_video_stream"]
    LOCO["Loco API<br/>RotateHead · WaveHand · Handshake<br/>HandOnChestGreeting · Move · ChangeMode"]
  end

  subgraph PC["Dev PC"]
    direction TB
    subgraph BR["k1_voice_bridge.py"]
      BMIC["BoosterMicStream<br/>ch 0 of 3 · x4 gain<br/>muted while robot speaks"]
      BSPK["Speaker out<br/>24 to 16 kHz · PushPcmStream"]
    end
    subgraph AG["realtime-voice-agent · server.py :8090"]
      ASR["Silero VAD · turn detector · Qwen3-ASR"]
      VIS{"Visual question?"}
      LLM["vLLM :8011 · Qwen3.8-27B<br/>images on · K1 persona"]
      TTS["Chatterbox TTS"]
      ACT["robot.py act()<br/>tag to HTTP · walk only if asked"]
    end
    subgraph HAL["HAL :5001 · device booster-k1"]
      SERVO["/servo/nudge · /aim · /play"]
      HEAD["BoosterHeadMotionService<br/>SAFETY.md clamp · max 150 deg/s"]
      BODYR["/booster/gesture · /walk · /stop"]
      BODY["BoosterBody<br/>walk max 0.3 m/s · 3 s · ends at 0"]
      SNAP["/camera/snapshot"]
      CAMD["BoosterVideoCaptureDevice"]
    end
    ESTOP["k1_estop.py :8099<br/>STOP: Move 0 + kPrepare"]
  end

  MIC -->|"PCM 16 kHz"| BMIC
  BMIC -->|"WebSocket"| ASR
  ASR --> VIS
  VIS -->|"no"| LLM
  VIS -->|"yes: GET"| SNAP
  SNAP -->|"JPEG frame"| LLM
  LLM -->|"spoken text"| TTS
  LLM -->|"gesture tag"| ACT
  TTS -->|"reply audio"| BSPK
  BSPK -->|"PCM"| SPK
  ACT -->|"head tags"| SERVO
  ACT -->|"arm / walk tags"| BODYR
  SERVO --> HEAD
  HEAD -->|"RotateHead"| LOCO
  BODYR --> BODY
  BODY -->|"WaveHand · Handshake · Move"| LOCO
  CAM -->|"JPEG 544x306 ~10 Hz"| CAMD
  CAMD --> SNAP
  ESTOP -->|"Move 0 · ChangeMode kPrepare"| LOCO

  classDef hal fill:#fde68a,stroke:#b45309,color:#000
  classDef agent fill:#bfdbfe,stroke:#1d4ed8,color:#000
  classDef robot fill:#e5e7eb,stroke:#374151,color:#000
  class BMIC,HEAD,BODYR,BODY,CAMD hal
  class ACT,LLM,ESTOP,BSPK agent
  class MIC,SPK,CAM,LOCO robot
```

## What was added in HAL

| File | Role in the loop |
|------|------------------|
| `drivers/motors/booster_service.py` | Head driver behind `/servo/*` (clamping, ≤ 150°/s glide), plus `BoosterBody` for arm gestures and capped walking. `init_booster_channel()` shares one DDS connection with the camera |
| `routes/booster_body.py` | `/booster/gesture`, `/booster/walk`, `/booster/stop`, `/booster/status`, which the agent calls |
| `drivers/camera/booster_capture_device.py` | behind `/camera/snapshot`: reads Booster's JPEG stream |
| `drivers/voice/_internal/booster_mic.py` | the K1 mic stream (`BoosterMicStream`), imported directly by the bridge |
| `runtime.py`, `drivers/motors/factory.py`, `drivers/camera/factory.py`, `config.py`, `drivers/voice/voice_service.py`, `board/boards.json` | register these drivers and routes for the `booster-k1` device |

## Gesture tags the agent can emit

| Tag | HAL call |
|-----|----------|
| `[nod]`, `[shake]` | `POST /servo/play` (`nod`, `headshake`) |
| `[look_left]`, `[look_right]`, `[look_up]`, `[look_down]` | `POST /servo/nudge` |
| `[center]` | `POST /servo/aim` (`center`) |
| `[wave]`, `[handshake]`, `[greet]` | `POST /booster/gesture` |
| `[step_forward]`, `[step_back]`, `[turn_left]`, `[turn_right]` | `POST /booster/walk` (1.5 s; only if the user asked to move) |

## Notes

- **HAL doesn't carry the audio.** The bridge reads the mic and plays speech on
  the robot directly; HAL serves only the camera and motion. HAL's own voice
  pipeline also supports the K1 mic (`HAL_AUDIO_INPUT_BACKEND=booster`), but it
  wasn't used in this loop.
- **The stop page bypasses HAL.** It has its own DDS connection, so it still
  works if HAL or the agent hangs.
- **Mode:** head, arm and walk commands only work while the robot is in
  walking mode (`kWalking`). HAL never changes the robot's mode.

#!/bin/bash
# Run HAL on this dev machine as the Booster K1 over the dev cable (port 5001).
# State lives in ~/.autonomous-sim/k1; the log is ~/.autonomous-sim/k1/hal.log.
set -e
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
S="${K1_STATE_DIR:-$HOME/.autonomous-sim/k1}"
mkdir -p "$S"
# Local STT + TTS (realtime-voice-agent/speech_servers.py on :8096). An existing
# config.json is left alone; delete it to reset to these defaults.
[ -f "$S/config.json" ] || cat > "$S/config.json" <<'JSON'
{
  "stt_base_url": "http://127.0.0.1:8096/v1",
  "stt_api_key": "local",
  "stt_model": "qwen3-asr",
  "stt_language": "en",
  "tts_provider": "openai",
  "tts_base_url": "http://127.0.0.1:8096/v1",
  "tts_api_key": "local"
}
JSON
set -a; . "$ROOT/robots/booster-k1/tools/dev-hal.env"; set +a
export DEVICES_DIR="$ROOT/robots"
# Repo-relative paths in dev-hal.env resolve against the repo root.
case "${HAL_REALTIME_PROMPT_FILE:-}" in ""|/*) ;; *) export HAL_REALTIME_PROMPT_FILE="$ROOT/$HAL_REALTIME_PROMPT_FILE" ;; esac
# The realtime agent reads its persona from the agent workspace, which os-server
# creates under /root on a device. On a dev PC, seed a local one with the K1 SOUL.md.
W="$S/openclaw-workspace"
mkdir -p "$W/skills"
[ -f "$W/SOUL.md" ] || cp "$ROOT/robots/booster-k1/SOUL.md" "$W/SOUL.md"
# The agent name sets the wake phrase ("hey k1"); without it HAL uses "booster-k1".
[ -f "$W/IDENTITY.md" ] || printf -- '- **Name:** K1\n' > "$W/IDENTITY.md"
export HAL_OPENCLAW_WORKSPACE_DIR="$W"
export OS_CONFIG_PATH=$S/config.json HAL_SNAPSHOT_DIR=$S/snapshots HAL_SNAPSHOT_PERSIST_DIR=$S/snapshots_p \
  HAL_TTS_CACHE_DIR=$S/tts_cache HAL_CALIBRATION_DIR=$S/calib HAL_USER_BEARING_PATH=$S/user_bearing.json \
  HAL_FACE_HEIGHT_PATH=$S/face_height.json HAL_VOICE_STRANGERS_DIR=$S/voice_strangers \
  HAL_DL_STALL_LOG=$S/dl_stall.log HAL_CODEX_WORKSPACE_DIR=$S/workspace HAL_LOG_DIR=$S/log \
  HAL_USERS_DIR=$S/users HAL_STRANGERS_DIR=$S/strangers HAL_BT_STATE_DIR=$S HAL_VOLUME_STATE_PATH=$S/volume
cd "$ROOT/hal"
echo "HAL as booster-k1 on http://127.0.0.1:5001 (log: $S/hal.log)"
PYTHONPATH=.. exec .venv/bin/uvicorn hal.server:app --host 127.0.0.1 --port 5001 > "$S/hal.log" 2>&1

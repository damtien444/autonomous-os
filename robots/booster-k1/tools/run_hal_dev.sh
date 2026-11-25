#!/bin/bash
# Run HAL on this dev machine as the Booster K1 over the dev cable (port 5001).
# State lives in ~/.autonomous-sim/k1; the log is ~/.autonomous-sim/k1/hal.log.
set -e
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
S="${K1_STATE_DIR:-$HOME/.autonomous-sim/k1}"
mkdir -p "$S"
[ -f "$S/config.json" ] || echo '{}' > "$S/config.json"
set -a; . "$ROOT/robots/booster-k1/tools/dev-hal.env"; set +a
export DEVICES_DIR="$ROOT/robots"
export OS_CONFIG_PATH=$S/config.json HAL_SNAPSHOT_DIR=$S/snapshots HAL_SNAPSHOT_PERSIST_DIR=$S/snapshots_p \
  HAL_TTS_CACHE_DIR=$S/tts_cache HAL_CALIBRATION_DIR=$S/calib HAL_USER_BEARING_PATH=$S/user_bearing.json \
  HAL_FACE_HEIGHT_PATH=$S/face_height.json HAL_VOICE_STRANGERS_DIR=$S/voice_strangers \
  HAL_DL_STALL_LOG=$S/dl_stall.log HAL_CODEX_WORKSPACE_DIR=$S/workspace HAL_LOG_DIR=$S/log \
  HAL_USERS_DIR=$S/users HAL_STRANGERS_DIR=$S/strangers HAL_BT_STATE_DIR=$S HAL_VOLUME_STATE_PATH=$S/volume
cd "$ROOT/hal"
echo "HAL as booster-k1 on http://127.0.0.1:5001 (log: $S/hal.log)"
PYTHONPATH=.. exec .venv/bin/uvicorn hal.server:app --host 127.0.0.1 --port 5001 > "$S/hal.log" 2>&1

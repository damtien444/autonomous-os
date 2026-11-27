#!/bin/bash
# Run HAL as the K1 for the external voice agent: body + camera only (port 5001).
# Same as run_hal_dev.sh plus the overrides in dev-hal-external.env.
DIR="$(cd "$(dirname "$0")" && pwd)"
K1_HAL_ENV_OVERRIDE="$DIR/dev-hal-external.env" exec "$DIR/run_hal_dev.sh"

#!/usr/bin/env bash
# Demo v1 (WP5): from a clean state to the checked scenario; leaves the stack up.
#   scripts/demo_v1.sh        full demo (about 5 min of wall clock)
#   scripts/demo_v1.sh stop   only stop the EMTRA processes (make down uses it)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE="docker compose -f infra/docker-compose.yml"
PY="$ROOT/.venv/bin/python"
OUT="$ROOT/runs/demo-v1"
PROCS='-m (services\.ingestor|services\.reliability_monitor|sim\.world)( |$)'

# Stop EMTRA processes of earlier runs: SIGTERM, then SIGKILL after 30 s. Only those whose
# working directory is this repo, whoever launched them.
stop_procs() {
    local pid mine=()
    for pid in $(pgrep -f -- "$PROCS" || true); do
        [ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" = "$ROOT" ] && mine+=("$pid")
    done
    [ ${#mine[@]} -eq 0 ] && return 0
    kill -TERM "${mine[@]}" 2>/dev/null || true
    for _ in $(seq 60); do
        local alive=0
        for pid in "${mine[@]}"; do kill -0 "$pid" 2>/dev/null && alive=1; done
        [ $alive -eq 0 ] && return 0
        sleep 0.5
    done
    kill -KILL "${mine[@]}" 2>/dev/null || true
}

if [ "${1:-}" = "stop" ]; then
    stop_procs
    exit 0
fi

trap 'stop_procs; exit 130' INT TERM

echo "== 1. clean state"
stop_procs
uv sync --frozen --quiet
rm -rf "$OUT" && mkdir -p "$OUT"
$COMPOSE down -v --remove-orphans
make up
"$PY" -m emtra_common.reset          # MQTT sessions, retained messages, FogStore tables
"$PY" -m emtra_common.reference load # reference tables were emptied with the rest

echo "== 2. services"
for svc in ingestor reliability_monitor; do
    nohup "$PY" -m "services.$svc" >"$OUT/$svc.log" 2>&1 &
done
"$PY" -m scripts.demo_v1_check ready

echo "== 3. world"
echo "dashboard: $("$PY" -m scripts.demo_v1_check url)"
# shellcheck disable=SC2046
"$PY" -m sim.world $("$PY" -m scripts.demo_v1_check args) --out "$OUT/world"

echo "== 4. checks"
"$PY" -m scripts.demo_v1_check check
echo "demo-v1 OK. Stack left up; 'make down' stops it."

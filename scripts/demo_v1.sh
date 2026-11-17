#!/usr/bin/env bash
# Demo v1 (WP5): from a clean state to the checked scenario; leaves the stack up.
#   scripts/demo_v1.sh        full demo (about 5 min of wall clock)
#   scripts/demo_v1.sh stop   only stop the EMTRA processes (make down uses it)
#   scripts/demo_v1.sh cloud  start the bridge, the sink and the ActionPlanner unless they run
#                             (make up uses it)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE="docker compose -f infra/docker-compose.yml"
PY="$ROOT/.venv/bin/python"
OUT="$ROOT/runs/demo-v1"
PROCS='-m (services\.ingestor|services\.reliability_monitor|services\.bridge|services\.cloud_sink|services\.action_planner|sim\.world)( |$)'

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

# Bridge, sink and ActionPlanner are part of the stack (make up), not of one demo run.
start_cloud() {
    local svc pid
    mkdir -p "$ROOT/runs/cloud"
    for svc in bridge cloud_sink action_planner; do
        for pid in $(pgrep -f -- "-m services\.$svc( |\$)" || true); do
            [ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" = "$ROOT" ] && continue 2
        done
        nohup "$PY" -m "services.$svc" >>"$ROOT/runs/cloud/$svc.log" 2>&1 &
    done
}

if [ "${1:-}" = "stop" ]; then
    stop_procs
    exit 0
fi
if [ "${1:-}" = "cloud" ]; then
    start_cloud
    exit 0
fi

trap 'stop_procs; exit 130' INT TERM

echo "== 1. clean state"
stop_procs
uv sync --frozen --quiet
rm -rf "$OUT" && mkdir -p "$OUT"
# Outbox and lake go with the Kafka volume: msg_ids are deterministic and the outbox keeps its
# sent rows, so a stale outbox would swallow this run's messages.
rm -rf "$ROOT/data/bridge" "$ROOT/data/lake"
$COMPOSE down -v --remove-orphans
make up
stop_procs                           # make up started bridge, sink and planner; reset must not race them
"$PY" -m emtra_common.reset          # MQTT sessions, retained messages, FogStore tables
"$PY" -m emtra_common.reference load # reference tables were emptied with the rest
start_cloud

echo "== 2. services"
nohup "$PY" -m services.ingestor >"$OUT/ingestor.log" 2>&1 &
# the Monitor scales the recommendation budget (wall clock) by the world's speed
nohup "$PY" -m services.reliability_monitor --speed "$("$PY" -m scripts.demo_v1_check speed)" \
    >"$OUT/reliability_monitor.log" 2>&1 &
"$PY" -m scripts.demo_v1_check ready

echo "== 3. world"
echo "dashboard: $("$PY" -m scripts.demo_v1_check url)"
# shellcheck disable=SC2046
"$PY" -m sim.world $("$PY" -m scripts.demo_v1_check args) --out "$OUT/world"

echo "== 4. checks"
"$PY" -m scripts.demo_v1_check check
echo "demo-v1 OK. Stack left up; 'make down' stops it."

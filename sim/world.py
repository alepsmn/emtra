"""Live world: the same engine on a real-time SimPy environment, publishing snapshots on MQTT.

python -m sim.world --seed N --speed 60 [--faults faults.yaml] [--hours H]
"""

import argparse
import signal
from pathlib import Path

import simpy

from emtra_common.config import ROOT
from emtra_common.corridor import load_corridor
from emtra_common.mqtt import connect, make_client
from sim.engine import Engine
from sim.faults import load_faults
from sim.params import load_sim_config
from sim.run import write_run


def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    # A background shell job starts with SIGINT ignored, so Python would never raise
    # KeyboardInterrupt; install the handler explicitly for both signals.
    signal.signal(signal.SIGINT, _raise_interrupt)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--speed", type=float, default=60.0)
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--faults", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    specs = load_faults(a.faults) if a.faults else []
    out = Path(a.out) if a.out else ROOT / "runs" / f"world-s{a.seed}"
    client = make_client(f"sim-world-{a.seed}")
    connect(client)
    client.loop_start()

    def publish(stop_id: str, line: str) -> None:
        client.publish(f"emtra/v1/sim/raw/{stop_id}", line, qos=1)

    env = simpy.rt.RealtimeEnvironment(factor=1 / a.speed, strict=False)
    eng = Engine(load_corridor(), load_sim_config(), a.seed, faults=specs, env=env,
                 horizon_s=None if a.hours is None else a.hours * 3600, on_snapshot=publish)
    eng.collector_id = f"sim-{out.name}"
    try:
        eng.run()
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
        print(write_run(eng, out, "world", specs))


if __name__ == "__main__":
    main()

"""PassengerInfo service (python -m services.passenger_info [--source run|fogstore])."""

import argparse

import uvicorn

from emtra_common.config import resolve
from emtra_common.corridor import load_corridor
from services.passenger_info.app import create_app
from services.passenger_info.config import load_passenger_info_config
from services.passenger_info.sources import FogStoreSource, PassengerSource, RunSource


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["run", "fogstore"])
    cfg = load_passenger_info_config()
    if (src := ap.parse_args().source) is not None:
        cfg = cfg.model_copy(update={"source": src})
    corridor = load_corridor()
    source: PassengerSource
    if cfg.source == "run":
        source = RunSource(resolve(cfg.run_dir), corridor, cfg.replay_start, cfg.replay_end,
                           cfg.replay_speed, cfg.snapshot_max_age_s)
    else:
        source = FogStoreSource(cfg.snapshot_max_age_s)
    uvicorn.run(create_app(source, corridor, cfg), host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()

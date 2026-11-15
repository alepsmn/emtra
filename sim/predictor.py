"""What the world announces (not the truth): mean-based estimate with bias and noise."""

import numpy as np

from emtra_common.rng import rng


def predict_all(eng, now: float, k: int) -> list[list[dict]]:  # noqa: ANN001
    """Arrivals list per corridor stop for poll number k at sim time `now`.

    time_left = (sum of mean links to the stop [+ nominal dwells at stops in between] - elapsed
    in the current link, min 0) * bias * noise(bus, poll). Looks only at the present state.
    """
    cfg = eng.cfg
    pc = cfg.predictor
    s2 = np.log(1 + pc.noise_cv**2)
    noise = rng(eng.seed, "pred", k).lognormal(-s2 / 2, np.sqrt(s2), size=len(eng.buses))
    cand: list[dict[str, list[tuple[float, object]]]] = [{} for _ in range(eng.n_stops)]
    for i, b in enumerate(eng.buses):
        tr = b.trip
        if not b.started or b.finished or not b.active or b.next_i >= len(tr.served):
            continue
        elapsed = 0.0 if b.leg_start is None else now - b.leg_start
        if b.next_i == 0:
            t = max(0.0, b.first_leg_mean - elapsed)
        else:
            t = max(0.0, cfg.link_mean_by_link_s[tr.served[b.next_i - 1]] - elapsed)
        for j in range(b.next_i, len(tr.served)):
            if j > b.next_i:
                t += pc.dwell_pred_s + cfg.link_mean_by_link_s[tr.served[j - 1]]
            tl = t * pc.bias * float(noise[i])
            if tl > pc.max_time_left_s:
                break
            cand[tr.served[j]].setdefault(tr.line_id, []).append((tl, b))
    out: list[list[dict]] = []
    for g in range(eng.n_stops):
        arrivals = []
        for lid in sorted(cand[g]):
            best = sorted(cand[g][lid], key=lambda x: (x[0], x[1].bus_id))
            ln = eng.c.line(lid)
            dest = next(s.name for s in eng.c.stops if s.stop_id == ln.exit_stop)
            for tl, b in best[: pc.max_buses_per_line]:
                arrivals.append({"line_id": lid, "direction_id": ln.direction_id,
                                 "bus_id": b.bus_id, "destination": dest,
                                 "time_left_s": int(round(tl)),
                                 "distance_m": int(tl * pc.nominal_speed_mps)})
        out.append(arrivals)
    return out

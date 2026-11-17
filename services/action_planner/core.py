"""ActionPlanner core (F10): a recommendation request in, a recommendation (or nothing) out.

No Kafka here. The corridor state comes in the request: the cloud does not read FogStore.
"""

from concurrent.futures import Executor

from contracts.envelope import Envelope
from contracts.payloads import Recommendation, RecommendationRequest, make_envelope
from emtra_common.clock import Clock
from sim.evaluator import PlannerConfig, evaluate

PRODUCER = "action_planner"


def answer(raw: bytes, pool: Executor, cfg: PlannerConfig,
           clock: Clock) -> Envelope[Recommendation] | None:
    """Evaluate the request of an envelope. The answer keeps the request's event time and
    carries `correlation_id = alert_id`. None: not one complete batch within the budget (the
    Monitor's fallback covers it). `budget_margin_s` of the request's budget is left for the
    trip back to the fog."""
    env = Envelope[RecommendationRequest].model_validate_json(raw)
    req = env.payload
    budget = max(0.0, req.budget_s - cfg.budget_margin_s)
    ev = evaluate(req, env.event_time, pool=pool, cfg=cfg, budget_s=budget, clock=clock)
    if ev.recommendation is None:
        return None
    return make_envelope("emtra.recommendation", ev.recommendation, producer=PRODUCER,
                         produced_at=clock.now(), event_time=env.event_time,
                         correlation_id=req.alert_id)

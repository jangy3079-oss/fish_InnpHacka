"""테스트용: 시나리오 → MetricRecord → FusedFeatures, 한 번만 생성해 재사용."""
from functools import lru_cache

from fusion.fuse import fuse
from simulator.echogram import synthesize
from simulator.labels import build_labels
from simulator.run import simulate
from simulator.scenario import load_scenario
from sonar.metrics import extract


@lru_cache(maxsize=None)
def scenario(name):
    sim = simulate(load_scenario(name))
    recs = sim.records + extract(synthesize(sim))
    return sim, recs, fuse(recs), build_labels(sim)


@lru_cache(maxsize=None)
def model():
    from stress.model import StressModel
    _, _, fused, _ = scenario("normal_14d")
    return StressModel("cage-003").fit(fused)


@lru_cache(maxsize=None)
def scores(name):
    _, _, fused, _ = scenario(name)
    return model().score_series(fused)


def in_interval(scores_, labels, cause):
    from datetime import datetime
    p = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    ivs = [(p(i["start"]), p(i["end"])) for i in labels["intervals"] if i["cause"] == cause]
    return [s for s in scores_ if any(a <= s.ts < b for a, b in ivs)]

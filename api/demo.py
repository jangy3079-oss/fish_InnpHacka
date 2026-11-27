"""Synthetic-only scenario API adapter (#30)."""

from functools import lru_cache

from fastapi import HTTPException
from pydantic import ValidationError
import yaml

from common.config_loader import ConfigError
from decision.first_aid import UnsafeFirstAid
from pipeline.replay import ReplayEngine, ReplayRangeError
from .schemas import DemoResult


@lru_cache(maxsize=1)
def get_replay_engine():
    return ReplayEngine()


def replay(engine, name, t):
    try:
        return DemoResult.model_validate(engine.replay(name,t))
    except ReplayRangeError:
        raise HTTPException(422,detail={'code':'invalid_replay_time','message':'Use elapsed hours within the scenario duration'}) from None
    except (ConfigError,UnsafeFirstAid,ValidationError,OSError,ValueError,TypeError,KeyError,yaml.YAMLError):
        raise HTTPException(503,detail={'code':'replay_unavailable','message':'Synthetic replay inputs are unavailable'}) from None

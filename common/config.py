"""Compatibility access to the same validated five-file configuration bundle.

ROCKFISH_CONFIG_DIR selects the bundle. Missing/invalid files or keys fail rather
than falling back to caller defaults. Data batches should pass one snapshot.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path(__file__).resolve().parents[1] / 'config'


def config_dir() -> Path:
    return Path(os.environ.get('ROCKFISH_CONFIG_DIR', DEFAULT_DIR))


@lru_cache(maxsize=8)
def _loader(directory):
    from .config_loader import ConfigLoader
    return ConfigLoader(directory)


def load_yaml(name: str) -> dict:
    return _loader(str(config_dir().resolve())).snapshot().to_dict()[name]


def get(name: str, dotted: str, default: Any = None) -> Any:
    # Keep the old call signature, but never accept an implicit business default.
    from .config_loader import ConfigError
    cur = load_yaml(name)
    try:
        for key in dotted.split('.'):
            cur = cur[key]
    except (KeyError, TypeError):
        raise ConfigError('Required configuration key is missing') from None
    return cur


def clear_cache() -> None:
    _loader.cache_clear()

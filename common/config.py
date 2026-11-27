"""config/*.yaml 읽기 (설정 분리 원칙, SDD 1.2).

파일이나 키가 없으면 호출하는 쪽의 기본값을 쓴다. 테스트에서 경로를 바꾸려면
환경변수 ROCKFISH_CONFIG_DIR 을 지정한다.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "config"


def config_dir() -> Path:
    return Path(os.environ.get("ROCKFISH_CONFIG_DIR", DEFAULT_DIR))


@lru_cache(maxsize=32)
def _load(path: str, version: tuple) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding='utf-8-sig')) or {}


def load_yaml(name: str) -> dict:
    """config/<name>.yaml 전체 dict (없으면 빈 dict)."""
    path = config_dir()/f'{name}.yaml'
    try:
        info = path.stat()
    except FileNotFoundError:
        return {}
    # API updates replace the file atomically: identity + nanosecond mtime + size detects the new version.
    # Avoid rereading the whole YAML for every feature lookup in offline fusion loops.
    return _load(str(path),(info.st_dev,info.st_ino,info.st_mtime_ns,info.st_size))


def get(name: str, dotted: str, default: Any) -> Any:
    """load_yaml(name)에서 'a.b.c' 경로 값. 없으면 default."""
    cur: Any = load_yaml(name)
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def clear_cache() -> None:
    _load.cache_clear()

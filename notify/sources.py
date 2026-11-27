"""Rule-scoped retrieval of explicitly reviewed passages, with source-byte hashes."""

import hashlib
import re
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

import yaml
from pydantic import Field, model_validator

from common.config_loader import ConfigError
from decision.first_aid import normalize, reviewed_guide, ensure_safe
from .models import Citation, Contract, Rule

ROOT = Path(__file__).resolve().parents[1]


class Passage(Contract):
    id: str
    rules: tuple[Rule, ...]
    source: str
    source_url: str
    scope: str
    quote: str


class GenerationPolicy(Contract):
    max_sentences: Annotated[int, Field(strict=True, ge=2, le=3)]
    max_characters: Annotated[int, Field(strict=True, ge=100, le=2000)]
    llm_timeout_seconds: Annotated[float, Field(gt=0, le=60)]
    llm_max_completion_tokens: Annotated[int, Field(strict=True, ge=1, le=4096)]
    passages: tuple[Passage, ...]

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        if len({p.id for p in self.passages}) != len(self.passages):
            raise ValueError("duplicate passage IDs")
        return self


def load_policy(path=ROOT / "config/notify.yaml"):
    raw = Path(path).read_bytes()
    return GenerationPolicy.model_validate(yaml.safe_load(raw)), hashlib.sha256(raw).hexdigest()


def flattened(text):
    return re.sub(r"\s+", " ", normalize(text).replace("**", "")).strip()


def citation(root: Path, id, source, scope, quote, source_url=None):
    path = (root / source).resolve()
    if not path.is_relative_to((root / "notify/docs").resolve()) or not path.is_file():
        raise ConfigError("notification source must be a reviewed local document")
    raw = path.read_bytes()
    if not quote.strip() or flattened(quote) not in flattened(raw.decode("utf-8")):
        raise ConfigError("notification passage is absent from its source")
    if source_url:
        parsed = urlsplit(source_url)
        if parsed.scheme != "https" or parsed.hostname not in {"nifs.go.kr", "www.nifs.go.kr"}:
            raise ConfigError("notification citation must identify an official NIFS source")
        if source_url not in raw.decode("utf-8"):
            raise ConfigError("notification citation URL is absent from its source metadata")
    return Citation(id=id, source=source, source_url=source_url,
                    source_sha256=hashlib.sha256(raw).hexdigest(), scope=scope, quote=quote)


def retrieve(rule_id, policy, snapshot, root=ROOT):
    if rule_id is None:
        return ()
    root = Path(root).resolve()
    if rule_id in ("R5", "R8"):
        # Uses the existing official-status, path, source-text and blocked-term checks.
        guide = reviewed_guide(rule_id, snapshot, source_root=root)
        return tuple(citation(root, action["id"], action["source"], "수과원 질병 사육관리 자료",
                              action["text"]) for action in guide["actions"])
    results = []
    for passage in policy.passages:
        if rule_id in passage.rules:
            ensure_safe(passage.quote, snapshot)
            results.append(citation(root, passage.id, passage.source, passage.scope,
                                    passage.quote, passage.source_url))
    return tuple(results)

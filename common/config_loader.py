"""Validated YAML snapshots with on-access reload; shared validation for #36 edits."""

import argparse
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from types import MappingProxyType

import yaml

from .config_schema import CONFIG_MODELS, validate_bundle
from .config import config_dir

CONFIG_NAMES = tuple(CONFIG_MODELS)
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / "config"
MAX_FILE_BYTES = 1024 * 1024


class ConfigError(ValueError):
    """No new snapshot was published. Existing snapshots remain valid."""


class UniqueSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ConfigError("YAML aliases are not supported")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ConfigError("YAML mapping keys must be strings")
            if key in result:
                raise ConfigError("Duplicate YAML mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigError("Non-finite configuration numbers are not supported")
    if value is not None and type(value) not in (str, int, float, bool):
        raise ConfigError("Configuration values must be JSON-compatible scalars")
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class ConfigSnapshot:
    revision: str
    loaded_at: datetime
    configs: Mapping

    def __getitem__(self, name: str) -> Mapping:
        if name not in CONFIG_NAMES:
            raise KeyError(name)
        return self.configs[name]

    def to_dict(self) -> dict:
        """Independent mutable copy for existing dict/list-based consumers."""
        return _thaw(self.configs)


def config_revision(raw):
    digest = hashlib.sha256()
    for name in CONFIG_NAMES:
        digest.update(name.encode()+b'\0'+raw[name]+b'\0')
    return digest.hexdigest()


def validate_contents(raw):
    documents = {}
    for name in CONFIG_NAMES:
        content = raw[name]
        if len(content)>MAX_FILE_BYTES:
            raise ConfigError(f'{name}.yaml exceeds the 1 MiB limit')
        try:
            value = yaml.load(content.decode('utf-8-sig'),Loader=UniqueSafeLoader)
            if not isinstance(value,dict) or not value:
                raise ConfigError('Root must be a non-empty mapping')
            _freeze(value)
            documents[name] = value
        except (UnicodeError,yaml.YAMLError,ConfigError) as exc:
            reason = str(exc) if isinstance(exc,ConfigError) else 'Invalid UTF-8 or YAML syntax/tag'
            raise ConfigError(f'{name}.yaml: {reason}') from None
    try:
        validated = validate_bundle(documents)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    return ConfigSnapshot(config_revision(raw),datetime.now(timezone.utc),_freeze(validated))


class ConfigLoader:
    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(config_dir() if directory is None else directory).resolve()
        self._lock = RLock()
        self._snapshot = None
        self._last_error = None

    @property
    def last_good(self) -> ConfigSnapshot | None:
        """Explicit fallback only: does not reread files or suppress errors."""
        with self._lock:
            return self._snapshot

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def _read_files(self) -> dict[str, bytes]:
        contents = {}
        for name in CONFIG_NAMES:
            try:
                with (self.directory / f"{name}.yaml").open("rb") as handle:
                    raw = handle.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES:
                    raise ConfigError(f"{name}.yaml exceeds the 1 MiB limit")
                contents[name] = raw
            except OSError:
                raise ConfigError(f"Cannot read {name}.yaml") from None
        return contents

    def _refresh(self, force: bool) -> ConfigSnapshot:
        with self._lock:
            try:
                raw = self._read_files()
                revision = config_revision(raw)
                if not force and self._snapshot is not None and self._snapshot.revision == revision:
                    self._last_error = None
                    return self._snapshot
                snapshot = validate_contents(raw)
                if raw != self._read_files():
                    raise ConfigError("Configuration changed while loading; retry after files are saved")
                self._snapshot = snapshot
                self._last_error = None
                return snapshot
            except ConfigError as exc:
                self._last_error = str(exc)
                raise

    def snapshot(self) -> ConfigSnapshot:
        """Check all five files and publish a validated immutable bundle if changed."""
        return self._refresh(force=False)

    def reload(self) -> ConfigSnapshot:
        """Explicitly reread/revalidate, even if file bytes are unchanged."""
        return self._refresh(force=True)

    def get(self, name: str) -> Mapping:
        if name not in CONFIG_NAMES:
            raise KeyError(name)
        return self.snapshot()[name]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate all five config YAML files")
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args(argv)
    try:
        snapshot = ConfigLoader(args.directory).reload()
    except ConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "revision": snapshot.revision,
        "loaded_at": snapshot.loaded_at.isoformat(), "files": list(CONFIG_NAMES)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

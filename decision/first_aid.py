"""Reviewed first-aid text and a fail-closed output guard; no prescribing/dispatch."""

import re
import unicodedata
from pathlib import Path

from common.config_loader import ConfigError

ROOT = Path(__file__).resolve().parents[1]


def normalize(value):
    value = unicodedata.normalize("NFKC", value)
    return "".join(c for c in value if unicodedata.category(c) != "Cf").casefold()


class UnsafeFirstAid(ValueError):
    pass


def ensure_safe(value, snapshot):
    """Scan keys and values without returning blocked vocabulary to the caller."""
    terms = snapshot["first_aid"]["blocked_terms"]
    blocked = [re.sub(r"\s+", "", normalize(t)) for t in (*terms["categories"], *terms["ingredients"])]
    patterns = [re.compile(unicodedata.normalize("NFKC", p), re.IGNORECASE) for p in terms["dose_patterns"]]

    def visit(item, field=None):
        if isinstance(item, str):
            text = normalize(item)
            if field in {"config_revision", "creation_config_revision", "context_version", "request_key"} and re.fullmatch(
                    r"(?:[0-9a-f]{32,64}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", text):
                return  # Valid opaque identifiers may contain digits followed by hex 'cc'; they are not dose text.
            if field == 'model_version' and re.fullmatch(r'm5-iforest-\d+(?:\.\d+)+\+[a-z0-9._-]+\.\d{8}\.[0-9a-f]{8}',text):
                return
            compact = re.sub(r"\s+", "", text)
            if any(term in compact for term in blocked) or any(p.search(text) or p.search(compact) for p in patterns):
                raise UnsafeFirstAid("Response content is not permitted")
        elif isinstance(item, dict):
            for key, content in item.items():
                visit(key)
                visit(content, key)
        elif isinstance(item, (list, tuple)):
            for content in item:
                visit(content)
    visit(value)


def reviewed_guide(rule_id, snapshot, source_root=ROOT):
    if rule_id not in ("R5", "R8"):
        raise ValueError("First-aid flow requires R5 or R8")
    root = Path(source_root).resolve()
    permitted = (root / "notify/docs").resolve()
    cfg = snapshot["first_aid"]
    actions, withheld = [], []
    for action in cfg["common_first_aid"]:
        if action.get("source_status", "pending") != "official":
            withheld.append({"id": action["id"], "source_status": action.get("source_status", "pending")})
            continue
        path = (root / action["source"]).resolve()
        if not path.is_relative_to(permitted) or not path.is_file():
            raise ConfigError("First-aid source is not an available reviewed document")
        # Markdown emphasis is presentation, not a change in the quoted source text.
        flatten = lambda s: re.sub(r"\s+", " ", normalize(s).replace("**", "")).strip()
        if flatten(action["text"]) not in flatten(path.read_text(encoding="utf-8")):
            raise ConfigError("First-aid text is not present in its source document")
        actions.append(dict(action))
    if not actions:
        raise ConfigError("No reviewed first-aid actions are available")
    guide = {"mode": "first_aid_required", "rule_id": rule_id, "suspected": True,
             "actions": actions, "withheld_actions": withheld,
             "message": cfg["diagnosis_request"]["disease" if rule_id == "R5" else "parasite"],
             "config_revision": snapshot.revision, "dispatch_status": "pending"}
    ensure_safe(guide, snapshot)
    return guide

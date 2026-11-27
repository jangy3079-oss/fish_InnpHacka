"""Pure six-hour decision over persisted history supplied by a future storage caller."""

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, Field

from common.config_loader import ConfigLoader
from .models import Contract, Level, NotificationContext


class AcceptedNotification(Contract):
    recipient_id: UUID
    cage_id: Annotated[int, Field(strict=True, gt=0)]
    level: Level
    accepted_at: AwareDatetime


class DedupDecision(Contract):
    allowed: bool
    reason: str
    next_allowed_at: AwareDatetime | None
    config_revision: str


def should_notify(context, recipient_id, *, now: datetime, history=(), loader=None):
    context = NotificationContext.model_validate(context.model_dump() if isinstance(context, NotificationContext) else context)
    recipient_id = UUID(str(recipient_id))
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("an aware server timestamp is required")
    now = now.astimezone(timezone.utc)
    snapshot = (loader or ConfigLoader()).snapshot()
    rows = [AcceptedNotification.model_validate(row.model_dump() if isinstance(row, AcceptedNotification) else row)
            for row in history]
    if context.baseline_status == "warming_up":
        return DedupDecision(allowed=False, reason="warming_up", next_allowed_at=None, config_revision=snapshot.revision)
    matches = [row.accepted_at for row in rows if row.recipient_id == recipient_id
               and row.cage_id == context.cage_id and row.level == context.level]
    if matches:
        last = max(matches)
        if last > now:
            raise ValueError("persisted notification timestamp is ahead of the server clock")
        next_at = last + timedelta(hours=snapshot["thresholds"]["alert_dedup_hours"])
        if now < next_at:
            return DedupDecision(allowed=False, reason="same_level_interval", next_allowed_at=next_at,
                                 config_revision=snapshot.revision)
    return DedupDecision(allowed=True, reason="eligible", next_allowed_at=None, config_revision=snapshot.revision)

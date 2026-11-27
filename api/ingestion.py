"""Authenticated, cage-scoped, atomic bulk insert through Supabase PostgREST."""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException

from common import MetricRecord
from .schemas import IngestReceipt

MAX_BATCH_SIZE = 1000
MAX_CAGE_ID = 9223372036854775807


def failure(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


@dataclass(frozen=True)
class IngestSettings:
    url: str
    publishable_key: str = field(repr=False)
    cage_id_map: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_env(cls):
        url = os.getenv("SUPABASE_URL", "").rstrip("/")
        key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
        parsed = urlsplit(url)
        if (not key or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path
                or parsed.scheme not in ("https", "http")
                or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))):
            raise failure(503, "ingest_not_configured", "Supabase ingestion configuration is unavailable")
        # Use a publishable/legacy anon key, never a secret/service-role key.
        if key.startswith("sb_secret_"):
            raise failure(503, "ingest_not_configured", "A Supabase publishable key is required")
        try:
            mapping = json.loads(os.getenv("INGEST_CAGE_ID_MAP", "{}"))
            if not isinstance(mapping, dict) or any(
                not name or type(value) is not int or not 0 < value <= MAX_CAGE_ID
                for name, value in mapping.items()
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise failure(503, "ingest_not_configured", "Cage ID mapping configuration is invalid") from None
        return cls(url, key, mapping)


class SupabaseMetricStore:
    def __init__(self, settings: IngestSettings, client: httpx.Client):
        self.settings, self.client = settings, client

    def _request(self, method: str, path: str, token: str, **kwargs) -> httpx.Response:
        try:
            return self.client.request(method, self.settings.url + path,
                headers={"apikey": self.settings.publishable_key,
                         "Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}, **kwargs)
        except httpx.RequestError:
            raise failure(503, "upstream_unavailable", "Supabase request failed; check persistence before retrying") from None

    def _authorized_cages(self, token: str) -> set[int]:
        response = self._request("GET", "/auth/v1/user", token)
        if response.status_code in (401, 403):
            raise failure(401, "invalid_token", "A valid Supabase user access token is required")
        if response.status_code != 200:
            raise failure(503, "auth_unavailable", "Supabase Auth is unavailable")
        try:
            user = response.json()
            UUID(user["id"])
            metadata = user["app_metadata"]
            cage_ids = metadata["ingest_cage_ids"]
            if (metadata.get("role") != "edge" or user.get("is_anonymous") is True
                    or not isinstance(cage_ids, list) or not cage_ids
                    or any(type(i) is not int or not 0 < i <= MAX_CAGE_ID for i in cage_ids)):
                raise ValueError
            return set(cage_ids)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise failure(403, "ingest_forbidden", "A dedicated edge account with assigned cages is required") from None

    def insert(self, records: list[MetricRecord], token: str) -> IngestReceipt:
        allowed = self._authorized_cages(token)
        rows, identities = [], set()
        for record in records:
            cage_id = self.settings.cage_id_map.get(record.cage_id)
            if cage_id is None and re.fullmatch(r"[1-9][0-9]*", record.cage_id):
                # Avoid converting an unbounded user-supplied integer string.
                if len(record.cage_id) <= 19:
                    cage_id = int(record.cage_id)
            if cage_id is None or not 0 < cage_id <= MAX_CAGE_ID:
                raise failure(422, "unknown_cage_id", "cage_id must be a positive DB ID string or a configured producer ID")
            if cage_id not in allowed:
                raise failure(403, "cage_forbidden", "The edge account cannot ingest this cage")
            identity = (cage_id, record.ts, record.source, record.metric)
            if identity in identities:
                raise failure(422, "duplicate_in_batch", "Duplicate metric identity within the batch")
            identities.add(identity)
            rows.append({**record.model_dump(mode="json"), "cage_id": cage_id})
        # One narrowly authorized RPC transaction, never a read-before-write API race.
        response = self._request("POST", "/rest/v1/rpc/ingest_metrics", token,
            json={"p_records": rows})
        if response.status_code == 200:
            try:
                payload = response.json()
                if payload.get('persisted') is not True or payload.get('received_count') != len(rows):
                    raise ValueError
                return IngestReceipt.model_validate(payload)
            except (ValueError, AttributeError, TypeError):
                raise failure(503, "storage_unavailable", "Invalid storage acknowledgement; retry only the original batch") from None
        try:
            code = response.json().get("code")
        except (ValueError, AttributeError):
            code = None
        if code == "23505":
            raise failure(409, "metric_conflict", "Metric identity has different content; the entire batch was rejected")
        if response.status_code == 401:
            raise failure(401, "invalid_token", "The access token is invalid or expired")
        if response.status_code == 403 or code == "42501":
            raise failure(403, "storage_forbidden", "Database RLS denied this batch; refresh the token and check cage grants")
        if code in ("23503", "23514", "23502", "22P02", "22003", "22023", "22007", "22008"):
            raise failure(422, "storage_validation_failed", "The entire batch failed database validation")
        raise failure(503, "storage_unavailable", "Supabase storage is unavailable; check persistence before retrying")


def get_metric_store() -> Iterator[SupabaseMetricStore]:
    settings = IngestSettings.from_env()
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        yield SupabaseMetricStore(settings, client)

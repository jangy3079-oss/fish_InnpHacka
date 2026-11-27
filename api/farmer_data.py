"""Farmer data gateway: validate user with Auth and forward their token to RLS."""

import os
from dataclasses import dataclass, field
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from .schemas import StockReceipt, StockRequest, Timeseries, WorkLogReceipt, WorkLogRequest


def data_error(status: int, code: str, message: str):
    return HTTPException(status, detail={"code": code, "message": message})


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    key: str = field(repr=False)

    @classmethod
    def from_env(cls):
        url, key = os.getenv("SUPABASE_URL", "").rstrip("/"), os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
        try:
            parsed = urlsplit(url)
            if (not key or key.startswith("sb_secret_") or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment or parsed.scheme not in ("http", "https")
                    or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))):
                raise ValueError
            parsed.port
        except ValueError:
            raise data_error(503, "data_not_configured", "Supabase data access is not configured") from None
        return cls(url, key)


class FarmerDataStore:
    def __init__(self, settings: SupabaseSettings, token: str, client: httpx.Client):
        self.settings, self.token, self.client = settings, token, client

    def request(self, method: str, path: str, **kwargs):
        try:
            response = self.client.request(method, self.settings.url + path,
                headers={"apikey": self.settings.key, "Authorization": f"Bearer {self.token}"}, **kwargs)
        except (httpx.RequestError, httpx.InvalidURL):
            raise data_error(503, "upstream_unavailable", "Supabase request failed; check persistence before retrying writes") from None
        if response.status_code in (200, 201):
            try:
                return response.json()
            except ValueError:
                raise data_error(503, "invalid_upstream_response", "Invalid Supabase response") from None
        try:
            code = response.json().get("code")
        except (ValueError, AttributeError):
            code = None
        if response.status_code == 401:
            raise data_error(401, "invalid_token", "A valid Supabase user access token is required")
        if response.status_code == 403 or code == "42501":
            raise data_error(403, "data_forbidden", "This operation is not permitted")
        if code in ("40001", "23505"):
            raise data_error(409, "data_conflict", "Data changed or request key conflicts; refresh before retrying")
        if code in ("22023", "23514", "23503", "22P02", "22003"):
            raise data_error(422, "invalid_data", "Database validation rejected the request")
        raise data_error(503, "data_unavailable", "Supabase data access is unavailable")

    def authenticate(self):
        user = self.request("GET", "/auth/v1/user")
        try:
            UUID(user["id"])
            if user.get("is_anonymous") is True:
                raise ValueError
        except (TypeError, KeyError, ValueError, AttributeError):
            raise data_error(401, "invalid_user", "A registered Supabase user is required") from None

    def require_cage(self, cage_id: int):
        rows = self.request("GET", "/rest/v1/cage", params={"select": "id", "id": f"eq.{cage_id}", "limit": 1})
        if rows == []:
            raise data_error(404, "cage_not_found", "Cage not found or not accessible")
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("id") != cage_id:
            raise data_error(503, "invalid_upstream_response", "Invalid cage lookup response")

    def replace_stock(self, cage_id: int, body: StockRequest) -> StockReceipt:
        self.require_cage(cage_id)
        data = self.request("POST", "/rest/v1/rpc/replace_cage_stock", json={
            "p_cage_id": cage_id, "p_stock": [s.model_dump() for s in body.stock], "p_insured": body.insured})
        try:
            receipt = StockReceipt.model_validate(data)
            if receipt.cage_id != cage_id or receipt.received_count != len(body.stock):
                raise ValueError
            return receipt
        except (ValidationError, ValueError):
            raise data_error(503, "invalid_upstream_response", "Invalid stock acknowledgement; check storage before retrying") from None

    def record_work_log(self, cage_id: int, body: WorkLogRequest) -> WorkLogReceipt:
        self.require_cage(cage_id)
        data = self.request("POST", "/rest/v1/rpc/record_work_log", json={
            "p_cage_id": cage_id, "p_kind": body.kind, "p_performed_at": body.performed_at.isoformat(),
            "p_request_key": str(body.request_key)})
        try:
            receipt = WorkLogReceipt.model_validate(data)
            if (receipt.cage_id != cage_id or receipt.kind != body.kind
                    or receipt.performed_at != body.performed_at):
                raise ValueError
            return receipt
        except (ValidationError, ValueError):
            raise data_error(503, "invalid_upstream_response", "Invalid work log acknowledgement; retry with the same request_key") from None

    def timeseries(self, cage_id: int, metric: str, start, end, source, limit: int, before) -> Timeseries:
        self.require_cage(cage_id)
        iso = lambda value: value.isoformat() if value is not None else None
        data = self.request("POST", "/rest/v1/rpc/cage_metric_series", json={
            "p_cage_id": cage_id, "p_metric": metric, "p_from": iso(start), "p_to": iso(end),
            "p_source": source, "p_limit": limit, "p_before": iso(before)})
        try:
            series = Timeseries.model_validate(data)
            if series.cage_id != cage_id or series.metric != metric or len(series.points) > limit:
                raise ValueError
            return series
        except (ValidationError, ValueError):
            raise data_error(503, "invalid_upstream_response", "Invalid timeseries response") from None


farmer_bearer = HTTPBearer(auto_error=False, scheme_name="FarmerAccessToken")


def require_farmer_token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(farmer_bearer)]):
    if credentials is None:
        raise HTTPException(401, detail={"code": "missing_token", "message": "Bearer access token required"},
                            headers={"WWW-Authenticate": "Bearer"})
    return credentials.credentials


def get_farmer_http_client():
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        yield client


def get_farmer_store(token: Annotated[str, Depends(require_farmer_token)],
                     client: Annotated[httpx.Client, Depends(get_farmer_http_client)]):
    store = FarmerDataStore(SupabaseSettings.from_env(), token, client)
    store.authenticate()
    return store

"""Run with: python -m uvicorn api.main:app --reload."""

import json
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, HTTPException, Path as PathParam, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AwareDatetime

from .schemas import (
    CageStatus, ChoiceRequest, ConfigFile, ConfigRequest, DemoResult,
    DiagnosisRequest, Metric, MockReceipt, Recommendation, ReleaseDemand,
    Scenario, StockRequest, StressMap, Timeseries,
)

FIXTURE = Path(__file__).parent / "fixtures/status.json"
Id = Annotated[int, PathParam(gt=0)]


def status_fixture(cage_id: int = 3) -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["cage_id"] = cage_id
    return data


def time_window(
    start: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    end: Annotated[AwareDatetime | None, Query(alias="to")] = None,
) -> tuple:
    if start is not None and end is not None and start > end:
        raise HTTPException(422, detail="from must be before or equal to to")
    return start, end


app = FastAPI(
    title="Rockfish Guard Mock API", version="0.1.0",
    description="SDD 8장 목업 API. 합성 fixture만 반환하며 인증·DB·계산·저장·발송은 수행하지 않습니다. "
                "변경 요청도 persisted=false입니다. 실제 서비스 연결 전 docs/api-contract.md 합의가 필요합니다.",
)
origins = [s.strip() for s in os.getenv("API_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if s.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                   allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type", "Authorization"],
                   expose_headers=["X-Data-Mode"])


@app.middleware("http")
async def mock_header(request, call_next):
    response = await call_next(request)
    response.headers["X-Data-Mode"] = "mock"
    return response


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "mock"}


@app.post("/ingest/metrics", response_model=MockReceipt, tags=["ingest"],
          description="배열 envelope만 확인하는 임시 핸들러. #2 병합 후 MetricRecord로 교체하며 현재 지표 필드 검증·저장은 하지 않습니다.")
def ingest(records: Annotated[list[dict[str, Any]], Body(min_length=1)]) -> MockReceipt:
    if not records or any(not record for record in records):
        raise HTTPException(422, detail="Provide a non-empty array of non-empty metric objects")
    return MockReceipt(action="ingest_metrics", received_count=len(records))


@app.get("/cages/{id}/status", response_model=CageStatus, tags=["cages"],
         responses={200: {"content": {"application/json": {"example": status_fixture()}}}})
def status(id: Id):
    return status_fixture(id)


@app.get("/cages/{id}/timeseries", response_model=Timeseries, tags=["cages"])
def timeseries(id: Id, metric: Metric, window: Annotated[tuple, Depends(time_window)]):
    start, end = window
    values = {"vertical_dist": [0.15, 0.5, 0.35], "activity": 0.65, "cohesion": 0.8,
              "feeding": 0.7, "still_ratio": 0.1, "rubbing_rate": 0.02, "quality": 0.9,
              "temp_surface": 26.4, "temp_mid": 22.1, "do": 6.5}
    status = CageStatus.model_validate(status_fixture(id))
    points = [] if (start and status.ts < start) or (end and status.ts > end) else [{"ts": status.ts, "value": values[metric]}]
    return {"cage_id": id, "metric": metric, "points": points}


@app.get("/cages/{id}/echogram", tags=["cages"], response_class=Response,
         responses={200: {"content": {"image/svg+xml": {"schema": {"type": "string"}}}, "description": "합성 미리보기 SVG"}})
def echogram(id: Id, window: Annotated[tuple, Depends(time_window)]):
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="180" viewBox="0 0 400 180"><rect width="400" height="180" fill="#13243b"/><path d="M40 20V150H380" fill="none" stroke="white"/><rect x="50" y="65" width="320" height="35" fill="#35bfc0"/><text x="50" y="40" fill="white">MOCK echogram</text><text x="200" y="175" fill="white">Time</text><text x="5" y="15" fill="white">Depth</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/cages/{id}/recommendation", response_model=Recommendation, tags=["cages"])
def recommendation(id: Id):
    return {"id": 1, "cage_id": id, "ts": status_fixture()["ts"], "recommended_option": "reduce_feed",
            "options": [{"id": "reduce_feed", "label": "사료 감량 (목업)", "available": True,
                         "estimated_loss_krw": None, "reason": "손익 계산은 #27에서 구현"}]}


@app.post("/recommendations/{id}/choice", response_model=MockReceipt, tags=["recommendations"])
def choice(id: Id, body: ChoiceRequest):
    return MockReceipt(action="record_choice", resource_id=id)


@app.put("/cages/{id}/stock", response_model=MockReceipt, tags=["cages"])
def stock(id: Id, body: StockRequest):
    return MockReceipt(action="update_stock", resource_id=id, received_count=len(body.stock))


@app.get("/areas/stress-map", response_model=StressMap, tags=["areas"])
def stress_map():
    return {"ts": status_fixture()["ts"], "areas": [{"area_id": 1, "name": "시연 해역", "level": "경고", "stress_score": 64, "cage_count": 1}]}


@app.get("/areas/{id}/release-demand", response_model=ReleaseDemand, tags=["areas"])
def release_demand(id: Id):
    return {"area_id": id, "ts": status_fixture()["ts"], "estimated_fish_count": 0, "cage_count": 0}


@app.post("/cages/{id}/diagnosis-request", response_model=MockReceipt, tags=["cages"])
def diagnosis(id: Id, body: DiagnosisRequest):
    return MockReceipt(action="request_diagnosis", resource_id=id)


@app.post("/demo/scenario/{name}", response_model=DemoResult, tags=["demo"],
          description="시나리오명·시간을 반환하지만 파이프라인 재생은 하지 않습니다. 모든 시나리오에서 동일한 status fixture를 반환합니다.")
def demo(name: Scenario, t: Annotated[float, Query(ge=0, allow_inf_nan=False)] = 0):
    return {"scenario": name, "t": t, "status": status_fixture()}


@app.put("/admin/config/{file}", response_model=MockReceipt, tags=["admin"])
def config(file: ConfigFile, body: ConfigRequest):
    return MockReceipt(action="update_config", resource_id=file)

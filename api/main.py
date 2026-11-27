"""Run with: python -m uvicorn api.main:app --reload."""

import json
import os
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Path as PathParam, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AwareDatetime

from .farmer_data import FarmerDataStore, get_farmer_store
from .echogram import EchogramRenderer, get_echogram_renderer

from .schemas import (
    CageStatus, ChoiceRequest, ConfigFile, ConfigRequest, DemoResult,
    DiagnosisRequest, Metric, MockReceipt, Recommendation, ReleaseDemand,
    Scenario, StockRequest, StressMap, Timeseries, StockReceipt,
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
    title="Rockfish Guard API", version="0.3.0",
    description="재고 PUT·시계열·에코그램은 사용자 인증과 가두리 권한을 검사하는 실제 데이터 경로입니다. "
                "나머지 API는 목업입니다. docs/farmer-data-api.md 참조.",
)
origins = [s.strip() for s in os.getenv("API_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if s.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                   allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type", "Authorization"],
                   expose_headers=["X-Data-Mode"])


@app.middleware("http")
async def mock_header(request, call_next):
    response = await call_next(request)
    path = request.url.path.rstrip("/")
    real = path.startswith("/cages/") and path.rsplit("/", 1)[-1] in {"timeseries", "stock", "echogram"}
    response.headers["X-Data-Mode"] = "asset" if real and path.endswith("/echogram") else "database" if real else "mock"
    if real:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = ", ".join(filter(None, [response.headers.get("Vary"), "Authorization"]))
    return response


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "mixed"}


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
def timeseries(id: Id, metric: Metric, window: Annotated[tuple, Depends(time_window)],
               store: Annotated[FarmerDataStore, Depends(get_farmer_store)],
               source: Literal["sonar", "camera", "env"] | None = None,
               limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
               before: AwareDatetime | None = None):
    start, end = window
    return store.timeseries(id, metric, start, end, source, limit, before)


@app.get("/cages/{id}/echogram", tags=["cages"], response_class=Response,
         responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}, "description": "권한 확인한 NPZ 에코그램의 PNG 미리보기"}})
def echogram(id: Id, window: Annotated[tuple, Depends(time_window)],
             store: Annotated[FarmerDataStore, Depends(get_farmer_store)],
             renderer: Annotated[EchogramRenderer, Depends(get_echogram_renderer)]):
    store.require_cage(id)
    return Response(content=renderer.render(id, *window), media_type="image/png")


@app.get("/cages/{id}/recommendation", response_model=Recommendation, tags=["cages"])
def recommendation(id: Id):
    return {"id": 1, "cage_id": id, "ts": status_fixture()["ts"], "recommended_option": "reduce_feed",
            "options": [{"id": "reduce_feed", "label": "사료 감량 (목업)", "available": True,
                         "estimated_loss_krw": None, "reason": "손익 계산은 #27에서 구현"}]}


@app.post("/recommendations/{id}/choice", response_model=MockReceipt, tags=["recommendations"])
def choice(id: Id, body: ChoiceRequest):
    return MockReceipt(action="record_choice", resource_id=id)


@app.put("/cages/{id}/stock", response_model=StockReceipt, tags=["cages"],
         description="소유자만 재고 전체를 원자적으로 대체합니다. 요청에서 생략한 크기 그룹은 제거됩니다.")
def stock(id: Id, body: StockRequest, store: Annotated[FarmerDataStore, Depends(get_farmer_store)]):
    return store.replace_stock(id, body)


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
